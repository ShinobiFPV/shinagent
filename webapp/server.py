"""
IMQ2 Web App Server
A Flask API that lets any device on the local network (iPhone, tablet, etc.)
communicate with Q2 via text, camera photos, and voice PTT.

Endpoints:
  POST /chat          — text message → Q2 reply text
  POST /analyze       — image (base64 or multipart) + optional text → Q2 reply
  POST /transcribe    — audio blob → transcript text (via Deepgram STT)
  POST /tts           — text → WAV audio bytes (via Deepgram TTS, optional)
  GET  /              — serves the mobile web UI

Run from ~/imq2:
    source ~/.venv/bin/activate
    python webapp/server.py
"""

import base64
import io
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path so we can import IMQ2 modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

from config.loader import config
from core.agent import IMQ2Agent
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder=str(Path(__file__).parent), static_url_path="")
CORS(app)

# Single shared agent instance — same memory, tools, and personality as the
# voice/text modes. All three interfaces (voice, text, web) share one agent.
_agent: IMQ2Agent | None = None


def get_agent() -> IMQ2Agent:
    global _agent
    if _agent is None:
        log.info("Initialising IMQ2 agent for web app...")
        _agent = IMQ2Agent()
    return _agent


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent), "index.html")


@app.route("/chat", methods=["POST"])
def chat():
    """Text message → Q2 reply."""
    data = request.get_json(silent=True) or {}
    text = data.get("message", "").strip()
    if not text:
        return jsonify({"error": "Empty message"}), 400
    try:
        reply = get_agent().chat(text)
        return jsonify({"reply": reply})
    except Exception as e:
        log.error(f"/chat error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Image + optional text → Q2 reply using Claude Vision.
    Accepts either multipart/form-data (image file + text field) or
    application/json with base64 image_data and optional message.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    image_bytes = None
    media_type  = "image/jpeg"
    user_text   = "Please analyze this image."

    if request.content_type and "multipart" in request.content_type:
        if "image" in request.files:
            f = request.files["image"]
            image_bytes = f.read()
            media_type  = f.content_type or "image/jpeg"
        user_text = request.form.get("message", user_text)
    else:
        data = request.get_json(silent=True) or {}
        b64  = data.get("image_data", "")
        if b64:
            # Strip data URI prefix if present
            if "," in b64:
                header, b64 = b64.split(",", 1)
                if "jpeg" in header:  media_type = "image/jpeg"
                elif "png" in header: media_type = "image/png"
                elif "webp" in header:media_type = "image/webp"
            image_bytes = base64.b64decode(b64)
        user_text = data.get("message", user_text)

    if not image_bytes:
        return jsonify({"error": "No image provided"}), 400

    try:
        # Send image directly to Claude Vision for analysis, then pass the
        # result as context into the agent so Q2's memory and personality apply.
        vision_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }],
        )
        vision_text = vision_response.content[0].text

        # Feed the vision analysis into the agent as context so it's in memory
        combined = f"[Image analysis]: {vision_text}"
        reply = get_agent().chat(combined)

        return jsonify({"reply": reply, "vision_analysis": vision_text})
    except Exception as e:
        log.error(f"/analyze error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/transcribe", methods=["POST"])
def transcribe():
    """Audio blob (WebM/OGG/WAV from MediaRecorder) → transcript text."""
    from voice.pipeline import get_stt

    audio_data = request.data
    if not audio_data:
        f = request.files.get("audio")
        if f:
            audio_data = f.read()

    if not audio_data:
        return jsonify({"error": "No audio data"}), 400

    try:
        stt = get_stt()
        transcript = stt.transcribe(audio_data)
        return jsonify({"transcript": transcript})
    except Exception as e:
        log.error(f"/transcribe error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/tts", methods=["POST"])
