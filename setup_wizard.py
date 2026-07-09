#!/usr/bin/env python3
"""
ShinAgent Setup Wizard — standalone Flask app.

Runs BEFORE the main requirements.txt is installed, so this file imports
nothing from the ShinAgent codebase itself and only depends on flask,
requests, and pyyaml (installed by setup.sh before this runs). psutil is
soft-imported where used (RAM detection) since it's a HUD-only dependency,
not part of the main requirements.txt, and may genuinely not be present at
wizard-run-time. Safe to run multiple times — re-detects current state and
lets you review/change it rather than assuming a blank slate.

===========================================================================
AUDIT OF THE PREVIOUS WIZARD (this file, before this rewrite)
===========================================================================
Findings from a full read of the prior version before rewriting it:

1. Steps that existed (6, not 8): Welcome/System Check, LLM Backend,
   Voice, Google Services (optional), Sim Racing/Flight (optional),
   Review & Finish. No dedicated Elite Dangerous step (folded nowhere —
   ED wasn't mentioned at all) and no ShinAgent HUD step.

2. Missing entirely: Elite Dangerous / Ship Computer setup (no ED
   toggle, no INARA key field, no ed_bridge.py instructions, no
   companion-panel URL) and ShinAgent HUD setup (no pywebview/HUD
   dependency check, no launch command, no retro gaming mention). The
   Vernacular Generator and Pop-Up Video are Settings-panel features, not
   first-run setup concerns, so their absence here is correct, not a gap.

3. API key fields: contrary to what might be assumed, GEMINI_API_KEY and
   ZAI_API_KEY (GLM) were both ALREADY present in ENV_KEYS and already had
   full backend cards in Step 2, including a genuinely good multi-step
   Gemini guide with free-tier details and a model selector. Grok and
   GLM key fields existed but had no live TEST endpoint (the `noTest`
   flag) — not a bug, since testing an untested-tier key risks
   surprising the user with a real billed call.

4. The Gemini step-by-step guide: present and reasonably thorough
   (3 steps, free-tier numbers, "no credit card" reassurance). Kept and
   extended in this rewrite rather than replaced.

5. config.yaml handling (`_update_config`): wrote llm.backend,
   llm.gemini.model, voice.input_device/output_device/deepgram_tts.model/
   wake_word.enabled, agent.name/call_name/active_profile, face.style,
   and integrations.forza_telemetry/ac_telemetry/msfs_telemetry.enabled.
   Missing: integrations.ed_telemetry.enabled, ed.inara_enabled — Elite
   Dangerous had no config-writing path at all, matching finding #2.

6. Directory creation (`_ensure_directories`): created logs,
   logs/conversations, photos/{captures,incoming,processed}, memory/db,
   credentials, wake_words, cache/ufc. Missing cache/ed (used by
   integrations/ed_inara.py and ed_edsm.py) and cache/popups (used by
   integrations/popup_video.py for Pop-Up Video sessions) — both features
   self-create their cache dir on first write regardless, so this wasn't
   a functional bug, just an incomplete "everything's ready on first run"
   guarantee.

   Separately (not a wizard bug, but discovered while checking this):
   integrations/ed_inara.py and ed_edsm.py both hardcoded
   `CACHE_DIR = Path.home() / "imq2" / "cache" / "ed"` instead of the
   repo-relative pattern every other cache dir in this codebase uses
   (ufc_data.py, popup_video.py both use `Path(__file__).resolve()
   .parent.parent / "cache" / ...`) — meaning ED's disk cache silently
   pointed at the wrong directory on any machine where the repo isn't
   cloned to exactly `~/imq2` (this dev machine included). Fixed in both
   files as part of this task, since it directly determines where this
   wizard's directory creation needs to point.

7. requirements.txt install: worked correctly (SSE-streamed `pip install
   -r requirements.txt`, kept in this rewrite basically unchanged). No
   equivalent existed for installing missing *system* packages
   (portaudio/ffmpeg/chromium/tmux) — the old Step 1 only warned about
   them with no in-wizard fix, despite Step 1's own checklist showing
   them as WARN. Added in this rewrite.

8. Completion screen: already correctly referenced
   `bash scripts/q2_start.sh` / `q2_stop.sh` / `q2_status.sh` and the web
   app URL — NOT bare `python main.py` as might be assumed for an
   early-written wizard. Missing from the quick-reference card: the
   `tmux attach -t q2` monitor command, the Settings URL, and (new) the
   HUD launch command — all added in this rewrite.

9. Other things found while reading: `/setup/api/cameras` was a defined
   Flask route with real v4l2-ctl detection logic that the JS never
   called anywhere — dead code. No step in the new spec asks for a
   camera-selection UI either, so this route is kept for API parity
   (webcam vision analysis is a real, separate ShinAgent feature) but
   remains intentionally unused by this wizard's own UI, same as before
   — noted here explicitly rather than left as an unexplained dead route.

===========================================================================
"""

import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

try:
    import yaml
except ImportError:
    yaml = None

try:
    import requests
except ImportError:
    requests = None

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
REQUIREMENTS_PATH = BASE_DIR / "requirements.txt"

ENV_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "ZAI_API_KEY", "GEMINI_API_KEY",
    "DEEPGRAM_API_KEY", "PORCUPINE_ACCESS_KEY", "INARA_API_KEY", "TAVILY_API_KEY",
]

REQUIRED_DIRS = (
    "logs", "logs/conversations",
    "photos/captures", "photos/incoming", "photos/processed",
    "credentials", "wake_words",
    "cache", "cache/ufc", "cache/ed", "cache/popups",
    "memory/db",
)

SYSTEM_PACKAGES = ["portaudio19-dev", "ffmpeg", "chromium-browser", "tmux"]

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _can_import(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_pi() -> bool:
    return _get_pi_model() is not None


def _get_ram_gb():
    """psutil if available (soft dependency, not in the main
    requirements.txt — it's a HUD-only package that may not be installed
    yet), else /proc/meminfo on Linux, else None."""
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 ** 2), 1)
    except Exception:
        pass
    return None


def _get_pi_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def _get_disk_free_gb():
    try:
        return round(shutil.disk_usage(str(BASE_DIR)).free / (1024 ** 3), 1)
    except Exception:
        return None


def _which_chromium():
    return shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("chromium.exe")


def _which_browser():
    """Chromium first (Linux kiosk display default), then Chrome/Edge --
    chromium-browser is rarely installed via a package manager on Windows,
    so Chrome or Edge (both ship with every Windows 10/11 install) is the
    realistic kiosk-display browser there."""
    found = _which_chromium()
    if found:
        return found
    if not _is_windows():
        return None
    for exe in ("chrome.exe", "msedge.exe"):
        path = shutil.which(exe)
        if path:
            return path
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(var)
        if not base:
            continue
        for rel in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
            candidate = Path(base) / rel
            if candidate.exists():
                return str(candidate)
    return None


def _get_platform_name() -> str:
    if _is_windows():
        return f"Windows {platform.release()}"
    pi_model = _get_pi_model()
    if pi_model:
        return pi_model
    if _is_linux():
        try:
            info = platform.freedesktop_os_release()
            name = info.get("PRETTY_NAME") or info.get("NAME")
            if name:
                return name
        except Exception:
            pass
        return f"Linux {platform.release()}"
    return platform.system() or "Unknown"


def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _read_env_existing_keys() -> set:
    existing = set()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                existing.add(stripped.split("=", 1)[0].strip())
    return existing


def _write_env(api_keys: dict):
    """Merge-only: append keys that don't already exist in .env, never
    touch/overwrite a key that's already present (even if its value is
    blank) — the wizard is safe to re-run without clobbering manual edits."""
    existing_keys = _read_env_existing_keys()
    new_lines = []
    for key in ENV_KEYS:
        val = (api_keys.get(key) or "").strip()
        if val and key not in existing_keys:
            new_lines.append(f"{key}={val}")
    if not new_lines:
        return
    prefix = ""
    if ENV_PATH.exists():
        existing_text = ENV_PATH.read_text(encoding="utf-8")
        if existing_text and not existing_text.endswith("\n"):
            prefix = "\n"
    else:
        new_lines.insert(0, "# Auto-generated by ShinAgent setup wizard")
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(prefix + "\n".join(new_lines) + "\n")


def _update_config(data: dict):
    if yaml is None:
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}

    voice = cfg.setdefault("voice", {})
    if data.get("input_device"):
        voice["input_device"] = data["input_device"]
    if data.get("output_device"):
        voice["output_device"] = data["output_device"]
    if data.get("tts_voice"):
        voice.setdefault("deepgram_tts", {})["model"] = data["tts_voice"]
    if data.get("wake_word_enabled") is not None:
        voice.setdefault("wake_word", {})["enabled"] = bool(data["wake_word_enabled"])

    llm = cfg.setdefault("llm", {})
    if data.get("llm_backend"):
        llm["backend"] = data["llm_backend"]
    if data.get("gemini_model"):
        llm.setdefault("gemini", {})["model"] = data["gemini_model"]

    agent = cfg.setdefault("agent", {})
    agent["name"] = data.get("agent_name") or "ShinAgent"
    agent.setdefault("call_name", agent["name"])
    agent.setdefault("active_profile", "profiles/default.yaml")

    face = cfg.setdefault("face", {})
    face["style"] = 1

    integrations = cfg.setdefault("integrations", {})
    if data.get("forza_enabled") is not None:
        integrations.setdefault("forza_telemetry", {})["enabled"] = bool(data["forza_enabled"])
    if data.get("ac_enabled") is not None:
        integrations.setdefault("ac_telemetry", {})["enabled"] = bool(data["ac_enabled"])
    if data.get("msfs_enabled") is not None:
        integrations.setdefault("msfs_telemetry", {})["enabled"] = bool(data["msfs_enabled"])
    if data.get("ed_enabled") is not None:
        integrations.setdefault("ed_telemetry", {})["enabled"] = bool(data["ed_enabled"])

    if data.get("ed_enabled") is not None or data.get("inara_enabled") is not None:
        ed = cfg.setdefault("ed", {})
        if data.get("inara_enabled") is not None:
            ed["inara_enabled"] = bool(data["inara_enabled"])

    CONFIG_PATH.write_text(
        yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )


def _ensure_directories():
    for d in REQUIRED_DIRS:
        (BASE_DIR / d).mkdir(parents=True, exist_ok=True)


