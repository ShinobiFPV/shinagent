"""
IMQ2 Photo Tools
capture_image  — take a photo from the C920, save to photos/captures/
show_photo     — display a saved photo full-screen on the kiosk
analyze_photo  — analyze a photo by path using the active LLM backend, move to processed/
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

PHOTOS_DIR   = Path("/home/your-pi/imq2/photos")
CAPTURES_DIR = PHOTOS_DIR / "captures"
INCOMING_DIR = PHOTOS_DIR / "incoming"
PROCESSED_DIR= PHOTOS_DIR / "processed"

for d in (CAPTURES_DIR, INCOMING_DIR, PROCESSED_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _resolve_within_photos_dir(path: str, *fallback_dirs: Path) -> "Path | None":
    """
    Resolve a caller-supplied (LLM-controlled) path against PHOTOS_DIR and
    its fallback subdirectories, refusing anything that resolves outside
    PHOTOS_DIR. Without this, a hallucinated or injected absolute path
    (e.g. a secrets/.env file that happens to exist) would be read and,
    for analyze_photo, unconditionally moved out of its original location.
    Returns None if the resolved path escapes PHOTOS_DIR; does not check
    existence otherwise (callers do that separately for a clearer error).
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        for base in (*fallback_dirs, PHOTOS_DIR):
            maybe = base / path
            if maybe.exists():
                candidate = maybe
                break
        else:
            candidate = fallback_dirs[0] / path if fallback_dirs else PHOTOS_DIR / path

    try:
        resolved = candidate.resolve()
        photos_root = PHOTOS_DIR.resolve()
    except Exception:
        return None
    if resolved != photos_root and photos_root not in resolved.parents:
        return None
    return resolved


def capture_image(prompt: str = "Describe what you see.") -> str:
    """
    Capture a photo from the C920, save it to photos/captures/ with a
    timestamp filename, analyze it with the active LLM backend, and return
    the analysis.
    """
    import datetime
    import time
    import urllib.request as _req
    import urllib.error as _uerr
    from config.loader import config as _cfg
    from core.llm import get_llm_backend, build_vision_message
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

    # Analyze with whichever LLM backend is currently active
    try:
        llm = get_llm_backend()
        response = llm.complete(messages=[build_vision_message(jpeg, "image/jpeg", prompt)])
        analysis = response.text
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
        resolved = _resolve_within_photos_dir(path)
        if resolved is None:
            return f"[show_photo] Refusing to access a path outside {PHOTOS_DIR}: {path}"
        photo_path = resolved
        if not photo_path.exists():
            return f"[show_photo] File not found: {path}"

    from urllib.parse import quote

    port = config.get("face.port", 8765)
    url  = f"http://127.0.0.1:{port}/photo?file={quote(str(photo_path))}"

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
    Analyze a photo by path using the active LLM backend.
    If no path given, lists photos in the incoming folder.
    After analysis, moves the file to photos/processed/.
    """
    from core.llm import get_llm_backend, build_vision_message

    if not path:
        incoming = list(INCOMING_DIR.glob("*.[jJ][pP][gG]")) + \
                   list(INCOMING_DIR.glob("*.[pP][nN][gG]")) + \
                   list(INCOMING_DIR.glob("*.[jJ][pP][eE][gG]"))
        if not incoming:
            return f"[analyze_photo] No photos in {INCOMING_DIR}. Drop a photo there and try again."
        names = [f.name for f in sorted(incoming)]
        return f"Photos waiting in incoming: {', '.join(names)}. Call analyze_photo with a specific filename to analyze one."

    photo_path = _resolve_within_photos_dir(path, INCOMING_DIR)
    if photo_path is None:
        return f"[analyze_photo] Refusing to access a path outside {PHOTOS_DIR}: {path}"
    if not photo_path.exists():
        return f"[analyze_photo] File not found: {path}"

    try:
        img_bytes = photo_path.read_bytes()
        ext = photo_path.suffix.lower()
        media_type = "image/png" if ext == ".png" else "image/jpeg"

        llm = get_llm_backend()
        response = llm.complete(messages=[build_vision_message(img_bytes, media_type, prompt)])
        analysis = response.text

        # Move to processed/
        dest = PROCESSED_DIR / photo_path.name
        photo_path.rename(dest)
        log.info(f"analyze_photo: moved {photo_path.name} to processed/")

        return f"{analysis}\n\n[{photo_path.name} moved to processed/]"

    except Exception as e:
        log.error(f"analyze_photo error: {e}", exc_info=True)
        return f"[analyze_photo] Error: {e}"