def tts():
    """Text → WAV audio (optional — client can display text instead)."""
    from voice.pipeline import get_tts

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text"}), 400

    try:
        tts_engine = get_tts()
        audio_bytes = tts_engine.synthesize(text)
        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/wav",
            as_attachment=False,
        )
    except Exception as e:
        log.error(f"/tts error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


STATIC_DIR = Path(__file__).parent / "static"

@app.route("/static/<path:filename>")
def static_files(filename):
    from flask import send_from_directory
    return send_from_directory(str(STATIC_DIR), filename)


FACE_SERVER = "http://127.0.0.1:8765"

@app.route("/settings")
def settings_page():
    """Proxy the settings HTML from the face server, rewriting all API URLs."""
    try:
        import requests as req
        r = req.get(f"{FACE_SERVER}/settings.html", timeout=3)
        html = r.text

        # The settings page builds its BASE from a PORT constant:
        #   const PORT = 8765;
        #   const BASE = `http://127.0.0.1:${PORT}`;
        # Rewrite both so all API calls go through our /face-api/ proxy instead.
        html = html.replace(
            "const PORT = 8765;",
            "const PORT = '';"
        ).replace(
            "const BASE = `http://127.0.0.1:${PORT}`;",
            "const BASE = '/face-api';"
        ).replace(
            "http://127.0.0.1:8765",
            "/face-api"
        )

        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return (
            "<html><body style='background:#0a0a12;color:#ff2fb0;font-family:monospace;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            f"<div>Settings unavailable — is Q2 running with --face?<br><br>{e}</div></body></html>"
        ), 503


@app.route("/face-api/<path:subpath>", methods=["GET", "POST"])
def face_api_proxy(subpath):
    """
    Proxy API calls from the settings panel through to the face server.
    The settings panel JS calls /state, /settings, /ledger — we forward them.
    """
    try:
        import requests as req
        url = f"{FACE_SERVER}/{subpath}"
        if request.method == "POST":
            r = req.post(url, json=request.get_json(silent=True), timeout=5)
        else:
            r = req.get(url, timeout=5)
        return r.content, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/json")}
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/voice", methods=["GET", "POST"])
def voice_endpoint():
    """GET current TTS voice, POST to change it."""
    from config.loader import config as cfg
    if request.method == "GET":
        return jsonify({
            "current": cfg.get("voice.deepgram_tts.model", "aura-2-pluto-en"),
        })
    data = request.get_json(silent=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"ok": False, "error": "No model specified"}), 400
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).parent.parent / "config" / "config.yaml"
        with open(cfg_path) as f:
            raw = yaml.safe_load(f)
        raw.setdefault("voice", {}).setdefault("deepgram_tts", {})["model"] = model
        with open(cfg_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
        # Also update live config so next TTS call uses new voice without restart
        cfg.raw.setdefault("voice", {}).setdefault("deepgram_tts", {})["model"] = model
        log.info(f"TTS voice changed to {model}")
        return jsonify({"ok": True, "model": model})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/llm-switch", methods=["POST"])
def llm_switch():
    """Save LLM backend + model to config.yaml ready for restart."""
    import sys, os
    from pathlib import Path
    data    = request.get_json(silent=True) or {}
    backend = data.get("backend", "claude")
    model   = data.get("model", "")
    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "config" / "config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("llm", {})["backend"] = backend
        if model:
            cfg["llm"].setdefault(backend, {})["model"] = model
        with open(cfg_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        log.info(f"LLM switched to {backend}{'/'+model if model else ''}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/restart", methods=["POST"])
def restart():
    """Signal main.py to restart by writing a flag file."""
    from pathlib import Path
    flag = Path(__file__).parent.parent / ".restart_requested"
    flag.touch()
    log.info("Restart flag written — main.py will restart shortly.")
    return jsonify({"ok": True, "message": "Restarting Q2..."})


@app.route("/camera/stream")
def camera_stream():
    """MJPEG stream from the C920 — consumed by the web app live view."""
    try:
        from integrations.webcam import webcam
        if not webcam.is_running:
            webcam.start()
        from flask import Response, stream_with_context
        return Response(
            stream_with_context(webcam.stream_generator()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/camera/snapshot")
def camera_snapshot():
    """Single JPEG snapshot."""
    try:
        from integrations.webcam import webcam
        if not webcam.is_running:
            webcam.start()
            import time; time.sleep(0.5)
        jpeg = webcam.grab_jpeg()
        if not jpeg:
            return jsonify({"error": "No frame available"}), 503
        from flask import Response
        return Response(jpeg, mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/health")
def health():
    from config.loader import config as cfg
    return jsonify({
        "ok": True,
        "agent_loaded": _agent is not None,
        "llm_backend": cfg.get("llm.backend", "claude"),
    })


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

@app.route('/log-tail')
def log_tail():
    """Return last N lines of the Q2 log for the H9000 terminal face."""
    import subprocess
    n = request.args.get('n', 20, type=int)
    try:
        result = subprocess.run(
            ['tail', '-n', str(n), '/home/shinobi/imq2/logs/imq2.log'],
            capture_output=True, text=True, timeout=2
        )
        return result.stdout, 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    port = int(os.environ.get("WEBAPP_PORT", 8766))
    host = "0.0.0.0"  # bind to all interfaces so iPhone on same network can reach it

    log.info(f"Q2 Web App starting on http://{host}:{port}")
    log.info("Access from iPhone: http://<shinobi-ip>:8766")

    # Pre-load the agent so the first request isn't slow
    get_agent()

    app.run(host=host, port=port, debug=False, threaded=True)
