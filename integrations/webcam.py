"""
IMQ2 Webcam Integration
Thread-safe frame capture from the Logitech C920 at /dev/video1.
Provides on-demand JPEG snapshots for Claude Vision analysis and
a continuous MJPEG stream for the web app live view.

The capture thread runs continuously once started, keeping the latest
frame in memory so snapshot requests are instant rather than waiting
for a new frame to arrive from the sensor.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default device — C920 is always /dev/video1 on your-pi
def _find_c920_device() -> int:
    """Find C920 video device index by scanning v4l2 — survives USB port changes."""
    import subprocess
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True, text=True, timeout=3
        )
        found_c920 = False
        for line in result.stdout.splitlines():
            if "C920" in line or "Pro Webcam" in line:
                found_c920 = True
            elif found_c920 and "/dev/video" in line.strip():
                idx = int(line.strip().replace("/dev/video", ""))
                return idx
    except Exception:
        pass
    return 0  # fallback

DEFAULT_DEVICE = _find_c920_device()
DEFAULT_WIDTH    = 1920
DEFAULT_HEIGHT   = 1080
SNAPSHOT_WIDTH   = 720    # portrait after rotation
SNAPSHOT_HEIGHT  = 1280
STREAM_WIDTH     = 360    # MJPEG stream — portrait after rotation
STREAM_HEIGHT    = 640
STREAM_FPS       = 15


class WebcamCapture:
    """
    Singleton webcam manager. Starts a background capture thread on first
    use and keeps a rolling latest frame available for snapshots and streaming.
    """

    _instance: Optional["WebcamCapture"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self._cap         = None
        self._frame       = None          # latest raw frame (numpy BGR)
        self._frame_lock  = threading.Lock()
        self._running     = False
        self._thread: Optional[threading.Thread] = None
        self._device      = DEFAULT_DEVICE

    def start(self, device: int = DEFAULT_DEVICE) -> bool:
        """Open the camera and start the background capture thread."""
        # If already running but cap got closed, restart cleanly
        if self._running and (self._cap is None or not self._cap.isOpened()):
            self._running = False
            import time; time.sleep(0.3)
        if self._running:
            return True

        import cv2
        self._device = device
        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            log.error(f"Webcam: could not open /dev/video{device}")
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  DEFAULT_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, DEFAULT_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, 30)
        self._cap     = cap
        self._running = True
        self._thread  = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        log.info(f"Webcam: capture started on /dev/video{device} "
                 f"({DEFAULT_WIDTH}x{DEFAULT_HEIGHT})")
        return True

    def _capture_loop(self):
        import cv2
        try:
            while self._running:
                try:
                    ok, frame = self._cap.read()
                    if ok:
                        # Rotate once in the capture thread so grab_jpeg/stream are cheap
                        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
                        with self._frame_lock:
                            self._frame = frame
                    else:
                        log.warning("Webcam: frame read failed — retrying in 1s")
                        time.sleep(1)
                except Exception as e:
                    # A driver-level exception (e.g. camera physically
                    # unplugged) must not silently kill this thread forever —
                    # log and keep trying rather than going dark with no signal.
                    log.warning(f"Webcam capture loop error: {e}")
                    time.sleep(1)
        finally:
            # However the loop exits, clear _running so a future start() call
            # isn't permanently blocked by its "already running" guard.
            self._running = False

    def stop(self):
        self._running = False
        if self._cap:
            self._cap.release()
            self._cap = None

    def grab_jpeg(self, width: int = SNAPSHOT_WIDTH,
                  height: int = SNAPSHOT_HEIGHT,
                  quality: int = 88) -> Optional[bytes]:
        """
        Return a JPEG-encoded snapshot at the requested resolution.
        Returns None if no frame is available yet.
        """
        import cv2
        import numpy as np

        with self._frame_lock:
            frame = self._frame

        if frame is None:
            return None

        if (width, height) != (frame.shape[1], frame.shape[0]):
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return bytes(buf) if ok else None

    def grab_frame_for_vision(self) -> Optional[bytes]:
        """
        Grab a snapshot sized for Claude Vision — 1280x720 JPEG.
        Returns raw bytes suitable for base64 encoding and sending to the API.
        """
        return self.grab_jpeg(SNAPSHOT_WIDTH, SNAPSHOT_HEIGHT, quality=90)

    def stream_generator(self):
        """
        Generator yielding MJPEG frames for the /camera/stream endpoint.
        Each chunk is a multipart/x-mixed-replace boundary + JPEG bytes.
        """
        import cv2
        import time

        interval = 1.0 / STREAM_FPS
        while True:
            t_start = time.time()
            jpeg = self.grab_jpeg(STREAM_WIDTH, STREAM_HEIGHT, quality=72)
            if jpeg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                )
            elapsed = time.time() - t_start
            time.sleep(max(0, interval - elapsed))

    @property
    def is_running(self) -> bool:
        return self._running


# Module-level singleton
webcam = WebcamCapture()
