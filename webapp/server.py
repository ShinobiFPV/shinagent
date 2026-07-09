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
import time
from pathlib import Path

# Add project root to sys.path so we can import IMQ2 modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

from config.loader import config
from core.agent import IMQ2Agent
from core.llm import build_vision_message
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
    Image + optional text → Q2 reply, using whichever LLM backend is
    currently active (Claude/OpenAI/Ollama/Grok/GLM — see core/llm.py).
    Accepts either multipart/form-data (image file + text field) or
    application/json with base64 image_data and optional message.
    """
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
        # Send the image to whichever LLM backend is currently active, then
        # pass the result as context into the agent so Q2's memory and
        # personality apply. Never hardcode a provider here — the backend is
        # swappable at runtime (see core/llm.py get_llm_backend()).
        agent = get_agent()
        vision_response = agent.llm.complete(
            messages=[build_vision_message(image_bytes, media_type, user_text)]
        )
        vision_text = vision_response.text

        # Feed the vision analysis into the agent as context so it's in memory
        combined = f"[Image analysis]: {vision_text}"
        reply = agent.chat(combined)

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
    """
    Proxy the settings HTML from the face server as-is. The page itself
    detects whether it's being viewed directly on the face server's own
    port (8765, the Pi kiosk case) or through this proxy (any other port,
    e.g. this webapp's 8766) via location.port, and picks its API base
    path accordingly — so no HTML rewriting is needed here. (An earlier
    version of this route rewrote a `const PORT = 8765` pattern that
    settings.html no longer contains, which had made this proxy silently
    a no-op — the two settings surfaces had drifted out of sync.)
    """
    try:
        import requests as req
        r = req.get(f"{FACE_SERVER}/settings.html", timeout=3)
        return r.text, 200, {"Content-Type": "text/html; charset=utf-8"}
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


@app.route("/restart", methods=["POST"])
def restart():
    """Signal main.py to restart by writing a flag file."""
    from pathlib import Path
    flag = Path(__file__).parent.parent / ".restart_requested"
    flag.touch()
    log.info("Restart flag written — main.py will restart shortly.")
    return jsonify({"ok": True, "message": "Restarting Q2..."})


@app.route("/reload-personality", methods=["POST"])
def reload_personality():
    """
    Called by face/server.py's settings panel after a dial change or
    profile switch. This webapp runs as its own OS subprocess with its own
    IMQ2Agent/config singleton (see get_agent() above), so a settings
    change made against the main process's config never reaches this
    process's memory on its own — this re-reads config.yaml, the active
    profile, and config/personality_state.yaml from disk to catch up,
    without needing to restart the webapp.
    """
    try:
        get_agent().reload_personality()
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"/reload-personality error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/ed-companion")
def ed_companion_page():
    """Standalone Elite Dangerous Ship Computer companion panel."""
    return send_from_directory(str(Path(__file__).parent), "ed_companion.html")


# Last response Q2 gave to an /ed/paste or /ed/search call, surfaced to the
# companion app's "Q2 response" panel via /ed/state's polling loop.
_ed_last_response = {"text": "", "time": 0.0}


@app.route("/ed/state")
def ed_state():
    """Full Elite Dangerous game state for the companion app's 2s poll loop."""
    try:
        from integrations.ed_telemetry import get_snapshot, is_active
        return jsonify({
            "active": is_active(),
            "state": get_snapshot(),
            "last_q2_response": _ed_last_response["text"],
            "last_response_time": _ed_last_response["time"],
        })
    except Exception as e:
        log.error(f"/ed/state error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/msfs/state")
def msfs_state():
    """
    Full raw MSFS telemetry snapshot (altitude/airspeed/heading/autopilot/
    fuel/etc) for external dashboards — same shape as /ed/state above.
    face/server.py's /state only carries a cheap active-flag + aircraft
    name for the kiosk display (see _msfs_status() there); this route is
    the one place the full flight-data dict is exposed over HTTP, since
    First Officer mode never got its own dedicated companion page.
    """
    try:
        from integrations.msfs_telemetry import get_snapshot, is_active
        return jsonify({"active": is_active(), "state": get_snapshot()})
    except Exception as e:
        log.error(f"/msfs/state error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/whiplash/state")
def whiplash_state():
    """Full Whiplash metronome/groove/MIDI/Clone Hero state for the HUD's
    Whiplash panel poll loop -- same shape idea as /ed/state above."""
    try:
        from integrations.whiplash import get_metronome, get_session, score_hits, FUNK_GROOVES
        from integrations.whiplash_midi import get_listener

        metronome = get_metronome()
        session = get_session()
        listener = get_listener()
        midi = listener.snapshot()
        groove = FUNK_GROOVES.get(session.current_groove) if session.active else None

        stats = {"count": 0}
        if metronome.is_synced():
            hits = listener.get_recent_hits(since=metronome.synced_at)
            stats = score_hits(metronome, hits)

        return jsonify({
            "metronome": {
                "running": metronome.running,
                "bpm": round(metronome.bpm),
                "synced": metronome.is_synced(),
            },
            "groove": {
                "active": session.active,
                "name": groove["name"] if groove else "",
                "artist_credit": groove["artist_credit"] if groove else "",
            },
            "midi": midi,
            "timing_stats": stats,
            "clone_hero": {
                "artist": session.clone_hero_artist,
                "song": session.clone_hero_song,
            },
        })
    except Exception as e:
        log.error(f"/whiplash/state error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/retro/decide", methods=["POST"])
def retro_decide():
    """
    One-shot LLM decision for the ShinAgent HUD's retro gaming module
    (hud/retro_ai.py's Player 2 AI, "llm"/"hybrid" modes). Deliberately a
    thin wrapper around tools/retro_decide.py's direct LLM call rather than
    IMQ2Agent.chat() -- see that module's docstring for why routing this
    through the conversational agent would be wrong.
    """
    data = request.get_json(silent=True) or {}
    try:
        from tools.retro_decide import decide_retro_action
        result = decide_retro_action(
            game=data.get("game", ""),
            system=data.get("system", ""),
            state=data.get("state", {}) or {},
            aggression=float(data.get("aggression", 0.5)),
            buttons=data.get("buttons", []),
            recent_actions=data.get("recent_actions", []),
        )
        return jsonify(result)
    except Exception as e:
        log.error(f"/retro/decide error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/ed/paste", methods=["POST"])
def ed_paste():
    """Commander pastes text from the game UI — Q2 interprets it and replies."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    try:
        reply = get_agent().chat(f"[Pasted from Elite Dangerous]: {text}")
        _ed_last_response["text"] = reply
        _ed_last_response["time"] = time.time()
        return jsonify({"ok": True, "response": reply})
    except Exception as e:
        log.error(f"/ed/paste error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/ed/search", methods=["POST"])
def ed_search():
    """Galaxy search (INARA/EDSM) from the companion app's search box."""
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "No query provided"}), 400
    try:
        from tools.ship_computer import search_galaxy
        result = search_galaxy(query)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        log.error(f"/ed/search error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


def _acc_companion_base() -> str:
    host = config.get("acc_setups.companion_host", "192.168.1.101")
    port = config.get("acc_setups.companion_port", 8092)
    return f"http://{host}:{port}"


@app.route("/acc-setups")
def acc_setups_page():
    """ACC Setup Manager companion panel."""
    return send_from_directory(str(Path(__file__).parent), "acc_setups.html")


@app.route("/acc-setups/api/status")
def acc_setups_status():
    """Proxy to windows/acc_setup_manager.py's /status."""
    try:
        import requests as req
        r = req.get(f"{_acc_companion_base()}/status", timeout=5)
        return r.content, r.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/acc-setups/api/setups")
def acc_setups_list():
    """Proxy to windows/acc_setup_manager.py's /setups, forwarding query params (car/track/session_type)."""
    try:
        import requests as req
        r = req.get(f"{_acc_companion_base()}/setups", params=request.args, timeout=10)
        return r.content, r.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/acc-setups/api/setups/<int:setup_id>", methods=["DELETE"])
def acc_setups_delete(setup_id):
    """Proxy to windows/acc_setup_manager.py's DELETE /setups/<id>."""
    try:
        import requests as req
        r = req.delete(
            f"{_acc_companion_base()}/setups/{setup_id}",
            json=request.get_json(silent=True) or {},
            timeout=10,
        )
        return r.content, r.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/acc-setups/api/setups/<int:setup_id>", methods=["PUT"])
def acc_setups_update(setup_id):
    """Proxy to windows/acc_setup_manager.py's PUT /setups/<id> (rename, notes, favourite)."""
    try:
        import requests as req
        r = req.put(
            f"{_acc_companion_base()}/setups/{setup_id}",
            json=request.get_json(silent=True) or {},
            timeout=10,
        )
        return r.content, r.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/acc-setups/api/apply/<int:setup_id>", methods=["POST"])
def acc_setups_apply(setup_id):
    """Proxy to windows/acc_setup_manager.py's POST /setups/<id>/apply."""
    try:
        import requests as req
        r = req.post(f"{_acc_companion_base()}/setups/{setup_id}/apply", timeout=10)
        return r.content, r.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/acc-setups/api/generate", methods=["POST"])
def acc_setups_generate():
    """
    Calls generate_acc_setup() directly in-process (research + LLM call +
    save + apply all happen here, synchronously) rather than proxying —
    unlike the other /acc-setups/api/* routes, there's no companion-app
    endpoint that does the generation itself, only /setups and /apply.
    """
    data = request.get_json(silent=True) or {}
    car = (data.get("car") or "").strip()
    track = (data.get("track") or "").strip()
    if not car or not track:
        return jsonify({"ok": False, "error": "car and track are required"}), 400
    try:
        from tools.acc_setup_generator import generate_acc_setup
        message = generate_acc_setup(
            car=car, track=track,
            session_type=data.get("session_type", "sprint"),
            weather=data.get("weather", "dry"),
            ambient_temp=int(data.get("ambient_temp", 22)),
            track_temp=int(data.get("track_temp", 28)),
            notes=data.get("notes", ""),
        )
        ok = not message.startswith("[generate_acc_setup]")
        return jsonify({"ok": ok, "message": message} if ok else {"ok": False, "error": message})
    except Exception as e:
        log.error(f"/acc-setups/api/generate error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/popup-companion")
def popup_companion_page():
    """Standalone Pop-Up Video companion panel (MTV Pop Up Video style bubbles)."""
    return send_from_directory(str(Path(__file__).parent), "popup_companion.html")


# Last popup Q2 delivered via get_popup() (voice/text turn), surfaced to the
# companion panel's poll loop so the bubble appears there too — same bridge
# pattern as _ed_last_response above.
_popup_last_delivered = {"popup": None, "title": "", "year": None, "time": 0.0}


@app.route("/popup/api/state")
def popup_state():
    """Current session summary + last delivered popup + saved titles, for the companion's 2s poll."""
    try:
        from tools.popup_video import get_active_session, library
        session = get_active_session()
        return jsonify({
            "active": session is not None,
            "title": session.title if session else "",
            "year": session.year if session else None,
            "popup_count": len(session.popups) if session else 0,
            "current_ts": session.current_ts if session else 0,
            "last_delivered": _popup_last_delivered["popup"],
            "last_delivered_time": _popup_last_delivered["time"],
            "saved_titles": library.list_titles(),
        })
    except Exception as e:
        log.error(f"/popup/api/state error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/popup/api/deliver", methods=["POST"])
def popup_deliver():
    """Q2 pushes here (from tools/popup_video.py's get_popup) whenever a bubble fires."""
    data = request.get_json(silent=True) or {}
    popup = data.get("popup")
    if not popup:
        return jsonify({"ok": False, "error": "No popup provided"}), 400
    _popup_last_delivered["popup"] = popup
    _popup_last_delivered["title"] = data.get("title", "")
    _popup_last_delivered["year"] = data.get("year")
    _popup_last_delivered["time"] = time.time()
    return jsonify({"ok": True})


@app.route("/popup/api/session")
def popup_session():
    """Full active session data (title, popup list) for the companion panel."""
    try:
        from tools.popup_video import get_active_session
        session = get_active_session()
        if session is None:
            return jsonify({"active": False})
        return jsonify({"active": True, **session.to_dict()})
    except Exception as e:
        log.error(f"/popup/api/session error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/popup/api/timestamp", methods=["POST"])
def popup_timestamp():
    """Companion panel's manual timestamp lookup — mirrors get_popup()."""
    data = request.get_json(silent=True) or {}
    timestamp = (data.get("timestamp") or "").strip()
    if not timestamp:
        return jsonify({"ok": False, "error": "No timestamp provided"}), 400
    try:
        from tools.popup_video import get_popup
        message = get_popup(timestamp)
        ok = not message.startswith("[get_popup]")
        return jsonify({"ok": ok, "message": message} if ok else {"ok": False, "error": message})
    except Exception as e:
        log.error(f"/popup/api/timestamp error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/popup/api/upcoming")
def popup_upcoming():
    """Next 3 upcoming pop-ups from the active session's current position."""
    try:
        from tools.popup_video import get_active_session
        session = get_active_session()
        if session is None:
            return jsonify({"active": False, "upcoming": []})
        upcoming = session.get_upcoming(session.current_ts, count=3)
        return jsonify({"active": True, "upcoming": upcoming})
    except Exception as e:
        log.error(f"/popup/api/upcoming error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/popup/api/generate", methods=["POST"])
def popup_generate():
    """
    Calls generate_popups() directly in-process (web research + LLM call +
    save all happen here, synchronously) — same pattern as
    /acc-setups/api/generate, since there's no separate companion app doing
    the generation itself.
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title is required"}), 400
    try:
        from tools.popup_video import generate_popups
        year = data.get("year")
        message = generate_popups(
            title=title,
            year=int(year) if year else None,
            episode=data.get("episode") or None,
        )
        ok = not message.startswith("[generate_popups]")
        return jsonify({"ok": ok, "message": message} if ok else {"ok": False, "error": message})
    except Exception as e:
        log.error(f"/popup/api/generate error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/camera/stream")
def camera_stream():
    """MJPEG stream from the C920 — consumed by the web app live view."""
    try:
        from integrations.webcam import webcam
        if not webcam.is_running and not webcam.start():
            return jsonify({"error": "Webcam unavailable"}), 503
        from flask import Response, stream_with_context

        def gen():
            # Release the camera once the client disconnects (image src
            # cleared, panel closed, tab closed) instead of leaving it
            # capturing indefinitely in the background.
            try:
                yield from webcam.stream_generator()
            finally:
                webcam.stop()

        return Response(
            stream_with_context(gen()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/camera/snapshot")
def camera_snapshot():
    """Single JPEG snapshot. Also used server-side by tools/photo_tools.py."""
    try:
        from integrations.webcam import webcam
        was_running = webcam.is_running
        if not was_running and not webcam.start():
            return jsonify({"error": "Webcam unavailable"}), 503
        if not was_running:
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
    n = request.args.get('n', 20, type=int)
    try:
        log_file = config.get("logging.file")
        if not log_file:
            return "", 200, {'Content-Type': 'text/plain'}
        log_path = PROJECT_ROOT / log_file
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]), 200, {'Content-Type': 'text/plain'}
    except FileNotFoundError:
        return "", 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    port = int(os.environ.get("WEBAPP_PORT", 8766))
    host = "0.0.0.0"  # bind to all interfaces so iPhone on same network can reach it

    log.info(f"Q2 Web App starting on http://{host}:{port}")
    log.info("Access from iPhone: http://<your-pi-ip>:8766")

    # Pre-load the agent so the first request isn't slow
    get_agent()

    app.run(host=host, port=port, debug=False, threaded=True)
