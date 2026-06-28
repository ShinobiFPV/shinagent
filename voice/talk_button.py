"""
IMQ2 Talk Button Listener
Push-to-talk toggle via a single configurable key, fed by ANY device that
emits standard keyboard events — a real keyboard, an 8BitDo Zero 2 in
Bluetooth keyboard mode, or a Flipper Zero running its BLE keyboard app.

IMPLEMENTATION NOTE — why evdev instead of pynput:
The original version of this module used pynput's global keyboard hook.
That works fine for a real physical keyboard, but on shinobi (a Wayland
session) it silently failed to receive any events from the 8BitDo at all —
keys still landed in whatever terminal had focus (proving the device itself
was sending real keypresses), but pynput's listener callback never fired.
This is a known pynput limitation: under Wayland, its X11-based hook only
sees events delivered to applications running through XWayland, not true
global system input, and X11 itself can also simply ignore HID devices it
doesn't consider "keyboard-like enough" — which a gamepad-emulating-a-
keyboard often isn't.

The fix: read the device directly at the kernel level via evdev
(/dev/input/eventN), which bypasses the windowing layer entirely. This is
confirmed working on shinobi — `evdev.list_devices()` correctly shows
"8BitDo Zero 2 gamepad Keyboard" as its own real input device.
"""

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)


class TalkButtonState:
    """Thread-safe toggle state, set by the background key listener,
    consumed by the voice loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._toggle_pending = False

    def signal_toggle(self):
        with self._lock:
            self._toggle_pending = True

    def consume_toggle(self) -> bool:
        """Returns True exactly once per actual toggle event, then clears it."""
        with self._lock:
            if self._toggle_pending:
                self._toggle_pending = False
                return True
            return False


# Module-level singleton — imported by both the listener and the voice loop
talk_button_state = TalkButtonState()


def _find_device_with_key(target_key_code: int):
    """
    Search all input devices for one that reports the target key code as
    part of its capabilities — i.e. a device that's actually capable of
    sending that key, rather than guessing a device name or hardcoded
    /dev/input/eventN path (which can shift across reboots as other USB/BT
    devices connect in different orders).
    """
    import evdev

    candidates = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            capabilities = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
            if target_key_code in capabilities:
                candidates.append(dev)
            else:
                dev.close()
        except Exception:
            continue

    return candidates


class TalkButtonListener:
    """
    Listens for a configured key directly at the kernel level via evdev,
    independent of the windowing system (X11/Wayland) entirely. Debounces
    key-repeat (evdev sends repeated "hold" events while a key stays down)
    so holding the button doesn't fire dozens of toggles per second.
    """

    def __init__(self, key_name: str = "g"):
        self._key_name = key_name.lower()
        self._device = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _resolve_key_code(self) -> int:
        import evdev
        attr_name = f"KEY_{self._key_name.upper()}"
        key_code = getattr(evdev.ecodes, attr_name, None)
        if key_code is None:
            raise ValueError(f"Unrecognized key name for evdev: '{self._key_name}' (expected e.g. 'g', 'a', 'space')")
        return key_code

    def start(self, device_name: str = ""):
        """
        Start the listener. device_name should be the exact evdev device name
        to listen on (e.g. '8BitDo Zero 2 gamepad Keyboard'). Runs a background
        thread that automatically reconnects when the controller sleeps/disconnects.
        """
        self._device_name = device_name
        self._key_code    = self._resolve_key_code()
        self._running     = True
        self._thread      = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._thread.start()

    def _open_device(self) -> bool:
        """Try to find and open the configured device. Returns True on success."""
        import evdev

        candidates = _find_device_with_key(self._key_code)
        if not candidates:
            return False

        device_name = getattr(self, '_device_name', '')
        if device_name:
            preferred = next((d for d in candidates if d.name == device_name), None)
        else:
            preferred = next(
                (d for d in candidates if "8bitdo" in d.name.lower() or "flipper" in d.name.lower()),
                None,
            )

        if preferred is None:
            for d in candidates:
                d.close()
            return False

        for d in candidates:
            if d is not preferred:
                d.close()

        self._device = preferred
        log.info(f"Talk button connected — key: '{self._key_name}' on '{preferred.name}'")
        return True

    def _reconnect_loop(self):
        """
        Outer loop: open device → read events → if device drops, wait and retry.
        This means the controller can sleep, wake, reconnect to the Pi and Q2
        will automatically pick it back up without needing a restart.
        """
        import time

        first_attempt = True
        while self._running:
            if not self._open_device():
                if first_attempt:
                    log.warning(
                        f"Talk button: no device found for key '{self._key_name}' "
                        f"(name='{getattr(self, '_device_name', '')}') — will retry every 5s."
                    )
                first_attempt = False
                time.sleep(5)
                continue

            first_attempt = True
            self._read_loop()  # blocks until device disconnects

            if self._running:
                log.info("Talk button disconnected — will reconnect in 5s...")
                time.sleep(5)

    def _read_loop(self):
        import evdev

        try:
            for event in self._device.read_loop():
                if not self._running:
                    break
                if event.type == evdev.ecodes.EV_KEY and event.code == self._key_code:
                    # value: 0=release, 1=press, 2=hold/auto-repeat
                    if event.value == 1:
                        talk_button_state.signal_toggle()
                        log.debug(f"Talk button toggled (key: {self._key_name})")
        except OSError:
            log.warning(f"Talk button device '{self._device.name}' disconnected.")

    def stop(self):
        self._running = False
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass


def start_talk_button_listener(key_name: str = "g", device_name: str = "") -> Optional[TalkButtonListener]:
    """
    Start the listener in a background thread. Returns the listener immediately —
    it will keep retrying every 5s until the controller connects, then reconnect
    automatically whenever it sleeps or disconnects.
    device_name: exact evdev device name (e.g. '8BitDo Zero 2 gamepad Keyboard').
    """
    try:
        listener = TalkButtonListener(key_name=key_name)
        listener.start(device_name=device_name)
        return listener
    except Exception as e:
        log.warning(f"Talk button listener failed to start ({e}) — falling back to Enter key push-to-talk.")
        return None