def _write_personality_state():
    """config/personality_state.yaml doesn't exist until Q2's own
    config/loader.py first runs and saves it — writing a sane default here
    means the very first launch (before any dial edit) has a real file to
    read rather than relying on load_personality_state()'s "not found"
    fallback path, matching what a normal running instance would produce."""
    if yaml is None:
        return
    path = BASE_DIR / "config" / "personality_state.yaml"
    if path.exists():
        return
    import datetime
    state = {
        "active_profile": "profiles/default.yaml",
        "dial_overrides": {},
        "probability_narration": False,
        "wellness_checkins": "off",
        "saved_at": datetime.datetime.now().isoformat(),
    }
    path.write_text(yaml.dump(state, default_flow_style=False, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route("/setup")
def setup_page():
    return Response(WIZARD_HTML, mimetype="text/html")


@app.route("/")
def index_redirect():
    return setup_page()


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------

@app.route("/setup/api/check")
def api_check():
    wake_dir = BASE_DIR / "wake_words"
    checks = {
        "python_version": platform.python_version(),
        "python_ok": sys.version_info >= (3, 11),
        "is_64bit": sys.maxsize > 2**32,
        "venv_active": sys.prefix != sys.base_prefix,
        "pip_available": _can_import("pip") or importlib.util.find_spec("pip") is not None,
        "portaudio": _can_import("sounddevice") or _can_import("pyaudio"),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "browser": _which_browser() is not None,
        "tmux": shutil.which("tmux") is not None,
        "requirements_installed": _can_import("flask") and _can_import("chromadb") and _can_import("anthropic"),
        "env_exists": ENV_PATH.exists(),
        "config_exists": CONFIG_PATH.exists(),
        "google_creds": (BASE_DIR / "credentials" / "credentials.json").exists(),
        "wake_word_file": bool(list(wake_dir.glob("*.ppn"))) if wake_dir.exists() else False,
        "disk_free_gb": _get_disk_free_gb(),
        "ram_gb": _get_ram_gb(),
        "pi_model": _get_pi_model(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "is_windows": _is_windows(),
    }
    return jsonify(checks)


@app.route("/setup/api/platform")
def api_platform():
    return jsonify({
        "is_windows": _is_windows(),
        "is_linux": _is_linux(),
        "is_pi": _is_pi(),
        "platform_name": _get_platform_name(),
        "python_version": platform.python_version(),
        "hostname": platform.node(),
    })


@app.route("/setup/api/install", methods=["POST"])
def api_install():
    def generate():
        if not REQUIREMENTS_PATH.exists():
            yield "data: ERROR: requirements.txt not found\n\n"
            return
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except Exception as e:
            yield f"data: ERROR: {e}\n\n"
            return
        for line in proc.stdout:
            yield f"data: {line.rstrip()}\n\n"
        proc.wait()
        yield "data: DONE\n\n" if proc.returncode == 0 else f"data: ERROR (pip exited {proc.returncode})\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/setup/api/install_system", methods=["POST"])
def api_install_system():
    """Streams `sudo apt-get install -y <missing system packages>` on
    Linux/Debian. On Windows there's no apt-get equivalent for
    ffmpeg/chromium/tmux, but PyAudio is the one of the four that's
    actually pip-installable there, so this streams a real
    `pip install pyaudio` instead of just printing instructions -- falling
    back to the pre-built-wheel note if the source build fails (common on
    Windows without a C compiler)."""
    def generate():
        if _is_windows():
            yield "data: Installing PyAudio via pip (no apt-get on Windows)...\n\n"
            proc = None
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "pip", "install", "pyaudio"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                for line in proc.stdout:
                    yield f"data: {line.rstrip()}\n\n"
                proc.wait()
            except Exception as e:
                yield f"data: ERROR: {e}\n\n"
            if proc is None or proc.returncode != 0:
                yield "data: PyAudio failed to install from source.\n\n"
                yield "data: Download a pre-built wheel instead:\n\n"
                yield "data: https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio\n\n"
                yield "data: Then run: pip install PyAudio-0.2.14-cpXX-cpXX-win_amd64.whl\n\n"
            yield "data: DONE\n\n"
            return
        if shutil.which("apt-get") is None:
            yield "data: No apt-get available on this platform -- install these manually:\n\n"
            yield f"data: {' '.join(SYSTEM_PACKAGES)}\n\n"
            yield "data: DONE\n\n"
            return
        try:
            proc = subprocess.Popen(
                ["sudo", "apt-get", "install", "-y"] + SYSTEM_PACKAGES,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except Exception as e:
            yield f"data: ERROR: {e}\n\n"
            return
        for line in proc.stdout:
            yield f"data: {line.rstrip()}\n\n"
        proc.wait()
        yield "data: DONE\n\n" if proc.returncode == 0 else f"data: ERROR (apt-get exited {proc.returncode})\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/setup/api/audio")
def api_audio():
    input_devices, output_devices = [], []
    try:
        import sounddevice as sd
        for idx, d in enumerate(sd.query_devices()):
            rate = int(d.get("default_samplerate") or 48000)
            if d.get("max_input_channels", 0) > 0:
                input_devices.append({
                    "index": idx, "name": d.get("name", "?"),
                    "channels": d.get("max_input_channels", 1),
                    "sample_rates": [rate],
                })
            if d.get("max_output_channels", 0) > 0:
                output_devices.append({
                    "index": idx, "name": d.get("name", "?"),
                    "channels": d.get("max_output_channels", 2),
                })
        return jsonify({"input_devices": input_devices, "output_devices": output_devices})
    except Exception:
        if _is_windows():
            return jsonify({
                "input_devices": [], "output_devices": [],
                "error": "sounddevice not installed. Run: pip install sounddevice",
            })

    # Fallback: parse `arecord -l` for input devices only (no portable
    # equivalent for output enumeration without sounddevice/pyaudio).
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5).stdout
        for m in re.finditer(r"card (\d+): ([^\[]+)\[([^\]]*)\], device (\d+): ([^\[]+)\[([^\]]*)\]", out):
            input_devices.append({
                "index": int(m.group(1)), "name": (m.group(3) or m.group(2)).strip(),
                "channels": 1, "sample_rates": [48000],
            })
    except Exception:
        pass
    return jsonify({"input_devices": input_devices, "output_devices": output_devices})


@app.route("/setup/api/cameras")
def api_cameras():
    """Not called by this wizard's own UI (no step asks for camera
    selection) -- kept for API parity since webcam vision analysis is a
    real, separate ShinAgent feature. See the audit note at the top of
    this file."""
    cameras = []
    try:
        out = subprocess.run(
            ["v4l2-ctl", "--list-devices"], capture_output=True, text=True, timeout=5
        ).stdout
        current_name = None
        for line in out.splitlines():
            if line and not line.startswith((" ", "\t")):
                current_name = line.split("(")[0].strip()
            elif line.strip().startswith("/dev/video"):
                cameras.append({"name": current_name or "Unknown", "path": line.strip()})
    except Exception:
        pass
    return jsonify({"cameras": cameras})


@app.route("/setup/api/test", methods=["POST"])
def api_test():
    data = request.get_json(silent=True) or {}
    backend = data.get("backend", "")
    key = (data.get("key") or "").strip()

    if not key:
        return jsonify({backend: "invalid", "message": "No key provided"})
    if requests is None:
        return jsonify({backend: "not_tested", "message": "requests not installed"})

    # Gemini gets its own branch with richer status-code handling (below) --
    # it's ShinAgent's recommended free default, so a wrong/expired/rate
    # -limited key should say exactly which of those it is rather than a
    # flat ok/invalid, since that's the backend a first-time user is most
    # likely testing.
    if backend == "gemini":
        try:
            r = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gemini-2.5-flash",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 5,
                },
                timeout=5,
            )
        except Exception as e:
            return jsonify({"gemini": "error", "error": str(e)})
        if r.status_code == 200:
            return jsonify({"gemini": "ok", "model": "gemini-2.5-flash"})
        if r.status_code in (401, 403):
            return jsonify({"gemini": "invalid", "error": "Key rejected"})
        if r.status_code == 429:
            return jsonify({"gemini": "ok", "note": "Rate limited but key is valid"})
        return jsonify({"gemini": "error", "error": f"HTTP {r.status_code}"})

    try:
        if backend == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            ok = r.status_code == 200
        elif backend == "deepgram":
            r = requests.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {key}"}, timeout=10,
            )
            ok = r.status_code == 200
        elif backend == "openai":
            r = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"}, timeout=10,
            )
            ok = r.status_code == 200
        else:
            return jsonify({backend: "not_tested"})
        return jsonify({backend: "ok" if ok else "invalid"})
    except Exception as e:
        return jsonify({backend: "invalid", "message": str(e)})


@app.route("/setup/api/test/mic", methods=["POST"])
def api_test_mic():
    data = request.get_json(silent=True) or {}
    device_index = data.get("device_index")
    try:
        import sounddevice as sd
        fs = 16000
        duration_s = 2
        rec = sd.rec(int(duration_s * fs), samplerate=fs, channels=1, dtype="int16", device=device_index)
        sd.wait()
        sd.play(rec, fs, device=data.get("output_index"))
        sd.wait()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/setup/api/tts_preview", methods=["POST"])
