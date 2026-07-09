"""HUD Flask server -- provides API and serves the UI."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import requests


def create_app(args):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    CORS(app)

    # Demo mode: every route below that would otherwise proxy to a live Q2
    # or touch local game/bridge processes short-circuits to hud/demo_data.py
    # instead, so the full UI can be inspected with no Q2, game, or bridge
    # connection at all -- see hud/demo_data.py's module docstring.
    demo = {"enabled": getattr(args, "demo", False), "module": getattr(args, "module", "race_engineer")}

    Q2_BASE = f"http://{args.q2}:{args.q2_port}"  # webapp/server.py
    Q2_FACE = f"http://{args.q2}:8765"             # face/server.py

    # ── Proxy helpers ──────────────────────────────────────────

    def q2_get(path, **kwargs):
        try:
            r = requests.get(f"{Q2_BASE}{path}", timeout=3, **kwargs)
            return r.json()
        except Exception as e:
            return {"error": str(e), "ok": False}

    def q2_post(path, data, **kwargs):
        try:
            r = requests.post(f"{Q2_BASE}{path}", json=data, timeout=5, **kwargs)
            return r.json()
        except Exception as e:
            return {"error": str(e), "ok": False}

    def face_get(path):
        try:
            r = requests.get(f"{Q2_FACE}{path}", timeout=3)
            return r.json()
        except Exception as e:
            return {"error": str(e), "ok": False}

    def face_post(path, data=None, timeout=5):
        try:
            r = requests.post(f"{Q2_FACE}{path}", json=data or {}, timeout=timeout)
            return r.json()
        except Exception as e:
            return {"error": str(e), "ok": False}

    # ── Main page ──────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            q2_host=args.q2,
            q2_port=args.q2_port,
            is_windows=sys.platform == "win32",
        )

    # ── Q2 state proxy ─────────────────────────────────────────

    @app.route("/api/state")
    def get_state():
        """Complete Q2 state -- active module, telemetry, status."""
        if demo["enabled"]:
            from hud.demo_data import get_demo_state
            result = get_demo_state(demo["module"])
            result["q2_host"] = args.q2
            return jsonify(result)

        state = face_get("/state")
        settings = face_get("/settings")

        profile = settings.get("active_profile", "") if isinstance(settings, dict) else ""
        module = _profile_to_module(profile)

        connected = isinstance(state, dict) and "error" not in state
        telemetry = state.get("telemetry") if connected else None

        return jsonify({
            "ok": True,
            "connected": connected,
            "module": module,
            "profile": profile,
            "q2_host": args.q2,
            "q2_state": {
                "speaking": state.get("speaking", False) if connected else False,
                "listening": state.get("listening", False) if connected else False,
                "thinking": state.get("thinking", False) if connected else False,
            },
            "telemetry": telemetry,
            "face_style": state.get("face_style", 1) if connected else 1,
            "llm_backend": settings.get("llm_backend", "unknown") if isinstance(settings, dict) else "unknown",
        })

    @app.route("/api/q2/chat", methods=["POST"])
    def chat():
        """Send a message to Q2."""
        msg = (request.json or {}).get("message", "")
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "response": f'[DEMO MODE] Q2 not connected. You said: "{msg}"'})
        return jsonify(q2_post("/chat", {"message": msg}))

    # ── Telemetry proxies ──────────────────────────────────────
    #
    # face/server.py's /state already builds one unified, source-agnostic
    # telemetry dict (see _telemetry_status() there) preferring AC over
    # Forza when both are live -- there's no separate raw endpoint per
    # source on the Q2 side, so the per-source routes below just filter
    # that same combined object by its "source" field instead of proxying
    # to something that doesn't exist.

    @app.route("/api/telemetry/forza")
    def forza_telemetry():
        if demo["enabled"]:
            from hud.demo_data import get_demo_state
            t = get_demo_state(demo["module"])["telemetry"]
            active = bool(t and t.get("source") == "forza")
            return jsonify({"active": active, "telemetry": t if active else None})
        state = face_get("/state")
        t = state.get("telemetry") if isinstance(state, dict) else None
        if t and t.get("source") == "forza":
            return jsonify({"active": True, "telemetry": t})
        return jsonify({"active": False, "telemetry": None})

    @app.route("/api/telemetry/ac")
    def ac_telemetry():
        if demo["enabled"]:
            return jsonify({"active": False, "telemetry": None})  # no AC demo fixture
        state = face_get("/state")
        t = state.get("telemetry") if isinstance(state, dict) else None
        if t and t.get("source") == "ac":
            return jsonify({"active": True, "telemetry": t})
        return jsonify({"active": False, "telemetry": None})

    # ── FH6 location browser ────────────────────────────────────
    #
    # ForzaLocationSystem persists to disk (cache/fh6_landmarks.json +
    # data/fh6_maps/*.json), so unlike genuinely in-memory agent-process
    # state (see the Whiplash/BB/Circuit Builder proxy comments elsewhere
    # in this file), these routes are safe to read/write directly here --
    # BUT they must construct a *fresh* ForzaLocationSystem() per request
    # rather than use the module's cached get_location_system() singleton.
    # That singleton is loaded once and reused for the lifetime of
    # whichever process first calls it; since landmarks are actually
    # marked via voice in the agent's own process, this HUD server's copy
    # would go stale (never see new marks) if it cached the same instance
    # across requests instead of re-reading from disk every time.

    @app.route("/api/forza/locations")
    def forza_locations():
        from integrations.forza_location import ForzaLocationSystem
        source = request.args.get("source", "")
        region = request.args.get("region", "")
        ltype = request.args.get("type", "")
        search = request.args.get("q", "").lower()

        if demo["enabled"]:
            from hud.demo_data import get_demo_forza_locations
            lms = get_demo_forza_locations()
        else:
            lms = ForzaLocationSystem().list_landmarks(source, region, ltype)

        if search:
            lms = [lm for lm in lms if search in lm.get("name", "").lower()
                   or search in lm.get("region", "").lower()
                   or search in lm.get("notes", "").lower()]

        return jsonify({"landmarks": lms[:100], "total": len(lms)})

    @app.route("/api/forza/locations/summary")
    def forza_locations_summary():
        if demo["enabled"]:
            from hud.demo_data import get_demo_forza_summary
            return jsonify(get_demo_forza_summary())
        from integrations.forza_location import ForzaLocationSystem
        return jsonify(ForzaLocationSystem().import_summary())

    @app.route("/api/forza/locations/import", methods=["POST"])
    def forza_locations_import():
        if demo["enabled"]:
            return jsonify({"result": "[DEMO] Import simulated -- no real map file loaded."})
        from integrations.forza_location import ForzaLocationSystem
        data = request.get_json(silent=True) or {}
        file_path = data.get("file_path", "reload")
        loc = ForzaLocationSystem()
        result = loc.reload_all_sources() if file_path.strip().lower() == "reload" else loc.import_map_file(file_path)
        return jsonify({"result": result})

    @app.route("/api/forza/locations/nearby")
    def forza_locations_nearby():
        """Landmarks near the current position. Position comes from
        face/server.py's /state (the agent process's live telemetry
        cache) via face_get(), same as /api/telemetry/forza above --
        not a direct integrations.forza_telemetry call, which would read
        an empty, disconnected copy in this separate HUD process."""
        if demo["enabled"]:
            from hud.demo_data import get_demo_forza_nearby
            return jsonify(get_demo_forza_nearby())

        from integrations.forza_location import ForzaLocationSystem
        state = face_get("/state")
        t = state.get("telemetry") if isinstance(state, dict) else None
        if not t or t.get("source") != "forza":
            return jsonify({"nearby": []})

        radius = int(request.args.get("radius", 500))
        near = ForzaLocationSystem().nearby(t.get("pos_x", 0), t.get("pos_z", 0), radius)
        return jsonify({"nearby": near[:15]})

    @app.route("/api/forza/locations/export", methods=["POST"])
    def forza_locations_export():
        if demo["enabled"]:
            return jsonify({"result": "[DEMO] Export simulated -- no file written."})
        from integrations.forza_location import ForzaLocationSystem
        data = request.get_json(silent=True) or {}
        result = ForzaLocationSystem().export_personal(data.get("name", "my_fh6_map"))
        return jsonify({"result": result})

    @app.route("/api/telemetry/msfs")
    def msfs_telemetry():
        # Full raw flight data -- a dedicated route on Q2's webapp
        # (webapp/server.py's /msfs/state), unlike Forza/AC above, since
        # flight data doesn't fit the race-telemetry shape face/server.py
        # already normalises.
        if demo["enabled"]:
            from hud.demo_data import get_demo_state
            return jsonify(get_demo_state("first_officer")["telemetry"])
        return jsonify(q2_get("/msfs/state"))

    @app.route("/api/telemetry/ed")
    def ed_telemetry():
        if demo["enabled"]:
            from hud.demo_data import get_demo_ed_state
            return jsonify(get_demo_ed_state())
        return jsonify(q2_get("/ed/state"))

    # ── ACC setups proxy ───────────────────────────────────────

    @app.route("/api/acc/setups")
    def acc_setups():
        if demo["enabled"]:
            from hud.demo_data import get_demo_acc_setups
            return jsonify(get_demo_acc_setups())
        car = request.args.get("car", "")
        track = request.args.get("track", "")
        params = {}
        if car:
            params["car"] = car
        if track:
            params["track"] = track
        return jsonify(q2_get("/acc-setups/api/setups", params=params))

    @app.route("/api/acc/apply/<int:setup_id>", methods=["POST"])
    def acc_apply(setup_id):
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": "[DEMO] Apply simulated"})
        return jsonify(q2_post(f"/acc-setups/api/apply/{setup_id}", {}))

    @app.route("/api/acc/delete/<int:setup_id>", methods=["POST"])
    def acc_delete(setup_id):
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": "[DEMO] Delete simulated"})
        try:
            r = requests.delete(f"{Q2_BASE}/acc-setups/api/setups/{setup_id}",
                                 json={"delete_file": False}, timeout=5)
            return jsonify(r.json())
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    @app.route("/api/acc/generate", methods=["POST"])
    def acc_generate():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": "[DEMO] Setup generation simulated"})
        return jsonify(q2_post("/acc-setups/api/generate", request.json))

    # ── ED companion proxy ─────────────────────────────────────

    @app.route("/api/ed/state")
    def ed_state():
        if demo["enabled"]:
            from hud.demo_data import get_demo_ed_state
            return jsonify(get_demo_ed_state())
        return jsonify(q2_get("/ed/state"))

    @app.route("/api/ed/paste", methods=["POST"])
    def ed_paste():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": "[DEMO] Paste-to-interpret simulated"})
        return jsonify(q2_post("/ed/paste", request.json))

    @app.route("/api/ed/search", methods=["POST"])
    def ed_search():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "results": []})
        return jsonify(q2_post("/ed/search", request.json))

    # ── Pop-up video proxy ─────────────────────────────────────

    @app.route("/api/popup/state")
    def popup_state():
        if demo["enabled"]:
            from hud.demo_data import get_demo_popup_state
            return jsonify(get_demo_popup_state())
        return jsonify(q2_get("/popup/api/state"))

    @app.route("/api/popup/upcoming")
    def popup_upcoming():
        if demo["enabled"]:
            from hud.demo_data import get_demo_popup_state
            return jsonify({"upcoming": get_demo_popup_state()["upcoming"]})
        return jsonify(q2_get("/popup/api/upcoming"))

    @app.route("/api/popup/timestamp", methods=["POST"])
    def popup_timestamp():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": "[DEMO] Timestamp call-out simulated"})
        return jsonify(q2_post("/popup/api/timestamp", request.json))

    # ── Whiplash proxy ──────────────────────────────────────────

    @app.route("/api/whiplash/state")
    def whiplash_state():
        if demo["enabled"]:
            from hud.demo_data import get_demo_whiplash_state
            return jsonify(get_demo_whiplash_state())
        return jsonify(q2_get("/whiplash/state"))

    # ── Beavis and Butthead proxy ───────────────────────────────
    # Unlike most proxied routes above (which hit webapp/server.py, a
    # separate OS subprocess -- see main.py's subprocess.Popen), these hit
    # face/server.py instead. face/server.py runs as a background thread
    # *inside* the same process as the voice/text agent, so its
    # integrations.beavis_butthead singleton is the actual live session
    # state the agent's own tool-calling touches -- a separate subprocess
    # would only ever see its own empty copy of that module-level state.

    @app.route("/api/bb/candidates", methods=["POST"])
    def bb_candidates():
        if demo["enabled"]:
            from hud.demo_data import get_demo_bb_candidates
            return jsonify(get_demo_bb_candidates())
        return jsonify(face_post("/bb/candidates"))

    @app.route("/api/bb/select", methods=["POST"])
    def bb_select():
        if demo["enabled"]:
            from hud.demo_data import get_demo_bb_session
            return jsonify({"ok": True, "response": "Okay. Uh huh huh. Let's watch.", "session": get_demo_bb_session()})
        return jsonify(face_post("/bb/select", request.get_json(silent=True) or {}))

    @app.route("/api/bb/start_video", methods=["POST"])
    def bb_start_video():
        if demo["enabled"]:
            from hud.demo_data import get_demo_bb_session
            sess = get_demo_bb_session()
            return jsonify({"ok": True, "commentary": "Okay this is Pantera. Uh huh huh. HEAVY.",
                             "video": sess["current_video"], "q2_is": sess["q2_is"]})
        return jsonify(face_post("/bb/start_video"))

    @app.route("/api/bb/react", methods=["POST"])
    def bb_react():
        if demo["enabled"]:
            return jsonify({"ok": True, "commentary": "This is cool. Uh huh huh."})
        return jsonify(face_post("/bb/react", request.get_json(silent=True) or {}))

    @app.route("/api/bb/video_end", methods=["POST"])
    def bb_video_end():
        if demo["enabled"]:
            return jsonify({"ok": True, "commentary": "That rocked. Uh huh huh. That rocked."})
        return jsonify(face_post("/bb/video_end"))

    @app.route("/api/bb/next_video", methods=["POST"])
    def bb_next_video():
        # Actually advances session.current_idx (tools.beavis_butthead's
        # next_video()) -- unlike calling /api/bb/start_video again, which
        # would just re-announce the same still-current video.
        if demo["enabled"]:
            from hud.demo_data import get_demo_bb_session
            sess = get_demo_bb_session()
            return jsonify({"ok": True, "commentary": "Uh... okay. Michael Jackson. Thriller. Heh heh.",
                             "video": sess["current_video"], "q2_is": sess["q2_is"]})
        return jsonify(face_post("/bb/next_video"))

    @app.route("/api/bb/user_comment", methods=["POST"])
    def bb_user_comment():
        if demo["enabled"]:
            return jsonify({"ok": True, "reaction": "Yeah. Yeah it does. Uh huh huh."})
        return jsonify(face_post("/bb/user_comment", request.get_json(silent=True) or {}))

    @app.route("/api/bb/toggle_nice_guy", methods=["POST"])
    def bb_toggle_nice_guy():
        if demo["enabled"]:
            return jsonify({"ok": True, "nice_guy": True, "commentary": "Nice Guy mode engaged. Wonderful."})
        return jsonify(face_post("/bb/toggle_nice_guy"))

    @app.route("/api/bb/swap_chars", methods=["POST"])
    def bb_swap_chars():
        if demo["enabled"]:
            return jsonify({"ok": True, "q2_is": "beavis", "commentary": "Uh huh huh. Okay. I'm Beavis now."})
        return jsonify(face_post("/bb/swap_chars"))

    @app.route("/api/bb/replay", methods=["POST"])
    def bb_replay():
        if demo["enabled"]:
            return jsonify({"ok": True, "commentary": "Okay, that one's in the replay list. Uh huh huh."})
        return jsonify(face_post("/bb/replay", request.get_json(silent=True) or {}))

    @app.route("/api/bb/replay_list")
    def bb_replay_list():
        if demo["enabled"]:
            from hud.demo_data import get_demo_bb_replay_list
            return jsonify(get_demo_bb_replay_list())
        return jsonify(face_get("/bb/replay_list"))

    @app.route("/api/bb/session")
    def bb_session():
        if demo["enabled"]:
            from hud.demo_data import get_demo_bb_session
            return jsonify(get_demo_bb_session())
        return jsonify(face_get("/bb/session"))

    # ── Game Companion ───────────────────────────────────────────
    # Session/history are real agent-process singleton state (see
    # integrations/game_companion.py) -- same reasoning as the BB/Circuit
    # Builder proxy blocks above, so these go through face/server.py
    # rather than importing integrations.game_companion directly (which
    # would only ever see this separate process's own empty copy).

    @app.route("/api/game/session")
    def game_session():
        if demo["enabled"]:
            from hud.demo_data import get_demo_game_session
            return jsonify(get_demo_game_session())
        return jsonify(face_get("/game/session"))

    @app.route("/api/game/history")
    def game_history():
        if demo["enabled"]:
            from hud.demo_data import get_demo_game_history
            return jsonify(get_demo_game_history())
        return jsonify(face_get("/game/history"))

    # ── Circuit Builder ──────────────────────────────────────────
    # "Active project" is real agent-process singleton state (see
    # tools/circuit_builder.py's _active_project), so those two routes go
    # through face/server.py -- same reasoning as the BB proxy block
    # above. Saved-project listing/lookup and the static component
    # database, on the other hand, only ever touch disk or a static
    # dict -- no process-local singleton involved -- so those are served
    # directly here, same as this file already does for e.g. ACC setups.

    @app.route("/api/circuit/active")
    def circuit_active():
        if demo["enabled"]:
            from hud.demo_data import get_demo_circuit_project
            return jsonify({"project": get_demo_circuit_project()})
        return jsonify(face_get("/circuit/active"))

    @app.route("/api/circuit/load/<project_id>", methods=["POST"])
    def circuit_load(project_id):
        if demo["enabled"]:
            from hud.demo_data import get_demo_circuit_project
            return jsonify({"ok": True, "project": get_demo_circuit_project()})
        return jsonify(face_post("/circuit/load", {"project_id": project_id}))

    @app.route("/api/circuit/create", methods=["POST"])
    def circuit_create():
        if demo["enabled"]:
            from hud.demo_data import get_demo_circuit_project
            return jsonify({"ok": True, "project": get_demo_circuit_project()})
        return jsonify(face_post("/circuit/create", request.get_json(silent=True) or {}))

    @app.route("/api/circuit/projects")
    def circuit_projects():
        from hud.circuit_builder.circuit_model import list_projects
        return jsonify({"projects": list_projects()})

    @app.route("/api/circuit/project/<project_id>")
    def circuit_get(project_id):
        from hud.circuit_builder.circuit_model import CircuitProject
        proj = CircuitProject.load(project_id)
        if proj:
            return jsonify({"project": proj.to_dict()})
        return jsonify({"error": "Not found"}), 404

    @app.route("/api/circuit/components")
    def circuit_components():
        from hud.circuit_builder.component_db import search_components
        query = request.args.get("q", "")
        category = request.args.get("cat", "")
        return jsonify({"components": search_components(query, category or None)})

    @app.route("/api/circuit/component/<comp_id>")
    def circuit_component(comp_id):
        from hud.circuit_builder.component_db import get_component
        comp = get_component(comp_id)
        if comp:
            return jsonify({"component": comp})
        return jsonify({"error": "Not found"}), 404

    # ── F1 / UFC watchalong proxy ───────────────────────────────
    #
    # Both are served from face/server.py's GET /settings (not /state --
    # they're network calls to OpenF1/ESPN, deliberately kept off the hot
    # /state polling path; see _f1_status()/_ufc_status() there), so these
    # proxy to that instead of a nonexistent dedicated Q2 route.

    @app.route("/api/f1/state")
    def f1_state():
        if demo["enabled"]:
            from hud.demo_data import get_demo_f1_state
            return jsonify(get_demo_f1_state())
        settings = face_get("/settings")
        return jsonify(settings.get("f1_status", {"active": False}) if isinstance(settings, dict) else {"active": False})

    @app.route("/api/ufc/state")
    def ufc_state():
        if demo["enabled"]:
            from hud.demo_data import get_demo_ufc_state
            return jsonify(get_demo_ufc_state())
        settings = face_get("/settings")
        return jsonify(settings.get("ufc_status", {"active": False}) if isinstance(settings, dict) else {"active": False})

    # ── Stats Hub ────────────────────────────────────────────────
    #
    # F1/UFC/NBA/NHL all reuse the same one GET /settings call face/
    # server.py already computes per-request (f1_status/ufc_status/
    # nba_status/nhl_status) rather than a second, separate call per
    # sport. Formula Drift and X Games have no Q2-side dependency at all
    # (pure scrapers, no live game state to track) so those run directly
    # in this process instead of proxying.

    @app.route("/api/stats/<sport>")
    def get_stats(sport):
        if demo["enabled"]:
            from hud.demo_data import get_demo_stats
            return jsonify(get_demo_stats(sport))

        try:
            if sport == "formula_drift":
                from integrations.formula_drift_data import get_fd_client
                fd = get_fd_client()
                return jsonify({"standings": fd.get_standings(), "schedule": fd.get_schedule()})

            elif sport == "xgames":
                from integrations.xgames_data import get_xg_client
                xg = get_xg_client()
                return jsonify({"results": xg.get_results()})

            elif sport in ("f1", "ufc", "nba", "nhl", "nfl", "mlb"):
                settings = face_get("/settings")
                settings = settings if isinstance(settings, dict) else {}
                if sport == "f1":
                    return jsonify({"race_status": settings.get("f1_status", {"active": False}), "standings": []})
                if sport == "ufc":
                    ufc_status = settings.get("ufc_status", {"active": False})
                    return jsonify({
                        "event": {"name": ufc_status.get("event_name", ""), "venue": ufc_status.get("venue", ""), "date": ufc_status.get("date", "")},
                        "current_fight": {
                            "fighter1": ufc_status.get("fighter1", ""), "record1": ufc_status.get("record1", ""),
                            "fighter2": ufc_status.get("fighter2", ""), "record2": ufc_status.get("record2", ""),
                            "weight_class": ufc_status.get("weight_class", ""),
                        } if ufc_status.get("fighter1") else {},
                    })
                if sport == "nba":
                    return jsonify({"game_status": settings.get("nba_status", {"active": False})})
                if sport == "nhl":
                    return jsonify({"game_status": settings.get("nhl_status", {"active": False}), "recent_goals": []})
                if sport == "nfl":
                    return jsonify({"game_status": settings.get("nfl_status", {"active": False}), "recent_drives": []})
                if sport == "mlb":
                    return jsonify({"game_status": settings.get("mlb_status", {"active": False})})

            return jsonify({"error": f"Unknown sport: {sport}"})

        except Exception as e:
            return jsonify({"error": str(e), "sport": sport})

    # ── Retro gaming (Q2 as Player 2) ───────────────────────────
    #
    # RetroArch + vgamepad both run locally on this same machine (ViGEmBus
    # is a local Windows driver), so retro_manager.py/retro_ai.py are
    # imported directly here rather than proxied like the Q2-side routes
    # above. Only the LLM decision itself (/api/retro/decide) leaves this
    # process, proxying to Q2's webapp -> tools/retro_decide.py.

    @app.route("/api/retro/status")
    def retro_status():
        if demo["enabled"]:
            # Skipping the is_windows() check entirely in demo mode -- the
            # whole point is previewing this tab's layout on any dev
            # machine, Windows or not, without real RetroArch/vgamepad.
            from hud.demo_data import get_demo_retro_status
            return jsonify(get_demo_retro_status())
        from hud.bridge_manager import is_windows
        if not is_windows():
            return jsonify({
                "platform_warning": True,
                "message": "Retro gaming requires Windows and RetroArch.",
                "setup_url": "https://www.retroarch.com",
            })
        from hud.retro_manager import get_manager
        mgr = get_manager()
        status = mgr.get_status()
        status["platform_warning"] = False
        return jsonify(status)

    @app.route("/api/retro/games")
    def retro_games():
        if demo["enabled"]:
            from hud.demo_data import get_demo_retro_games
            result = get_demo_retro_games()
            system = request.args.get("system", "")
            if system:
                games = [g for g in result["games"] if g["system"] == system]
                result = {"games": games, "count": len(games)}
            return jsonify(result)
        from hud.retro_manager import get_manager
        mgr = get_manager()
        system = request.args.get("system", "")
        games = mgr.scan_roms()
        if system:
            games = [g for g in games if g["system"] == system]
        return jsonify({"games": games, "count": len(games)})

    @app.route("/api/retro/launch", methods=["POST"])
    def retro_launch():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "game": "Street Fighter II",
                             "system": "snes", "p2_available": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        data = request.json or {}
        rom_path = data.get("path", "")
        system = data.get("system", "")
        result = mgr.launch_game(rom_path, system)
        return jsonify(result)

    @app.route("/api/retro/ai/start", methods=["POST"])
    def retro_ai_start():
        if demo["enabled"]:
            data = request.json or {}
            return jsonify({"ok": True, "demo": True, "mode": data.get("mode", "hybrid"),
                             "aggression": float(data.get("aggression", 0.5)), "game": "Street Fighter II"})
        from hud.retro_manager import get_manager
        from hud.retro_ai import RetroAIController
        mgr = get_manager()
        data = request.json or {}
        mode = data.get("mode", "hybrid")
        aggression = float(data.get("aggression", 0.5))

        if not mgr.current_game:
            return jsonify({"ok": False, "error": "No game running"})

        if not mgr.p2.available:
            return jsonify({
                "ok": False,
                "error": "Virtual gamepad not available. "
                         "Install vgamepad: pip install vgamepad"
            })

        # q2_base points at THIS HUD server (127.0.0.1:<its own port>), not
        # Q2 directly -- retro_ai.py's LLM-mode calls go through this
        # process's own /api/retro/decide proxy below, matching every
        # other Q2-bound call in this file.
        ai = RetroAIController(mgr, q2_base=f"http://127.0.0.1:{args.port}",
                                ai_mode=mode, aggression=aggression)
        ai.start(mgr.current_game)
        mgr.ai_controller = ai

        return jsonify({
            "ok": True,
            "mode": mode,
            "aggression": aggression,
            "game": mgr.current_game,
        })

    @app.route("/api/retro/ai/stop", methods=["POST"])
    def retro_ai_stop():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        if mgr.ai_controller:
            mgr.ai_controller.stop()
            mgr.ai_controller = None
        return jsonify({"ok": True})

    @app.route("/api/retro/ai/config", methods=["POST"])
    def retro_ai_config():
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        data = request.json or {}
        if mgr.ai_controller:
            if "aggression" in data:
                mgr.ai_controller.set_aggression(data["aggression"])
            if "mode" in data:
                mgr.ai_controller.set_mode(data["mode"])
        return jsonify({"ok": True})

    @app.route("/api/retro/ai/state")
    def retro_ai_state():
        if demo["enabled"]:
            return jsonify({
                "active": True, "mode": "hybrid", "aggression": 0.5,
                "game_state": {"p1_health": 78, "p2_health": 92, "round": 2},
                "recent_actions": ["low_kick", "block", "hadouken", "jump_back"],
            })
        from hud.retro_manager import get_manager
        mgr = get_manager()
        ai = mgr.ai_controller
        if not ai:
            return jsonify({"active": False})
        return jsonify({
            "active": True,
            "mode": ai._ai_mode,
            "aggression": ai._aggression,
            "game_state": ai._last_state,
            "recent_actions": ai._action_history[-10:],
        })

    @app.route("/api/retro/p2/press", methods=["POST"])
    def retro_p2_press():
        """One-shot P2 button press from the HUD's manual control grid."""
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        data = request.json or {}
        button = data.get("button", "")
        duration = int(data.get("duration_ms", 100))
        if button:
            mgr.p2.press(button, duration)
        return jsonify({"ok": True})

    @app.route("/api/retro/p2/hold", methods=["POST"])
    def retro_p2_hold():
        """
        True press-and-hold for the manual D-pad (mousedown), using
        VirtualP2Controller's own hold()/release() rather than a
        fixed-duration press -- matters for platformers where held-vs-tapped
        jump height/run speed genuinely differs.
        """
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        data = request.json or {}
        button = data.get("button", "")
        if button:
            mgr.p2.hold(button)
        return jsonify({"ok": True})

    @app.route("/api/retro/p2/release", methods=["POST"])
    def retro_p2_release():
        """Release a button held via /api/retro/p2/hold (mouseup)."""
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        data = request.json or {}
        button = data.get("button", "")
        if button:
            mgr.p2.release(button)
        return jsonify({"ok": True})

    @app.route("/api/retro/control", methods=["POST"])
    def retro_control():
        """Send RetroArch control command."""
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True})
        from hud.retro_manager import get_manager
        mgr = get_manager()
        data = request.json or {}
        command = data.get("command", "")
        valid = ["PAUSE_TOGGLE", "SAVE_STATE", "LOAD_STATE", "RESET"]
        if command in valid:
            mgr.ra.send(command)
        return jsonify({"ok": True})

    @app.route("/api/retro/decide", methods=["POST"])
    def retro_decide():
        """Proxies to Q2's own /retro/decide -- see tools/retro_decide.py."""
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "action": "block"})
        return jsonify(q2_post("/retro/decide", request.json or {}))

    # ── Bridge manager ─────────────────────────────────────────

    @app.route("/api/bridges/status")
    def bridges_status():
        if demo["enabled"]:
            from hud.demo_data import get_demo_bridge_status
            return jsonify(get_demo_bridge_status())
        from hud.bridge_manager import get_bridge_status
        return jsonify(get_bridge_status())

    @app.route("/api/bridges/start/<bridge_name>", methods=["POST"])
    def start_bridge(bridge_name):
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": f"[DEMO] {bridge_name} start simulated"})
        from hud.bridge_manager import start_bridge as _start
        return jsonify(_start(bridge_name, args.q2))

    @app.route("/api/bridges/stop/<bridge_name>", methods=["POST"])
    def stop_bridge(bridge_name):
        if demo["enabled"]:
            return jsonify({"ok": True, "demo": True, "message": f"[DEMO] {bridge_name} stop simulated"})
        from hud.bridge_manager import stop_bridge as _stop
        return jsonify(_stop(bridge_name))

    # ── Game detector ──────────────────────────────────────────

    @app.route("/api/games/detected")
    def games_detected():
        if demo["enabled"]:
            from hud.demo_data import get_demo_games
            return jsonify(get_demo_games())
        from hud.game_detector import get_running_games
        return jsonify({"games": get_running_games()})

    # ── Demo mode ────────────────────────────────────────────────

    @app.route("/api/demo/switch", methods=["POST"])
    def demo_switch():
        if not demo["enabled"]:
            return jsonify({"ok": False, "error": "Not in demo mode"})
        demo["module"] = (request.json or {}).get("module", "race_engineer")
        return jsonify({"ok": True, "module": demo["module"]})

    # ── Open external URL ──────────────────────────────────────

    @app.route("/api/open-url", methods=["POST"])
    def open_url():
        import webbrowser
        url = (request.json or {}).get("url", "")
        if url.startswith("http"):
            webbrowser.open(url)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Invalid URL"})

    # ── Helpers ────────────────────────────────────────────────

    def _profile_to_module(profile: str) -> str:
        MAP = {
            "q2_default": "default",
            "q2_guest": "guest",
            "race_engineer": "race_engineer",
            "first_officer": "first_officer",
            "ship_computer": "ship_computer",
            "watchalong_live": "watchalong",
            "watchalong_replay": "watchalong",
            "popup_video": "popup_video",
        }
        for key, module in MAP.items():
            if key in profile:
                return module
        return "default"

    return app
