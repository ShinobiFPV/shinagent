"""
IMQ2 Photo Tools
capture_image  — take a photo from the C920, save to photos/captures/
show_photo     — display a saved photo full-screen on the kiosk
analyze_photo  — analyze a photo by path using Claude Vision, move to processed/
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

PHOTOS_DIR   = Path("/home/shinobi/imq2/photos")
CAPTURES_DIR = PHOTOS_DIR / "captures"
INCOMING_DIR = PHOTOS_DIR / "incoming"
PROCESSED_DIR= PHOTOS_DIR / "processed"

for d in (CAPTURES_DIR, INCOMING_DIR, PROCESSED_DIR):
    d.mkdir(parents=True, exist_ok=True)


def capture_image(prompt: str = "Describe what you see.") -> str:
    """
    Capture a photo from the C920, save it to photos/captures/ with a
    timestamp filename, analyze it with Claude Vision, and return the analysis.
    """
    import base64
    import datetime
    import time
    import anthropic
    import urllib.request as _req
    import urllib.error as _uerr
    from config.loader import config as _cfg
    from integrations.webcam import webcam

    # Grab frame via webapp snapshot endpoint — retry since first request
    # may trigger webcam startup and return 503 briefly.
    jpeg = None
    port = _cfg.get("webapp.port", 8766)
    for _attempt in range(5):
        try:
            with _req.urlopen(f"http://127.0.0.1:{port}/camera/snapshot", timeout=5) as resp:
                jpeg = resp.read()
            log.info(f"capture_image: grabbed {len(jpeg)} bytes via webapp (attempt {_attempt+1})")
            break
        except _uerr.HTTPError as e:
            if e.code == 503:
                log.info("capture_image: snapshot 503 (webcam starting), retrying...")
                time.sleep(0.8)
            else:
                log.warning(f"capture_image: snapshot HTTP {e.code}")
                break
        except Exception as e:
            log.warning(f"capture_image: snapshot failed ({e})")
            break

    if not jpeg:
        return "[capture_image] No frame available — webcam may still be starting, try again in a moment."

    # Save with timestamp
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = CAPTURES_DIR / f"capture_{ts}.jpg"
    save_path.write_bytes(jpeg)
    log.info(f"capture_image: saved {len(jpeg)} bytes to {save_path}")

    # Also write to /tmp/q2_latest.jpg for backwards compatibility
    Path("/tmp/q2_latest.jpg").write_bytes(jpeg)

    # Analyze with Claude Vision
    try:
        b64 = base64.standard_b64encode(jpeg).decode()
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text",  "text": prompt},
                ],
            }],
        )
        analysis = resp.content[0].text
    except Exception as e:
        analysis = f"(Vision analysis failed: {e})"

    return f"{analysis}\n\n[Saved to {save_path}]"


def show_photo(path: str = "") -> str:
    """
    Display a saved photo full-screen on the kiosk via the face server.
    If no path given, shows the most recent capture.
    """
    from config.loader import config

    if not path:
        captures = sorted(CAPTURES_DIR.glob("*.jpg"))
        if not captures:
            return "[show_photo] No captures found. Take a photo first."
        photo_path = captures[-1]
    else:
        photo_path = Path(path)
        if not photo_path.exists():
            # Try relative to photos dir
            photo_path = PHOTOS_DIR / path
        if not photo_path.exists():
            return f"[show_photo] File not found: {path}"

    port = config.get("face.port", 8765)
    url  = f"http://127.0.0.1:{port}/photo?file={photo_path}"

    import subprocess, shutil
    for browser in ["chromium-browser", "chromium", "google-chrome"]:
        if shutil.which(browser):
            subprocess.Popen(
                [browser, f"--app={url}", "--start-fullscreen",
                 "--noerrdialogs", "--disable-infobars", "--no-first-run"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return f"Displaying {photo_path.name} on the kiosk."

    return f"[show_photo] No browser found. Open {url} manually."


def analyze_photo(path: str = "", prompt: str = "Describe this image in detail.") -> str:
    """
    Analyze a photo by path using Claude Vision.
    If no path given, lists photos in the incoming folder.
    After analysis, moves the file to photos/processed/.
    """
    import base64
    import anthropic

    if not path:
        incoming = list(INCOMING_DIR.glob("*.[jJ][pP][gG]")) + \
                   list(INCOMING_DIR.glob("*.[pP][nN][gG]")) + \
                   list(INCOMING_DIR.glob("*.[jJ][pP][eE][gG]"))
        if not incoming:
            return f"[analyze_photo] No photos in {INCOMING_DIR}. Drop a photo there and try again."
        names = [f.name for f in sorted(incoming)]
        return f"Photos waiting in incoming: {', '.join(names)}. Call analyze_photo with a specific filename to analyze one."

    photo_path = Path(path)
    if not photo_path.exists():
        photo_path = INCOMING_DIR / path
    if not photo_path.exists():
        photo_path = PHOTOS_DIR / path
    if not photo_path.exists():
        return f"[analyze_photo] File not found: {path}"

    try:
        img_bytes = photo_path.read_bytes()
        ext = photo_path.suffix.lower()
        media_type = "image/png" if ext == ".png" else "image/jpeg"
        b64 = base64.standard_b64encode(img_bytes).decode()

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text",  "text": prompt},
                ],
            }],
        )
        analysis = resp.content[0].text

        # Move to processed/
        dest = PROCESSED_DIR / photo_path.name
        photo_path.rename(dest)
        log.info(f"analyze_photo: moved {photo_path.name} to processed/")

        return f"{analysis}\n\n[{photo_path.name} moved to processed/]"

    except Exception as e:
        log.error(f"analyze_photo error: {e}", exc_info=True)
        return f"[analyze_photo] Error: {e}"