def api_tts_preview():
    """Not in the task's own Flask-routes list, but required by Step 3's
    [PREVIEW] button (sends sample text to Deepgram TTS, plays back in
    browser) -- a genuinely necessary route that list omitted."""
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    voice = data.get("voice") or "aura-2-zeus-en"
    if not key:
        return jsonify({"ok": False, "error": "No Deepgram key provided"}), 400
    if requests is None:
        return jsonify({"ok": False, "error": "requests not installed"}), 500
    try:
        r = requests.post(
            f"https://api.deepgram.com/v1/speak?model={voice}",
            headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
            json={"text": "ShinAgent is ready."},
            timeout=15,
        )
        if r.status_code != 200:
            return jsonify({"ok": False, "error": f"HTTP {r.status_code}"}), 400
        return Response(r.content, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/setup/api/detect_controller")
def api_detect_controller():
    """
    8BitDo Zero 2 gamepad-mode detection for the Voice step's Controller
    subsection. Self-contained (this wizard imports nothing from the main
    ShinAgent codebase, per the module docstring) rather than importing
    voice/controller.py's ControllerManager -- duplicates its minimal
    name-matching logic instead. Linux/Pi only, same as the real thing.
    """
    if _is_windows():
        return jsonify({"found": False, "platform": "windows"})
    try:
        import evdev
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                name = dev.name
                dev.close()
                if "8bitdo" in name.lower() and "zero" in name.lower():
                    return jsonify({"found": True, "path": path, "name": name})
            except Exception:
                continue
        return jsonify({"found": False})
    except ImportError:
        return jsonify({"found": False, "error": "evdev not installed"})


@app.route("/setup/api/check_ppn", methods=["POST"])
def api_check_ppn():
    d = BASE_DIR / "wake_words"
    files = list(d.glob("*.ppn")) if d.exists() else []
    return jsonify({"found": bool(files), "files": [f.name for f in files]})


@app.route("/setup/api/check_creds")
def api_check_creds():
    return jsonify({"found": (BASE_DIR / "credentials" / "credentials.json").exists()})


@app.route("/setup/api/google_auth", methods=["POST"])
def api_google_auth():
    script = BASE_DIR / "credentials" / "setup_gmail_oauth.py"
    if not script.exists():
        return jsonify({"ok": False, "error": "credentials/setup_gmail_oauth.py not found"}), 404
    try:
        # Fire-and-forget: this opens a browser tab for the OAuth consent
        # screen and blocks on user interaction there, so the wizard can't
        # (and shouldn't) wait on it synchronously -- /setup/api/google_status
        # is polled from the browser instead.
        subprocess.Popen([sys.executable, str(script)], cwd=str(BASE_DIR))
        return jsonify({"ok": True, "message": "Google auth flow started — check for a new browser tab."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/setup/api/google_status")
def api_google_status():
    """Polled by the browser after google_auth starts the OAuth flow --
    credentials/setup_gmail_oauth.py writes gmail_token.json on success,
    so its existence is the completion signal."""
    token_path = BASE_DIR / "credentials" / "gmail_token.json"
    return jsonify({"authorized": token_path.exists()})


@app.route("/setup/api/check_ollama", methods=["POST"])
def api_check_ollama():
    if requests is None:
        return jsonify({"running": False})
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return jsonify({"running": r.status_code == 200})
    except Exception:
        return jsonify({"running": False})


@app.route("/setup/api/check_hud", methods=["POST"])
def api_check_hud():
    packages = {
        "pywebview": _can_import("webview"),
        "flask": _can_import("flask"),
        "flask_cors": _can_import("flask_cors"),
        "requests": _can_import("requests"),
        "psutil": _can_import("psutil"),
        "vgamepad": _can_import("vgamepad"),
    }
    return jsonify({
        "packages": packages,
        "hud_ready": packages["pywebview"] and packages["flask"] and packages["flask_cors"] and packages["psutil"],
        "retro_ready": packages["vgamepad"],
    })


@app.route("/setup/api/save", methods=["POST"])
def api_save():
    data = request.get_json(silent=True) or {}
    try:
        _write_env(data.get("api_keys", {}) or {})
        _update_config(data)
        _ensure_directories()
        _write_personality_state()
        summary = ["Wrote .env", "Updated config.yaml", "Created required directories", "Wrote personality_state.yaml"]
        return jsonify({"ok": True, "restart_required": False, "summary": summary})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/setup/api/launch", methods=["POST"])
def api_launch():
    if _is_windows():
        # No tmux/bash on Windows -- launch in its own console window instead.
        # --text (not --face) is the safer default: the kiosk face pulls in
        # extra display deps that aren't guaranteed to be installed yet.
        try:
            subprocess.Popen(
                [sys.executable, "main.py", "--text"], cwd=str(BASE_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return jsonify({"ok": True, "output": "Launched ShinAgent in a new console window."})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    script = BASE_DIR / "scripts" / "q2_start.sh"
    if not script.exists():
        return jsonify({"ok": False, "error": "scripts/q2_start.sh not found"}), 404
    try:
        result = subprocess.run(
            ["bash", str(script)], cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=20,
        )
        return jsonify({"ok": result.returncode == 0, "output": result.stdout + result.stderr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/setup/api/launch_hud", methods=["POST"])
def api_launch_hud():
    """Only offered when the wizard itself is running on Windows -- if the
    server is Linux/Pi, the HUD runs on a separate Windows gaming PC that
    this process has no way to reach."""
    if not _is_windows():
        return jsonify({"ok": False, "error": "HUD auto-launch is only available when ShinAgent itself is on Windows -- run hud/hud.py on your Windows gaming PC instead."}), 400
    script = BASE_DIR / "hud" / "hud.py"
    if not script.exists():
        return jsonify({"ok": False, "error": "hud/hud.py not found"}), 404
    try:
        subprocess.Popen(
            [sys.executable, str(script), "--q2", "localhost"], cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return jsonify({"ok": True, "message": "HUD launched in a new window."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/setup/api/server_ip")
def api_server_ip():
    return jsonify({"ip": _get_local_ip()})


@app.route("/setup/api/shutdown", methods=["POST"])
def api_shutdown():
    def _delayed_exit():
        time.sleep(2)
        os._exit(0)
    threading.Thread(target=_delayed_exit, daemon=True).start()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Wizard HTML — single-page app, all CSS/JS inline
# ---------------------------------------------------------------------------

WIZARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ShinAgent Setup</title>
<style>
  :root {
    --bg:      #00080a;
    --surface: #021210;
    --surface2:#0a1f1a;
    --border:  rgba(0, 220, 120, 0.15);
    --text:    #c8f0dc;
    --dim:     #4a8a6a;
    --accent:  #ff3c3c;
    --accent2: #00c8ff;
    --accent3: #00dc78;
    --warning: #ffb400;
    --danger:  #ff3c3c;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font-family: 'Courier New', monospace;
    min-height: 100%;
  }
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 999;
    background: repeating-linear-gradient(0deg, rgba(0,0,0,0.15) 0px, rgba(0,0,0,0.15) 1px, transparent 1px, transparent 3px);
    opacity: 0.35;
  }
  h1, h2, h3 { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }

  #app { display: flex; min-height: 100vh; }

  /* -- Sidebar -- */
  #sidebar {
    width: 240px; flex-shrink: 0;
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 20px 0;
    position: sticky; top: 0; height: 100vh; overflow-y: auto;
  }
  #sidebar .logo { text-align: center; margin-bottom: 24px; padding: 0 16px; }
  #sidebar .logo .title { font-size: 1.3rem; font-weight: 800; letter-spacing: 0.08em; color: var(--accent); text-shadow: 0 0 14px rgba(255,60,60,0.5); }
  #sidebar .logo .sub { font-size: 0.68rem; color: var(--dim); margin-top: 4px; }

  .step-item {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px; cursor: pointer; font-size: 0.82rem;
    border-left: 3px solid transparent;
    color: var(--dim);
  }
  .step-item:hover { background: rgba(0,220,120,0.04); }
  .step-item.active { color: var(--text); border-left-color: var(--accent2); background: rgba(0,200,255,0.06); }
  .step-item.done { color: var(--accent3); }
  .step-item.disabled { cursor: not-allowed; opacity: 0.4; }
  .step-num {
    width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.72rem; font-weight: 700;
    border: 1px solid var(--border); color: var(--dim);
  }
  .step-item.active .step-num { border-color: var(--accent2); color: var(--accent2); }
  .step-item.done .step-num { border-color: var(--accent3); background: var(--accent3); color: #000; }
  .step-item.skip .step-num { border-color: var(--warning); color: var(--warning); }
  .step-title-sm { flex: 1; }
  .step-icon { font-size: 0.9rem; }

  /* -- Main content -- */
  #main { flex: 1; position: relative; z-index: 1; min-width: 0; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 24px 20px 80px; }

  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; margin-bottom: 16px; }
  .step-title { font-size: 1.4rem; margin: 0 0 6px; color: var(--text); }
  .step-desc { color: var(--dim); margin: 0 0 20px; line-height: 1.5; font-size: 0.92rem; }

  .checklist { display: flex; flex-direction: column; gap: 8px; margin: 16px 0; }
  .check-row { display: flex; align-items: center; gap: 10px; font-size: 0.88rem; flex-wrap: wrap; }
  .badge { display: inline-flex; align-items: center; justify-content: center; min-width: 52px; padding: 2px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em; }
  .badge-ok   { background: rgba(0,220,120,0.15); color: var(--accent3); border: 1px solid var(--accent3); }
  .badge-warn { background: rgba(255,180,0,0.12); color: var(--warning); border: 1px solid var(--warning); }
  .badge-err  { background: rgba(255,60,60,0.12); color: var(--accent); border: 1px solid var(--accent); }
  .badge-info { background: rgba(0,200,255,0.1); color: var(--accent2); border: 1px solid var(--accent2); }
  .badge-none { background: var(--surface2); color: var(--dim); border: 1px solid var(--border); }

  .fix-cmd { margin: 4px 0 0 62px; }

  .row { margin-bottom: 16px; }
  .row label.field-label { display: block; font-size: 0.82rem; color: var(--dim); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.05em; }
  input[type=text], input[type=password], select, textarea {
    width: 100%; background: var(--surface2); border: 1px solid var(--border); color: var(--text);
    padding: 10px 12px; border-radius: 6px; font-family: inherit; font-size: 0.92rem;
  }
  input:focus, select:focus { outline: none; border-color: var(--accent2); }
  .input-with-btn { display: flex; gap: 8px; }
  .input-with-btn input { flex: 1; }
  .key-wrap { position: relative; }
  .key-wrap input { padding-right: 40px; }
  .eye-toggle { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); cursor: pointer; color: var(--dim); font-size: 0.8rem; user-select: none; }

  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 18px; border-radius: 6px; border: none; font-family: inherit; font-size: 0.88rem; font-weight: 700; cursor: pointer; letter-spacing: 0.03em; }
  .btn-primary { background: var(--accent); color: #000; }
  .btn-primary:hover { background: #ff5c5c; }
  .btn-ghost { background: transparent; color: var(--accent3); border: 1px solid var(--accent3); }
  .btn-ghost:hover { background: rgba(0,220,120,0.08); }
  .btn-cyan { background: transparent; color: var(--accent2); border: 1px solid var(--accent2); }
  .btn-cyan:hover { background: rgba(0,200,255,0.08); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-sm { padding: 6px 12px; font-size: 0.78rem; }
  .btn-full { width: 100%; }

  .backend-option { border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; cursor: pointer; transition: border-color 0.15s; }
  .backend-option.selected { border-color: var(--accent2); background: rgba(0,200,255,0.05); }
  .backend-option .bh { display: flex; align-items: center; gap: 10px; font-weight: 700; }
  .backend-option .bh .tag { font-size: 0.68rem; color: var(--accent); border: 1px solid var(--accent); border-radius: 3px; padding: 1px 6px; }
  .backend-option .bd { color: var(--dim); font-size: 0.84rem; margin: 6px 0 10px; }
  .backend-option .bkey { display: none; }
  .backend-option.selected .bkey { display: flex; gap: 8px; }

  .gemini-guide { border: 1px solid rgba(0,220,120,0.2); background: rgba(0,20,10,0.5); border-radius: 8px; padding: 14px 16px; margin: 4px 0 12px; }
  .gemini-guide .gg-title { display: flex; align-items: center; justify-content: space-between; font-size: 0.76rem; letter-spacing: 0.06em; color: var(--accent3); font-weight: 700; margin-bottom: 10px; }
  .gemini-guide .gg-step { margin-bottom: 12px; font-size: 0.84rem; color: var(--dim); line-height: 1.6; }
  .gemini-guide .gg-step b { color: var(--text); display: block; margin-bottom: 4px; }
  .gemini-guide .gg-step ul { margin: 4px 0 0 18px; padding: 0; }
  .gemini-guide .gg-freetier { font-size: 0.8rem; color: var(--dim); border-top: 1px solid rgba(0,220,120,0.15); padding-top: 10px; margin-top: 4px; line-height: 1.6; }
  .gemini-model-row { margin-top: 12px; }

  .card-toggle { display: flex; align-items: center; justify-content: space-between; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
  .card-toggle .ct-body { flex: 1; }
  .card-toggle .ct-title { font-weight: 700; margin-bottom: 4px; }
  .card-toggle .ct-desc { color: var(--dim); font-size: 0.82rem; line-height: 1.5; }
  .card-toggle-extra { border: 1px solid var(--border); border-top: none; border-radius: 0 0 8px 8px; margin: -12px 0 12px; padding: 14px 16px; background: rgba(0,0,0,0.15); }

  .toggle { position: relative; width: 46px; height: 26px; flex-shrink: 0; }
  .toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-track { position: absolute; inset: 0; background: var(--surface2); border: 1px solid var(--border); border-radius: 999px; transition: 0.15s; }
  .toggle input:checked ~ .toggle-track { background: var(--accent3); border-color: var(--accent3); }
  .toggle-thumb { position: absolute; top: 2px; left: 2px; width: 20px; height: 20px; border-radius: 50%; background: #fff; transition: 0.15s; }
  .toggle input:checked ~ .toggle-thumb { transform: translateX(20px); }

  .term-log { background: #00080a; border: 1px solid rgba(0,220,120,0.2); font: 12px 'Courier New', monospace; color: var(--accent3); padding: 12px; overflow-y: auto; height: 200px; white-space: pre-wrap; border-radius: 6px; }

  code, pre.code { background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; display: block; color: var(--accent2); font-size: 0.82rem; overflow-x: auto; }

  .nav-row { display: flex; justify-content: space-between; align-items: center; margin-top: 20px; }
  .skip-link { color: var(--dim); font-size: 0.82rem; text-decoration: underline; cursor: pointer; }
  .skip-link:hover { color: var(--text); }

  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--dim); border-top-color: var(--accent2); border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .success-screen { text-align: center; padding: 40px 0; }
  .success-screen .big { font-size: 1.8rem; color: var(--accent3); margin-bottom: 8px; text-shadow: 0 0 16px rgba(0,220,120,0.4); }
  .quick-ref { text-align: left; margin-top: 24px; }

  .summary-table { width: 100%; border-collapse: collapse; margin: 12px 0; }
  .summary-table td { padding: 6px 8px; font-size: 0.86rem; border-bottom: 1px solid var(--border); }
  .summary-table td:first-child { color: var(--dim); }
  .summary-table td:last-child { text-align: right; }

  .hidden { display: none !important; }
  .muted { color: var(--dim); font-size: 0.82rem; }

  .platform-badge {
    display: inline-flex; align-items: center; font-size: 0.68rem; font-weight: 700;
    letter-spacing: 0.04em; padding: 3px 9px; border-radius: 999px; margin-top: 8px;
    background: rgba(255,180,0,0.15); border: 1px solid var(--warning); color: var(--warning);
  }
  .platform-badge.badge-pi      { background: rgba(0,220,120,0.15); border-color: var(--accent3); color: var(--accent3); }
  .platform-badge.badge-windows { background: rgba(0,200,255,0.15); border-color: var(--accent2); color: var(--accent2); }
  .platform-badge.badge-linux   { background: rgba(255,180,0,0.15); border-color: var(--warning); color: var(--warning); }

  @media (max-width: 720px) {
    #app { flex-direction: column; }
    #sidebar { width: 100%; height: auto; position: relative; display: flex; overflow-x: auto; padding: 10px 0; }
    #sidebar .logo { display: none; }
    .step-item { flex-direction: column; gap: 4px; padding: 8px 12px; border-left: none; border-bottom: 3px solid transparent; white-space: nowrap; }
    .step-item.active { border-left: none; border-bottom-color: var(--accent2); }
  }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <div class="logo">
      <div class="title">SHINAGENT</div>
      <div class="sub">Setup Wizard</div>
      <div id="platformBadge" class="platform-badge hidden"></div>
    </div>
    <div id="stepList"></div>
  </div>
  <div id="main"><div class="wrap" id="stepContainer"></div></div>
</div>

<script>
const STEP_META = [
  { key: 'system',  title: 'System Check' },
  { key: 'llm',     title: 'AI Backend' },
  { key: 'voice',   title: 'Voice Setup' },
  { key: 'google',  title: 'Google (Optional)', skippable: true },
  { key: 'simrace', title: 'Sim Racing / Flight', skippable: true },
  { key: 'ed',      title: 'Elite Dangerous', skippable: true },
  { key: 'hud',     title: 'Desktop HUD', skippable: true },
  { key: 'review',  title: 'Ready to Launch' },
];

const BACKENDS = [
  { id: 'gemini', label: 'Gemini 2.5 Flash (Google)', tag: 'FREE — RECOMMENDED', envKey: 'GEMINI_API_KEY', testKey: 'gemini',
    desc: 'No credit card. 500 requests/day. 1M token context. Get a key at aistudio.google.com', needsKey: true },
  { id: 'claude', label: 'Claude (Anthropic)', tag: 'Best quality', envKey: 'ANTHROPIC_API_KEY', testKey: 'anthropic',
    desc: 'Best quality and tool compliance. Get a key at console.anthropic.com', needsKey: true },
  { id: 'openai', label: 'GPT-4o (OpenAI)', tag: 'Strong tools', envKey: 'OPENAI_API_KEY', testKey: 'openai',
    desc: 'Strong alternative. Get a key at platform.openai.com', needsKey: true },
  { id: 'grok', label: 'Grok (xAI)', tag: 'Fast', envKey: 'XAI_API_KEY', testKey: 'xai',
    desc: 'Fast responses. Get a key at console.x.ai', needsKey: true, noTest: true },
  { id: 'glm', label: 'GLM-5.2 (Z.ai)', tag: '1M context', envKey: 'ZAI_API_KEY', testKey: 'zai',
    desc: '1 million token context. Get a key at z.ai', needsKey: true, noTest: true },
  { id: 'ollama', label: 'Ollama (Local — no API key)', tag: 'No API key needed', envKey: '', testKey: '',
    desc: 'Runs entirely on your machine. Slowest option, no API costs.', needsKey: false, isOllama: true },
];

const VOICE_OPTIONS = [
  { group: 'Masculine', options: [
    ['aura-2-zeus-en','Zeus - American, deep (recommended)'], ['aura-2-draco-en','Draco - British, deep'],
    ['aura-2-orion-en','Orion - American'],
    ['aura-2-arcas-en','Arcas - American'], ['aura-2-perseus-en','Perseus - American'],
    ['aura-2-angus-en','Angus - Scottish'], ['aura-2-helios-en','Helios - American'],
    ['aura-2-orpheus-en','Orpheus - American'],
  ]},
  { group: 'Feminine', options: [
    ['aura-2-luna-en','Luna - American'], ['aura-2-stella-en','Stella - American'],
    ['aura-2-athena-en','Athena - British'], ['aura-2-hera-en','Hera - American'],
    ['aura-2-asteria-en','Asteria - American'], ['aura-2-thalia-en','Thalia - American'],
  ]},
];

let state = {
  step: 0,
  skipped: {},
  completed: {},
  check: {},
  llm_backend: 'gemini',
  api_keys: { ANTHROPIC_API_KEY:'', OPENAI_API_KEY:'', XAI_API_KEY:'', ZAI_API_KEY:'', GEMINI_API_KEY:'',
              DEEPGRAM_API_KEY:'', PORCUPINE_ACCESS_KEY:'', INARA_API_KEY:'', TAVILY_API_KEY:'' },
  key_status: {},
  audio: { input_devices: [], output_devices: [] },
  input_device: '', output_device: '',
  tts_voice: 'aura-2-zeus-en', tts_tested: false,
  wake_word_enabled: false, wake_word_found: false,
  google_creds_found: false, google_authorized: false,
  forza_enabled: false, ac_enabled: false, msfs_enabled: false,
  ed_enabled: false, inara_enabled: false,
  hud_check: null,
  agent_name: 'ShinAgent',
  gemini_model: 'gemini-2.5-flash',
  gemini_key_status: null,
  gemini_status_message: '',
  server_ip: 'localhost',
};
let geminiGuideOpen = true;
let PLATFORM = { is_windows: false, is_linux: true, is_pi: false, platform_name: '', python_version: '', hostname: '' };

function updatePlatformUI() {
  const badge = document.getElementById('platformBadge');
  if (badge && PLATFORM.platform_name) {
    badge.textContent = PLATFORM.platform_name;
    badge.classList.remove('hidden', 'badge-pi', 'badge-windows', 'badge-linux');
    badge.classList.add(PLATFORM.is_pi ? 'badge-pi' : PLATFORM.is_windows ? 'badge-windows' : 'badge-linux');
  }
  document.title = PLATFORM.is_pi ? 'ShinAgent Setup — Pi 5'
    : PLATFORM.is_windows ? 'ShinAgent Setup — Windows'
    : 'ShinAgent Setup — Linux';
}

function renderSidebar() {
  const list = document.getElementById('stepList');
  list.innerHTML = STEP_META.map((s, i) => {
    let cls = 'step-item';
    if (i === state.step) cls += ' active';
    else if (state.completed[i]) cls += ' done';
    else if (state.skipped[i]) cls += ' skip';
    if (i > maxReachableStep()) cls += ' disabled';
    const icon = state.skipped[i] ? '&#9711;' : state.completed[i] ? '&#10003;' : (i === state.step ? '&#9656;' : '');
    return `<div class="${cls}" onclick="jumpToStep(${i})">
      <span class="step-num">${i+1}</span>
      <span class="step-title-sm">${s.title}</span>
      <span class="step-icon">${icon}</span>
    </div>`;
  }).join('');
}

function maxReachableStep() {
  // Can jump back to any completed/skipped step, or forward one past the
  // furthest step already completed/skipped/current.
  let max = state.step;
  for (let i = 0; i < STEP_META.length; i++) {
    if (state.completed[i] || state.skipped[i]) max = Math.max(max, i + 1);
  }
  return Math.min(max, STEP_META.length - 1);
}

function jumpToStep(n) {
  if (n > maxReachableStep()) return;
  goToStep(n);
}

function goToStep(n) {
  state.step = n;
  renderSidebar();
  renderStep();
  window.scrollTo(0, 0);
}
function nextStep() {
  if (!validateStep(state.step)) return;
  state.completed[state.step] = true;
  goToStep(Math.min(state.step + 1, STEP_META.length - 1));
}
function prevStep() { goToStep(Math.max(state.step - 1, 0)); }
function skipStep() {
  state.skipped[state.step] = true;
  goToStep(Math.min(state.step + 1, STEP_META.length - 1));
}

function validateStep(n) {
  if (STEP_META[n].key === 'llm') {
    const b = BACKENDS.find(x => x.id === state.llm_backend);
    if (b && b.needsKey && !state.api_keys[b.envKey]) {
      alert('Enter an API key for your selected backend (or choose Ollama for no key needed).');
      return false;
    }
  }
  return true;
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !['TEXTAREA'].includes(document.activeElement.tagName)) {
    const next = document.getElementById('btnNext');
    if (next && !next.disabled) { e.preventDefault(); nextStep(); }
  }
});

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

function badge(kind, text) {
  const cls = kind === 'ok' ? 'badge-ok' : kind === 'warn' ? 'badge-warn' : kind === 'err' ? 'badge-err' : kind === 'info' ? 'badge-info' : 'badge-none';
  const label = kind === 'ok' ? 'OK' : kind === 'warn' ? 'WARN' : kind === 'err' ? 'FAIL' : kind === 'info' ? 'INFO' : '--';
  return `<span class="badge ${cls}">${label}</span> ${text}`;
}

function eyeToggle(id) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
}

function navRow(opts) {
  opts = opts || {};
  const back = state.step > 0 ? `<button class="btn btn-ghost" onclick="prevStep()">&larr; Back</button>` : '<span></span>';
  const skip = STEP_META[state.step].skippable ? `<span class="skip-link" onclick="skipStep()">Skip this step</span>` : '';
  const next = opts.hideNext ? '' : `<button class="btn btn-primary" id="btnNext" onclick="nextStep()">Next &rarr;</button>`;
  return `<div class="nav-row">${back}<div style="display:flex;gap:16px;align-items:center">${skip}${next}</div></div>`;
}

// ---------------------------------------------------------------------------
// Step: System Check
// ---------------------------------------------------------------------------
async function loadStepSystem() {
  const el = document.getElementById('stepContainer');
  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">System Check</h2>
      <p class="step-desc">Verifying your system is ready for ShinAgent. Takes about 10 minutes total to finish setup.</p>
      <div class="checklist" id="checklist"><div class="muted">Checking system...</div></div>
      <div id="systemInstallArea" class="hidden" style="margin-top:14px">
        ${PLATFORM.is_windows ? '<pre class="code">pip install pyaudio</pre><p class="muted">If that fails to build, download a pre-built wheel: <a href="https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio" target="_blank" rel="noopener" style="color:var(--accent2)">lfd.uci.edu/~gohlke/pythonlibs &#8599;</a> then <code style="display:inline;padding:2px 6px">pip install PyAudio&#8209;0.2.14&#8209;cpXX&#8209;cpXX&#8209;win_amd64.whl</code></p>' : ''}
        <div class="term-log" id="systemInstallLog"></div>
        <div style="margin-top:10px"><button class="btn btn-ghost" id="systemInstallBtn" onclick="runInstallSystem()">${PLATFORM.is_windows ? 'Install PyAudio' : 'Install Missing System Packages'}</button></div>
      </div>
      <div id="installArea" class="hidden" style="margin-top:14px">
        <div class="term-log" id="installLog"></div>
        <div style="margin-top:10px"><button class="btn btn-primary" id="installBtn" onclick="runInstall()">Install Python Requirements</button></div>
      </div>
    </div>
    ${navRow()}
  `;
  const check = await api('/setup/api/check');
  state.check = check;
  const rows = [];
  rows.push([check.python_ok ? 'ok' : 'err', `Python ${check.python_version} (3.11+ required)`]);
  rows.push([check.is_64bit ? 'ok' : 'warn', check.is_64bit ? '64-bit Python' : '32-bit Python detected']);
  rows.push([check.venv_active ? 'ok' : 'err', check.venv_active ? 'Virtual environment active' : 'Not running inside a virtual environment']);
  rows.push([check.pip_available ? 'ok' : 'err', 'pip available']);

  if (PLATFORM.is_windows) {
    rows.push([check.portaudio ? 'ok' : 'warn', 'PyAudio', check.portaudio ? '' : 'pip install pyaudio']);
    if (check.disk_free_gb != null) rows.push([check.disk_free_gb >= 2 ? 'ok' : 'warn', `${check.disk_free_gb} GB free disk`]);
    rows.push(['info', `Platform: ${PLATFORM.platform_name || check.platform}`]);
    if (check.ram_gb != null) rows.push(['info', `${check.ram_gb} GB RAM`]);
    rows.push([check.browser ? 'ok' : 'warn', check.browser ? 'Browser detected (Chrome/Edge/Chromium)' : 'No Chrome, Edge, or Chromium found']);
    rows.push(['info', 'Windows Defender/Firewall', 'Ensure Python is allowed through Windows Defender Firewall for local network access (other devices reaching the web app).']);
    rows.push(['info', 'tmux not available on Windows', 'Use the ShinAgent HUD to manage ShinAgent, or run directly:\npython main.py --text\n\nFor background operation, use Windows Task Scheduler or run in a separate PowerShell window.']);
  } else {
    rows.push([check.portaudio ? 'ok' : 'warn', 'portaudio', check.portaudio ? '' : 'sudo apt-get install -y portaudio19-dev']);
    rows.push([check.ffmpeg ? 'ok' : 'warn', 'ffmpeg', check.ffmpeg ? '' : 'sudo apt-get install -y ffmpeg']);
    rows.push([check.browser ? 'ok' : 'warn', 'chromium (kiosk display)', check.browser ? '' : 'sudo apt-get install -y chromium-browser']);
    rows.push([check.tmux ? 'ok' : 'warn', 'tmux', check.tmux ? '' : 'sudo apt-get install -y tmux']);
    if (check.disk_free_gb != null) rows.push([check.disk_free_gb >= 2 ? 'ok' : 'warn', `${check.disk_free_gb} GB free disk`]);
    rows.push(['info', `Platform: ${check.platform} ${check.platform_release || ''}`]);
    if (check.ram_gb != null) rows.push(['info', `${check.ram_gb} GB RAM`]);
    rows.push(['info', PLATFORM.is_pi ? `${check.pi_model || 'Raspberry Pi'} detected -- optimal hardware` : (check.pi_model || 'Not a Raspberry Pi (continuing anyway)')]);
  }
  rows.push([check.requirements_installed ? 'ok' : 'warn', check.requirements_installed ? 'Python requirements installed' : 'Python requirements not yet installed']);

  document.getElementById('checklist').innerHTML = rows.map(r => {
    const line = `<div class="check-row">${badge(r[0], r[1])}</div>`;
    const fix = r[2] ? `<pre class="code fix-cmd">${r[2]}</pre>` : '';
    return line + fix;
  }).join('');

  const needsInstallArea = PLATFORM.is_windows
    ? !check.portaudio
    : (!check.portaudio || !check.ffmpeg || !check.browser || !check.tmux);
  if (needsInstallArea) {
    document.getElementById('systemInstallArea').classList.remove('hidden');
  }
  if (!check.requirements_installed) {
    document.getElementById('installArea').classList.remove('hidden');
  }

  const nextBtn = document.getElementById('btnNext');
  if (nextBtn) nextBtn.disabled = !(check.python_ok && check.venv_active);
}

function runInstallSystem() {
  const btn = document.getElementById('systemInstallBtn');
  const log = document.getElementById('systemInstallLog');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Installing...';
  log.textContent = '';
  fetch('/setup/api/install_system', { method: 'POST' }).then(async (resp) => {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n\n');
      buf = lines.pop();
      for (const chunk of lines) {
        if (!chunk.startsWith('data: ')) continue;
        const text = chunk.slice(6);
        if (text === 'DONE') {
          log.textContent += '\nDone. Re-checking...\n';
          btn.disabled = false;
          btn.textContent = 'Install Missing System Packages';
          loadStepSystem();
        } else {
          log.textContent += text + '\n';
        }
        log.scrollTop = log.scrollHeight;
      }
    }
  }).catch(e => { log.textContent += `\nERROR: ${e}\n`; btn.disabled = false; });
}

function runInstall() {
  const btn = document.getElementById('installBtn');
  const log = document.getElementById('installLog');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Installing...';
  log.textContent = '';
  fetch('/setup/api/install', { method: 'POST' }).then(async (resp) => {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n\n');
      buf = lines.pop();
      for (const chunk of lines) {
        if (!chunk.startsWith('data: ')) continue;
        const text = chunk.slice(6);
        if (text === 'DONE') {
          log.textContent += '\nInstallation complete.\n';
          btn.textContent = 'Installation complete';
          btn.disabled = true;
        } else {
          log.textContent += text + '\n';
        }
        log.scrollTop = log.scrollHeight;
      }
    }
  }).catch(e => { log.textContent += `\nERROR: ${e}\n`; btn.disabled = false; });
}

// ---------------------------------------------------------------------------
// Step: LLM Backend
// ---------------------------------------------------------------------------
function geminiGuideHTML() {
  if (!geminiGuideOpen) {
    return `<div class="skip-link" onclick="toggleGeminiGuide(event)" style="margin-bottom:10px">show setup guide</div>`;
  }
  return `
    <div class="gemini-guide" onclick="event.stopPropagation()">
      <div class="gg-title">
        <span>GETTING YOUR FREE GEMINI API KEY</span>
        <span class="skip-link" onclick="toggleGeminiGuide(event)">hide guide</span>
      </div>
      <div class="gg-step">
        <b>Step 1: Open Google AI Studio</b>
        <div style="margin:8px 0">
          <a class="btn btn-primary btn-sm" href="https://aistudio.google.com" target="_blank" rel="noopener">Open aistudio.google.com &#8599;</a>
        </div>
        Sign in with your Google account.
      </div>
      <div class="gg-step">
        <b>Step 2: Create an API key</b>
        <ul>
          <li>Click "Get API key" in the left sidebar</li>
          <li>Click "Create API key"</li>
          <li>Select "Create API key in new project" (easiest option)</li>
          <li>Your key will appear — it starts with "AIza..."</li>
        </ul>
      </div>
      <div class="gg-step" style="margin-bottom:0"><b>Step 3: Paste your key below and click TEST</b></div>
    </div>
  `;
}

function geminiStatusHTML() {
  const s = state.gemini_key_status;
  if (s === 'ok') return badge('ok', state.gemini_status_message || 'Key valid — Gemini 2.5 Flash ready');
  if (s === 'invalid') return badge('err', state.gemini_status_message || 'Key rejected — check you copied it correctly');
  if (s === 'error') return badge('warn', state.gemini_status_message || 'Could not reach Google API — check network');
  return '';
}

function geminiFooterHTML() {
  return `
    <div class="gg-freetier">
      500 requests/day &nbsp;|&nbsp; 60 requests/minute &nbsp;|&nbsp; 1M token context<br>
      No credit card required. No expiry.
    </div>
    <p class="muted" style="margin-top:8px">Your key is stored only in your local .env file. It is never sent to ShinAgent servers.</p>
  `;
}

function geminiModelSelectorHTML() {
  if (state.gemini_key_status !== 'ok') return '';
  return `
    <div class="row gemini-model-row" onclick="event.stopPropagation()">
      <label class="field-label">Model</label>
      <select id="geminiModel" onchange="state.gemini_model=this.value">
        <option value="gemini-2.5-flash" ${state.gemini_model==='gemini-2.5-flash'?'selected':''}>gemini-2.5-flash (recommended — fast, capable, free)</option>
        <option value="gemini-2.5-flash-lite" ${state.gemini_model==='gemini-2.5-flash-lite'?'selected':''}>gemini-2.5-flash-lite (faster, lower limits)</option>
        <option value="gemini-2.0-flash" ${state.gemini_model==='gemini-2.0-flash'?'selected':''}>gemini-2.0-flash (previous generation)</option>
      </select>
    </div>
  `;
}

function loadStepLLM() {
  const el = document.getElementById('stepContainer');
  const opts = BACKENDS.map(b => {
    const selected = state.llm_backend === b.id ? 'selected' : '';
    let inner = '';
    if (b.id === 'gemini') {
      inner = `
        <div class="bd">${b.desc}</div>
        ${selected ? geminiGuideHTML() : ''}
        <div class="bkey key-wrap input-with-btn">
          <div style="flex:1;position:relative">
            <input type="password" id="key_${b.id}" placeholder="API Key" value="${state.api_keys[b.envKey] || ''}"
                   oninput="state.api_keys['${b.envKey}']=this.value" style="padding-right:40px">
            <span class="eye-toggle" onclick="eyeToggle('key_${b.id}')">show</span>
          </div>
          <button class="btn btn-cyan btn-sm" onclick="testGeminiKey(event)">Test</button>
        </div>
        <div id="keyStatus_${b.id}" style="margin-top:6px">${selected ? geminiStatusHTML() : ''}</div>
        ${selected ? geminiModelSelectorHTML() : ''}
        ${selected ? geminiFooterHTML() : ''}
      `;
    } else if (b.isOllama) {
      inner = `
        <div class="bd">${b.desc}</div>
        <code>curl -fsSL https://ollama.com/install.sh | sh</code>
        <div style="margin-top:8px"><button class="btn btn-cyan btn-sm" onclick="checkOllama(event)">Check Ollama</button> <span id="ollamaStatus"></span></div>
        <p class="muted" style="margin-top:8px">Then pull a model: <code style="display:inline;padding:2px 6px">ollama pull llama3</code></p>
      `;
    } else {
      inner = `
        <div class="bd">${b.desc}</div>
        <div class="bkey key-wrap input-with-btn">
          <div style="flex:1;position:relative">
            <input type="password" id="key_${b.id}" placeholder="API Key" value="${state.api_keys[b.envKey] || ''}"
                   oninput="state.api_keys['${b.envKey}']=this.value" style="padding-right:40px">
            <span class="eye-toggle" onclick="eyeToggle('key_${b.id}')">show</span>
          </div>
          ${b.noTest ? '' : `<button class="btn btn-cyan btn-sm" onclick="testKey(event, '${b.id}','${b.testKey}','${b.envKey}')">Test</button>`}
        </div>
        <div id="keyStatus_${b.id}" style="margin-top:6px"></div>
      `;
    }
    return `
      <div class="backend-option ${selected}" onclick="selectBackend('${b.id}')">
        <div class="bh">${b.label}${b.tag ? `<span class="tag">${b.tag}</span>` : ''}</div>
        ${inner}
      </div>
    `;
  }).join('');

  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">AI Backend</h2>
      <p class="step-desc">Choose your primary AI engine. You need at least one API key to use ShinAgent (or Ollama for a fully local, keyless setup).</p>
      ${opts}
      <p class="muted" style="margin-top:14px">Additional backends can be added later in Settings.</p>
    </div>
    ${navRow()}
  `;
}

function selectBackend(id) {
  state.llm_backend = id;
  loadStepLLM();
}

function toggleGeminiGuide(evt) {
  evt.stopPropagation();
  geminiGuideOpen = !geminiGuideOpen;
  loadStepLLM();
}

async function testGeminiKey(evt) {
  evt.stopPropagation();
  const statusEl = document.getElementById('keyStatus_gemini');
  statusEl.innerHTML = '<span class="spinner"></span> Testing...';
  const key = state.api_keys.GEMINI_API_KEY;
  const result = await api('/setup/api/test', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backend: 'gemini', key }),
  });
  state.key_status.GEMINI_API_KEY = result.gemini;
  if (result.gemini === 'ok') {
    state.gemini_key_status = 'ok';
    state.gemini_status_message = result.note ? `Key valid — ${result.note}` : 'Key valid — Gemini 2.5 Flash ready';
  } else if (result.gemini === 'invalid') {
    state.gemini_key_status = 'invalid';
    state.gemini_status_message = 'Key rejected — check you copied it correctly';
  } else {
    state.gemini_key_status = 'error';
    state.gemini_status_message = 'Could not reach Google API — check network';
  }
  loadStepLLM();
}

async function testKey(evt, backendId, testKey, envKey) {
  evt.stopPropagation();
  const statusEl = document.getElementById(`keyStatus_${backendId}`);
  statusEl.innerHTML = '<span class="spinner"></span> Testing...';
  const key = state.api_keys[envKey];
  const result = await api('/setup/api/test', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backend: testKey, key }),
  });
  const status = result[testKey];
  state.key_status[envKey] = status;
  if (status === 'ok') statusEl.innerHTML = badge('ok', 'Key valid');
  else if (status === 'not_tested') statusEl.innerHTML = badge('warn', 'Cannot verify without billing — saved as-is');
  else statusEl.innerHTML = badge('err', 'Key rejected');
}

async function checkOllama(evt) {
  evt.stopPropagation();
  const el = document.getElementById('ollamaStatus');
  el.innerHTML = '<span class="spinner"></span>';
  const r = await api('/setup/api/check_ollama', { method: 'POST' });
  el.innerHTML = r.running ? badge('ok', 'Ollama running') : badge('err', 'Not running on port 11434');
}

// ---------------------------------------------------------------------------
// Step: Voice
// ---------------------------------------------------------------------------
async function loadStepVoice() {
  const el = document.getElementById('stepContainer');
  el.innerHTML = `<div class="card"><div class="muted">Detecting audio devices...</div></div>`;
  const audio = await api('/setup/api/audio');
  state.audio = audio;

  if (!state.input_device && audio.input_devices.length) {
    const c920 = audio.input_devices.find(d => /c920/i.test(d.name));
    state.input_device = (c920 || audio.input_devices[0]).name;
  }
  if (!state.output_device && audio.output_devices.length) {
    const pw = audio.output_devices.find(d => /pipewire/i.test(d.name));
    state.output_device = (pw || audio.output_devices[0]).name;
  }

  const inputOpts = audio.input_devices.map(d => `<option value="${d.name}" ${d.name===state.input_device?'selected':''}>${d.name}</option>`).join('')
    || '<option value="">No input devices detected</option>';
  const outputOpts = audio.output_devices.map(d => `<option value="${d.name}" ${d.name===state.output_device?'selected':''}>${d.name}</option>`).join('')
    || '<option value="">No output devices detected</option>';
  const voiceOpts = VOICE_OPTIONS.map(g => `<optgroup label="${g.group}">${g.options.map(([v,l]) => `<option value="${v}" ${v===state.tts_voice?'selected':''}>${l}</option>`).join('')}</optgroup>`).join('');

  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">Voice Setup</h2>
      <p class="step-desc">Configure your microphone, speakers, and text-to-speech voice.</p>

      ${audio.error ? `<div class="row">${badge('warn', audio.error)}</div>` : ''}
      <div class="row">
        <label class="field-label">Microphone</label>
        <div class="input-with-btn">
          <select id="inputDevice" onchange="state.input_device=this.value">${inputOpts}</select>
          <button class="btn btn-cyan btn-sm" onclick="testMic()">Test mic</button>
        </div>
        <div id="micTestStatus" style="margin-top:6px"></div>
      </div>

      <div class="row">
        <label class="field-label">Speaker / Output</label>
        <select id="outputDevice" onchange="state.output_device=this.value">${outputOpts}</select>
      </div>

      <div class="row">
        <label class="field-label">Text-to-Speech (Deepgram Aura-2)</label>
        <div class="input-with-btn">
          <div style="flex:1;position:relative">
            <input type="password" id="deepgramKey" placeholder="Deepgram API Key" value="${state.api_keys.DEEPGRAM_API_KEY}"
                   oninput="state.api_keys.DEEPGRAM_API_KEY=this.value" style="padding-right:40px">
            <span class="eye-toggle" onclick="eyeToggle('deepgramKey')">show</span>
          </div>
          <button class="btn btn-cyan btn-sm" onclick="testKey(event,'deepgram','deepgram','DEEPGRAM_API_KEY')">Test</button>
        </div>
        <div id="keyStatus_deepgram" style="margin-top:6px"></div>
        <p class="muted">Get a free key at deepgram.com — generous free tier. <a href="https://deepgram.com" target="_blank" rel="noopener" style="color:var(--accent2)">Open deepgram.com &#8599;</a></p>
        <div style="margin-top:10px">
          <select id="ttsVoice" onchange="state.tts_voice=this.value">${voiceOpts}</select>
          <div style="margin-top:8px"><button class="btn btn-cyan btn-sm" onclick="previewVoice()">Preview</button> <span id="previewStatus"></span></div>
        </div>
      </div>

      <div class="card-toggle">
        <div class="ct-body">
          <div class="ct-title">Wake word ("Hey Dude")</div>
          <div class="ct-desc">Hands-free activation via Picovoice Porcupine.</div>
        </div>
        <label class="toggle"><input type="checkbox" ${state.wake_word_enabled?'checked':''} onchange="toggleWakeWord(this.checked)"><span class="toggle-track"></span><span class="toggle-thumb"></span></label>
      </div>
      <div id="wakeWordExtra" class="${state.wake_word_enabled?'':'hidden'}">
        <div class="card-toggle-extra">
          <p class="muted">Requires a free Picovoice account at console.picovoice.ai</p>
          <div class="row">
            <input type="password" id="porcupineKey" placeholder="Picovoice Access Key" value="${state.api_keys.PORCUPINE_ACCESS_KEY}"
                   oninput="state.api_keys.PORCUPINE_ACCESS_KEY=this.value">
          </div>
          <p class="muted">Download the Hey-Dude model file and place it in <code style="display:inline;padding:2px 6px">wake_words/</code></p>
          <button class="btn btn-cyan btn-sm" onclick="checkWakeWordFile()">Check for .ppn file</button>
          <div id="wakeWordStatus" style="margin-top:8px"></div>
        </div>
      </div>

      <div class="row" style="margin-top:14px">
        <label class="field-label">Controller (optional)</label>
        <div class="input-with-btn">
          <div id="controllerStatus" class="muted">Not checked yet</div>
          <button class="btn btn-cyan btn-sm" onclick="checkController()">Check for controller</button>
        </div>
        <p class="muted" style="margin-top:6px">
          8BitDo Zero 2 in gamepad mode — used for hands-free push-to-talk,
          volume, repeat, and personality-mode switching.
          <a href="javascript:void(0)" onclick="togglePairingInstructions()" style="color:var(--accent2)">Pairing instructions &#9660;</a>
        </p>
        <div id="pairingInstructions" class="hidden" style="margin-top:8px;background:rgba(0,220,120,0.04);border:1px solid rgba(0,220,120,0.15);border-radius:8px;padding:12px;font-size:0.85rem;line-height:1.7">
          Power on with <b>B + START</b> for gamepad mode (LED blinks once
          per cycle). Hold <b>SELECT</b> 3 seconds for pairing mode (LED
          rapid-blinks), then pair via Bluetooth settings or
          <code style="display:inline;padding:1px 5px">bluetoothctl</code>.
          Recommended: press <b>LEFT + SELECT</b> on the controller for
          hat-mode D-pad (cleaner than the analog-stick default).
          <p style="color:#e8a33d;margin-top:8px;margin-bottom:0">
            &#9888; Avoid <b>R + START</b> (keyboard mode) — causes typing
            conflicts when using the web app.
          </p>
        </div>
      </div>
    </div>
    ${navRow()}
  `;
  checkWakeWordFile(true);
  checkController(true);
}

function toggleWakeWord(v) {
  state.wake_word_enabled = v;
  document.getElementById('wakeWordExtra').classList.toggle('hidden', !v);
}

function togglePairingInstructions() {
  document.getElementById('pairingInstructions').classList.toggle('hidden');
}

async function checkController(silent = false) {
  const el = document.getElementById('controllerStatus');
  if (!silent) el.innerHTML = '<span class="spinner"></span> Checking...';
  try {
    const r = await api('/setup/api/detect_controller');
    if (r.platform === 'windows') {
      el.innerHTML = badge('warn', 'Controller input is Pi/Linux-only — the HUD is the Windows interface');
    } else if (r.found) {
      el.innerHTML = badge('ok', `Found: ${r.name} (${r.path})`);
    } else {
      el.innerHTML = badge('warn', 'Not found — see pairing instructions below');
    }
  } catch (e) {
    el.innerHTML = badge('warn', 'Could not check (evdev not installed?)');
  }
}

async function testMic() {
  const statusEl = document.getElementById('micTestStatus');
  statusEl.innerHTML = '<span class="spinner"></span> Recording 2 seconds, then playing back...';
  const dev = state.audio.input_devices.find(d => d.name === state.input_device);
  const outDev = state.audio.output_devices.find(d => d.name === state.output_device);
  const r = await api('/setup/api/test/mic', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_index: dev ? dev.index : null, output_index: outDev ? outDev.index : null }),
  });
  statusEl.innerHTML = r.ok ? badge('ok', 'Playback complete — did you hear yourself?') : badge('err', r.error || 'Mic test failed');
}

async function previewVoice() {
  const statusEl = document.getElementById('previewStatus');
  const key = state.api_keys.DEEPGRAM_API_KEY;
  if (!key) { statusEl.innerHTML = badge('warn', 'Enter a Deepgram key first'); return; }
  statusEl.innerHTML = '<span class="spinner"></span>';
  try {
    const r = await fetch('/setup/api/tts_preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, voice: state.tts_voice }),
    });
    if (!r.ok) { const err = await r.json(); statusEl.innerHTML = badge('err', err.error || 'Preview failed'); return; }
    const blob = await r.blob();
    const audioEl = new Audio(URL.createObjectURL(blob));
    audioEl.play();
    statusEl.innerHTML = badge('ok', 'Playing...');
    state.tts_tested = true;
  } catch (e) { statusEl.innerHTML = badge('err', String(e)); }
}

async function checkWakeWordFile(silent) {
  const r = await api('/setup/api/check_ppn', { method: 'POST' });
  state.wake_word_found = r.found;
  const el = document.getElementById('wakeWordStatus');
  if (!el) return;
  el.innerHTML = r.found ? badge('ok', r.files[0]) : badge(silent ? 'none' : 'err', 'No .ppn file in wake_words/ yet');
}

// ---------------------------------------------------------------------------
// Step: Google (optional)
// ---------------------------------------------------------------------------
async function loadStepGoogle() {
  const el = document.getElementById('stepContainer');
  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">Google (Optional)</h2>
      <p class="step-desc">Connect Gmail, Drive, Sheets, Docs, Calendar, and YouTube Music.</p>
      <div class="checklist">
        <div class="check-row">&#10003; Read and send Gmail</div>
        <div class="check-row">&#10003; Google Drive file access</div>
        <div class="check-row">&#10003; Google Sheets read/write</div>
        <div class="check-row">&#10003; Google Docs create/edit</div>
        <div class="check-row">&#10003; Google Calendar events</div>
        <div class="check-row">&#10003; YouTube Music playback</div>
      </div>
      <ol class="muted" style="line-height:1.8">
        <li>Go to console.cloud.google.com</li>
        <li>Create a project</li>
        <li>Enable these APIs: Gmail, Drive, Sheets, Docs, Calendar</li>
        <li>Create OAuth 2.0 credentials (Desktop app type)</li>
        <li>Download <code style="display:inline;padding:2px 6px">credentials.json</code> and place it in <code style="display:inline;padding:2px 6px">credentials/</code></li>
      </ol>
      <button class="btn btn-cyan btn-sm" onclick="checkGoogleCreds()">Check for credentials.json</button>
      <div id="googleCredsStatus" style="margin-top:10px"></div>
      <div id="googleAuthArea" class="hidden" style="margin-top:14px">
        <button class="btn btn-primary btn-sm" onclick="runGoogleAuth()">Run Google Auth &#8599;</button>
        <div id="googleAuthStatus" style="margin-top:10px"></div>
      </div>
      <p class="muted" style="margin-top:16px">You can complete Google setup later by running: <code style="display:inline;padding:2px 6px">python3 credentials/setup_gmail_oauth.py</code></p>
    </div>
    ${navRow()}
  `;
  checkGoogleCreds(true);
}

async function checkGoogleCreds(silent) {
  const r = await api('/setup/api/check_creds');
  state.google_creds_found = r.found;
  const el = document.getElementById('googleCredsStatus');
  const authArea = document.getElementById('googleAuthArea');
  if (el) el.innerHTML = r.found ? badge('ok', 'credentials/credentials.json') : badge(silent ? 'none' : 'err', 'Place credentials.json in credentials/');
  if (authArea) authArea.classList.toggle('hidden', !r.found);
}

let googlePollInterval = null;

async function runGoogleAuth() {
  const el = document.getElementById('googleAuthStatus');
  el.innerHTML = '<span class="spinner"></span> Opening browser for OAuth consent — authorise all requested scopes for full functionality...';
  const r = await api('/setup/api/google_auth', { method: 'POST' });
  if (!r.ok) { el.innerHTML = badge('err', r.error || 'Failed to start auth flow'); return; }
  el.innerHTML = '<span class="spinner"></span> Waiting for you to complete authorisation in the new browser tab...';
  if (googlePollInterval) clearInterval(googlePollInterval);
  googlePollInterval = setInterval(async () => {
    const status = await api('/setup/api/google_status');
    if (status.authorized) {
      clearInterval(googlePollInterval);
      state.google_authorized = true;
      el.innerHTML = badge('ok', 'Authorised — Gmail, Drive, Sheets, Docs, Calendar');
    }
  }, 2000);
}

// ---------------------------------------------------------------------------
// Step: Sim Racing / Flight (optional)
// ---------------------------------------------------------------------------
function bridgePlatformNoteHTML() {
  if (PLATFORM.is_windows) {
    return `<p class="muted">You're setting up ShinAgent on Windows. The bridge scripts can run on the same machine as ShinAgent, or on a separate Windows gaming PC.</p>
      <p class="muted">Your server IP: <code style="display:inline;padding:2px 6px">${state.server_ip}</code> — use this IP when configuring SimHub or pointing bridge scripts at this machine.</p>`;
  }
  return `<p class="muted">Bridge scripts run on your Windows gaming PC, not here.</p>`;
}

function loadStepSimRace() {
  const el = document.getElementById('stepContainer');
  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">Sim Racing / Flight</h2>
      <p class="step-desc">Connect sim racing games and Microsoft Flight Simulator. All optional — enable or disable later in Settings.</p>
      ${bridgePlatformNoteHTML()}

      <div class="card-toggle">
        <div class="ct-body">
          <div class="ct-title">Assetto Corsa / ACC / AC EVO</div>
          <div class="ct-desc">Enable AC telemetry (port 8001). ${PLATFORM.is_windows ? 'Run this when playing (on this machine or your gaming PC)' : 'Run on your Windows PC when playing'} — auto-detects AC1, ACC, and AC EVO. SimHub not required for AC games.</div>
          <pre class="code">python windows/ac_bridge.py --host ${state.server_ip}</pre>
        </div>
        <label class="toggle"><input type="checkbox" ${state.ac_enabled?'checked':''} onchange="state.ac_enabled=this.checked"><span class="toggle-track"></span><span class="toggle-thumb"></span></label>
      </div>

      <div class="card-toggle">
        <div class="ct-body">
          <div class="ct-title">Microsoft Flight Simulator 2024</div>
          <div class="ct-desc">Enable MSFS telemetry (port 8002). Q2 can monitor AND control the aircraft via SimConnect.</div>
          <pre class="code">pip install SimConnect flask
python windows/msfs_bridge.py --host ${state.server_ip}</pre>
        </div>
        <label class="toggle"><input type="checkbox" ${state.msfs_enabled?'checked':''} onchange="state.msfs_enabled=this.checked"><span class="toggle-track"></span><span class="toggle-thumb"></span></label>
      </div>

      <div class="card-toggle">
        <div class="ct-body">
          <div class="ct-title">Forza Horizon</div>
          <div class="ct-desc">Enable Forza telemetry (port 8000). In SimHub: Game Config &gt; Telemetry &gt; UDP Forwarding.</div>
          <pre class="code">Target: ${state.server_ip}   Port: 8000</pre>
        </div>
        <label class="toggle"><input type="checkbox" ${state.forza_enabled?'checked':''} onchange="state.forza_enabled=this.checked"><span class="toggle-track"></span><span class="toggle-thumb"></span></label>
      </div>

      <p class="muted">These can all be enabled or disabled in Settings &gt; Race Engineer and Settings &gt; First Officer later.</p>
    </div>
    ${navRow()}
  `;
}

// ---------------------------------------------------------------------------
// Step: Elite Dangerous (optional)
// ---------------------------------------------------------------------------
function loadStepED() {
  const el = document.getElementById('stepContainer');
  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">Elite Dangerous</h2>
      <p class="step-desc">Connect the Elite Dangerous journal to ShinAgent for COVAS (Computer Onboard Voice Assist System) mode.</p>
      ${bridgePlatformNoteHTML()}

      <div class="card-toggle">
        <div class="ct-body">
          <div class="ct-title">ED Bridge</div>
          <div class="ct-desc">Enable ED telemetry (port 8003). Monitors jumps, scans, combat, trade, missions, ship status, fuel, hull, shields. ${PLATFORM.is_windows ? 'Run ed_bridge.py on this machine or your gaming PC.' : 'Run ed_bridge.py on your Windows gaming PC.'}</div>
          <pre class="code">pip install flask
python windows/ed_bridge.py --host ${state.server_ip}</pre>
        </div>
        <label class="toggle"><input type="checkbox" ${state.ed_enabled?'checked':''} onchange="state.ed_enabled=this.checked"><span class="toggle-track"></span><span class="toggle-thumb"></span></label>
      </div>

      <div class="card-toggle">
        <div class="ct-body">
          <div class="ct-title">INARA (galaxy search)</div>
          <div class="ct-desc">Free with an INARA account — inara.cz &gt; profile &gt; API key.</div>
        </div>
        <label class="toggle"><input type="checkbox" ${state.inara_enabled?'checked':''} onchange="toggleInara(this.checked)"><span class="toggle-track"></span><span class="toggle-thumb"></span></label>
      </div>
      <div id="inaraExtra" class="${state.inara_enabled?'':'hidden'}">
        <div class="card-toggle-extra">
          <input type="password" id="inaraKey" placeholder="INARA API Key" value="${state.api_keys.INARA_API_KEY}"
                 oninput="state.api_keys.INARA_API_KEY=this.value">
        </div>
      </div>

      <p class="muted" style="margin-top:16px">After setup, access the ED companion panel at:
        <code style="display:inline;padding:2px 6px">http://${state.server_ip}:8766/ed-companion</code></p>
    </div>
    ${navRow()}
  `;
}

function toggleInara(v) {
  state.inara_enabled = v;
  document.getElementById('inaraExtra').classList.toggle('hidden', !v);
}

// ---------------------------------------------------------------------------
// Step: ShinAgent HUD (optional)
// ---------------------------------------------------------------------------
async function loadStepHUD() {
  const el = document.getElementById('stepContainer');
  const hudIntro = PLATFORM.is_windows
    ? 'ShinAgent HUD runs on Windows. Install and launch:'
    : 'ShinAgent HUD is a desktop companion app for your Windows (or Linux) gaming PC that provides live telemetry, ACC setup management, bridge control, and retro gaming — all in one window. Install HUD dependencies on your Windows PC:';
  const hudLaunchCmd = PLATFORM.is_windows ? `python hud/hud.py --q2 localhost` : `python hud/hud.py --q2 ${state.server_ip}`;
  const launchNowBtn = PLATFORM.is_windows
    ? `<button class="btn btn-primary btn-sm" onclick="launchHudNow()">Launch HUD Now</button> <span id="hudLaunchStatus" style="margin-left:8px"></span>`
    : '';
  const retroDesc = PLATFORM.is_windows
    ? 'RetroArch and vgamepad can be set up on this machine. Requirements: RetroArch installed, Settings &gt; Network &gt; Network Commands: ON.'
    : 'Q2 can play as Player 2 in NES, SNES, and Genesis games via RetroArch. Requirements: RetroArch installed, Settings &gt; Network &gt; Network Commands: ON, and <code style="display:inline;padding:2px 6px">pip install vgamepad</code> (installs the ViGEmBus driver automatically).';

  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">Desktop HUD</h2>
      <p class="step-desc">${hudIntro}</p>

      <pre class="code">pip install pywebview flask flask-cors requests psutil</pre>
      <button class="btn btn-cyan btn-sm" onclick="checkHudDeps()">Check HUD Dependencies</button>
      <div id="hudDepsStatus" style="margin-top:10px"></div>

      <div class="row" style="margin-top:18px">
        <label class="field-label">Launch command</label>
        <div class="input-with-btn">
          <input type="text" readonly value="${hudLaunchCmd}" id="hudLaunchCmd">
          <button class="btn btn-ghost btn-sm" onclick="copyHudCommand()">Copy</button>
        </div>
        <div style="margin-top:10px">${launchNowBtn}</div>
      </div>

      <div class="card-toggle" style="margin-top:18px">
        <div class="ct-body">
          <div class="ct-title">Retro Gaming</div>
          <div class="ct-desc">${retroDesc}</div>
          <div style="margin-top:10px"><a class="btn btn-cyan btn-sm" href="https://www.retroarch.com" target="_blank" rel="noopener" style="text-decoration:none">Open retroarch.com &#8599;</a></div>
          ${PLATFORM.is_windows ? '<pre class="code" style="margin-top:8px">pip install vgamepad</pre>' : ''}
          <p class="muted" style="margin-top:8px">Place ROMs in <code style="display:inline;padding:2px 6px">~/ROMs/</code> — the HUD scans them automatically.</p>
        </div>
      </div>
    </div>
    ${navRow()}
  `;
  checkHudDeps(true);
}

async function launchHudNow() {
  const el = document.getElementById('hudLaunchStatus');
  el.innerHTML = '<span class="spinner"></span> Launching...';
  try {
    const r = await api('/setup/api/launch_hud', { method: 'POST' });
    el.innerHTML = r.ok ? badge('ok', r.message || 'HUD launched') : badge('err', r.error || 'Failed to launch HUD');
  } catch (e) {
    el.innerHTML = badge('err', String(e));
  }
}

async function checkHudDeps(silent) {
  const el = document.getElementById('hudDepsStatus');
  if (el && !silent) el.innerHTML = '<span class="spinner"></span> Checking...';
  const r = await api('/setup/api/check_hud', { method: 'POST' });
  state.hud_check = r;
  if (!el) return;
  const rows = Object.entries(r.packages).map(([name, ok]) => `<div class="check-row">${badge(ok ? 'ok' : 'none', name)}</div>`);
  el.innerHTML = rows.join('');
}

function copyHudCommand() {
  const el = document.getElementById('hudLaunchCmd');
  el.select();
  navigator.clipboard?.writeText(el.value).catch(() => {});
  showCopyToast();
}
function showCopyToast() {
  const btn = event.target;
  const orig = btn.textContent;
  btn.textContent = 'Copied!';
  setTimeout(() => { btn.textContent = orig; }, 1500);
}

// ---------------------------------------------------------------------------
// Step: Review and finish
// ---------------------------------------------------------------------------
function loadStepReview() {
  const el = document.getElementById('stepContainer');
  const backend = BACKENDS.find(b => b.id === state.llm_backend);
  const voiceLabel = VOICE_OPTIONS.flatMap(g => g.options).find(o => o[0] === state.tts_voice);

  const rows = [
    ['AI Backend', backend ? backend.label : state.llm_backend, true],
    ['Voice Input', state.input_device || 'not detected', !!state.input_device],
    ['Voice Output', state.output_device || 'not detected', !!state.output_device],
    ['TTS Voice', voiceLabel ? voiceLabel[1] : state.tts_voice, state.tts_tested],
    ['Wake Word', state.wake_word_enabled ? 'Enabled' : 'Disabled', state.wake_word_enabled],
    ['Google', state.google_authorized ? 'Authorised' : 'Not configured', state.google_authorized],
    ['Forza', state.forza_enabled ? `Enabled (port 8000)` : 'Not configured', state.forza_enabled],
    ['AC/ACC', state.ac_enabled ? `Enabled (port 8001)` : 'Not configured', state.ac_enabled],
    ['MSFS', state.msfs_enabled ? `Enabled (port 8002)` : 'Not configured', state.msfs_enabled],
    ['Elite Dangerous', state.ed_enabled ? `Enabled (port 8003)` : 'Not configured', state.ed_enabled],
    ['INARA', state.inara_enabled ? 'Key configured' : 'Not configured', state.inara_enabled],
    ['HUD', (state.hud_check && state.hud_check.hud_ready) ? 'pywebview installed' : 'Not detected', !!(state.hud_check && state.hud_check.hud_ready)],
    ['RetroArch', (state.hud_check && state.hud_check.retro_ready) ? 'vgamepad installed' : 'Not detected', !!(state.hud_check && state.hud_check.retro_ready)],
  ];
  const tableRows = rows.map(r => `<tr><td>${r[0]}</td><td>${badge(r[2] ? 'ok' : 'none', r[1])}</td></tr>`).join('');

  el.innerHTML = `
    <div class="card">
      <h2 class="step-title">Ready to Launch</h2>
      <p class="step-desc">Here's what will be configured:</p>
      <table class="summary-table">${tableRows}</table>

      <div class="row" style="margin-top:20px">
        <label class="field-label">Agent name</label>
        <input type="text" id="agentName" value="${state.agent_name}" oninput="state.agent_name=this.value">
        <p class="muted">This is what ShinAgent calls itself in conversation. You can change it later.</p>
      </div>

      <button class="btn btn-primary btn-full" style="padding:14px;font-size:1rem" onclick="finishSetup()">FINISH SETUP</button>
      <div id="finishArea" class="hidden" style="margin-top:16px">
        <div class="term-log" id="finishLog"></div>
      </div>
      <div id="successArea" class="hidden"></div>
    </div>
    <div class="nav-row" id="finishNav">
      <button class="btn btn-ghost" onclick="prevStep()">&larr; Back</button>
      <span></span>
    </div>
  `;
}

function logLine(id, text) {
  const el = document.getElementById(id);
  el.textContent += text + '\n';
  el.scrollTop = el.scrollHeight;
}

async function finishSetup() {
  document.getElementById('finishArea').classList.remove('hidden');
  document.getElementById('finishNav').classList.add('hidden');
  logLine('finishLog', 'Writing .env...');
  const payload = {
    api_keys: state.api_keys,
    llm_backend: state.llm_backend,
    gemini_model: state.gemini_model,
    input_device: state.input_device,
    output_device: state.output_device,
    tts_voice: state.tts_voice,
    wake_word_enabled: state.wake_word_enabled,
    agent_name: state.agent_name,
    forza_enabled: state.forza_enabled,
    ac_enabled: state.ac_enabled,
    msfs_enabled: state.msfs_enabled,
    ed_enabled: state.ed_enabled,
    inara_enabled: state.inara_enabled,
  };
  logLine('finishLog', 'Updating config.yaml...');
  const result = await api('/setup/api/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (result.ok) {
    (result.summary || []).forEach(line => logLine('finishLog', line));
    logLine('finishLog', 'SETUP COMPLETE');
    setTimeout(showSuccessScreen, 500);
  } else {
    logLine('finishLog', 'ERROR: ' + (result.error || 'setup did not complete cleanly.'));
  }
}

function showSuccessScreen() {
  document.getElementById('finishArea').classList.add('hidden');
  const el = document.getElementById('successArea');
  el.classList.remove('hidden');
  el.innerHTML = `
    <div class="success-screen">
      <div class="big">SHINAGENT IS READY</div>
      <div style="margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="startShinAgent()">Start ShinAgent</button>
        <a class="btn btn-cyan" href="http://${state.server_ip}:8766" target="_blank" style="text-decoration:none">Open Web App &#8599;</a>
      </div>
      <div id="startStatus" style="margin-top:14px"></div>
      <div class="quick-ref card" style="margin-top:24px;text-align:left">
        <pre class="code" style="background:transparent;border:none;padding:0">${PLATFORM.is_windows ? `Start:     python main.py --text
Web app:   http://localhost:8766
Settings:  http://localhost:8766/settings
HUD:       python hud/hud.py
Logs:      logs/imq2.log` : `Start:     bash scripts/q2_start.sh
Stop:      bash scripts/q2_stop.sh
Monitor:   tmux attach -t q2
Web app:   http://${state.server_ip}:8766
Settings:  http://${state.server_ip}:8766/settings
HUD:       python hud/hud.py --q2 ${state.server_ip}`}</pre>
      </div>
      ${PLATFORM.is_windows ? `<p class="muted" style="margin-top:12px">If the web app is not reachable from other devices, allow Python through Windows Defender Firewall: Settings &gt; Windows Security &gt; Firewall &gt; Allow an app.</p>` : ''}
      <p class="muted" style="margin-top:16px">This wizard will close automatically in a moment. <span class="skip-link" onclick="closeWizard()">Close now</span></p>
    </div>
  `;
  setTimeout(closeWizard, 30000);
}

let _wizardClosed = false;
function closeWizard() {
  if (_wizardClosed) return;
  _wizardClosed = true;
  fetch('/setup/api/shutdown', { method: 'POST' }).catch(() => {});
}

async function startShinAgent() {
  const el = document.getElementById('startStatus');
  el.innerHTML = PLATFORM.is_windows
    ? '<span class="spinner"></span> Starting ShinAgent in a new console window...'
    : '<span class="spinner"></span> Starting ShinAgent in a tmux session...';
  const manualHint = PLATFORM.is_windows ? 'python main.py --text' : 'bash scripts/q2_start.sh';
  try {
    const r = await api('/setup/api/launch', { method: 'POST' });
    el.innerHTML = r.ok
      ? badge('ok', `ShinAgent started. Open http://${state.server_ip}:8766`)
      : badge('err', r.error || `Failed to start — try ${manualHint} manually.`);
  } catch (e) {
    el.innerHTML = badge('warn', `The wizard is shutting down — run: ${manualHint}`);
  }
}

// ---------------------------------------------------------------------------
function renderStep() {
  const loaders = {
    system: loadStepSystem, llm: loadStepLLM, voice: loadStepVoice,
    google: loadStepGoogle, simrace: loadStepSimRace, ed: loadStepED,
    hud: loadStepHUD, review: loadStepReview,
  };
  loaders[STEP_META[state.step].key]();
}

async function init() {
  try {
    const r = await api('/setup/api/server_ip');
    state.server_ip = r.ip || 'localhost';
  } catch (e) {}
  try {
    PLATFORM = await api('/setup/api/platform');
  } catch (e) {}
  updatePlatformUI();
  renderSidebar();
  renderStep();
}
init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    port = 8080
    local_ip = _get_local_ip()
    print("")
    print("  ShinAgent Setup Wizard running.")
    print(f"  Open: http://{local_ip}:{port}/setup")
    print(f"  Or:   http://localhost:{port}/setup")
    print("")
    app.run(host="0.0.0.0", port=port, debug=False)
