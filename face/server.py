"""
IMQ2 Face Server
A minimal local HTTP server (stdlib only, no new dependencies) that serves
the kiosk-mode waveform face, exposes the state polling endpoint, and
provides a settings panel at /settings.html with GET/POST /settings for
runtime configuration — wake word, visualizer colours, profile, output
device, talk button key, and wake word sensitivity.

Runs in a background thread inside the main process.
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from config.loader import config, PROFILES_DIR

log = logging.getLogger(__name__)

FACE_DIR = Path(__file__).parent
INDEX_PATH = FACE_DIR / "index.html"
SETTINGS_PATH = FACE_DIR / "settings.html"
RESTART_FLAG = FACE_DIR.parent / ".restart_requested"


def _msfs_status() -> tuple[bool, str]:
    """
    (is_active, aircraft_title) for MSFS telemetry. Soft-imported since
    integrations/msfs_telemetry.py only matters once windows/msfs_bridge.py
    is actually sending to this box. Cheap enough (an in-memory time
    comparison, plus a locked dict copy only when active) to call from
    /state's hot polling path as well as /settings.
    """
    try:
        from integrations import msfs_telemetry
        active = msfs_telemetry.is_active()
        aircraft = ""
        if active:
            snap = msfs_telemetry.get_snapshot() or {}
            aircraft = snap.get("aircraft_title", "")
        return active, aircraft
    except Exception:
        return False, ""


def _ed_status() -> dict:
    """
    Live connection status for the ED Ship Computer section's status dot —
    same cheap in-memory soft-import pattern as _msfs_status() above, since
    ed_telemetry only matters once windows/ed_bridge.py is actually sending
    to this box.
    """
    try:
        from integrations import ed_telemetry
        active = ed_telemetry.is_active()
        if not active:
            return {"active": False, "commander": "", "ship": "", "system": ""}
        snap = ed_telemetry.get_snapshot()
        return {
            "active": True,
            "commander": snap.get("commander") or "",
            "ship": snap.get("ship") or "",
            "system": (snap.get("location") or {}).get("system") or "",
        }
    except Exception:
        return {"active": False, "commander": "", "ship": "", "system": ""}


def _load_vernacular_state() -> dict:
    """Soft-imported like _ed_status()/_msfs_status() above — personality.vernacular
    is a small, dependency-free module, but this keeps the same defensive
    pattern so a bug there can never break the whole /settings GET."""
    try:
        from personality.vernacular import load_vernacular
        return load_vernacular()
    except Exception:
        return {"enabled": False}


def _telemetry_status() -> Optional[dict]:
    """
    Unified race-telemetry snapshot for the KITT face style's instrument
    readouts. Soft-imported and fully try/except-wrapped like _msfs_status()
    above — both integrations are cheap in-memory checks (UDP listeners
    already running in the background), so this is safe on the hot /state
    path. Prefers AC over Forza when both are live since AC exposes more
    fields (tyre wear, fuel-estimated-laps, flag, pit status).
    """
    try:
        from integrations import forza_telemetry, ac_telemetry

        if ac_telemetry.is_active():
            d = ac_telemetry.get_snapshot()
            if not d:
                return None
            from integrations.ac_telemetry import AC_FLAG
            return {
                "source": "ac",
                "speed_kmh": d["speed_kmh"],
                "gear": d["gear"] - 1,  # AC: 0=R,1=N,2+=g-1 -> normalize to 0=N like Forza
                "rpm": round(d["rpm"]),
                "rpm_max": round(d["max_rpm"]),
                "fuel_pct": None,  # AC's packet has no max-fuel field to compute a %
                "fuel_laps": d["fuel_estimated_laps"],
                "tyre_temp_fl": d["tyre_temp_fl"], "tyre_temp_fr": d["tyre_temp_fr"],
                "tyre_temp_rl": d["tyre_temp_rl"], "tyre_temp_rr": d["tyre_temp_rr"],
                "tyre_wear_fl": d["tyre_wear_fl"], "tyre_wear_fr": d["tyre_wear_fr"],
                "tyre_wear_rl": d["tyre_wear_rl"], "tyre_wear_rr": d["tyre_wear_rr"],
                "lap": d["completed_laps"],
                "position": d["position"],
                "last_lap_ms": d["last_lap_ms"],
                "best_lap_ms": d["best_lap_ms"],
                "current_lap_ms": d["current_lap_ms"],
                "flag": AC_FLAG.get(d["flag"], "None"),
                "is_in_pit": bool(d["is_in_pit"]),
                "compound": None,  # not captured by AC's telemetry struct
            }
        if forza_telemetry.is_active():
            d = forza_telemetry.get_snapshot()
            if not d:
                return None
            drift = d.get("drift", {}) or {}
            air = d.get("air", {}) or {}
            location = None
            if d.get("driving_mode") == "freeroam":
                try:
                    from integrations.forza_location import get_location_system
                    nearest = get_location_system().nearest(d.get("pos_x", 0), d.get("pos_z", 0))
                    if nearest and nearest["distance"] <= 150.0:
                        location = nearest["name"]
                except Exception:
                    pass
            return {
                "source": "forza",
                "speed_kmh": d["speed"] * 3.6,
                "gear": d["gear"],
                "rpm": round(d["current_engine_rpm"]),
                "rpm_max": round(d["engine_max_rpm"]),
                "fuel_pct": d["fuel"] * 100,
                "fuel_laps": None,  # Forza has no laps-remaining estimate
                "tyre_temp_fl": d["tire_temp_fl"], "tyre_temp_fr": d["tire_temp_fr"],
                "tyre_temp_rl": d["tire_temp_rl"], "tyre_temp_rr": d["tire_temp_rr"],
                "tyre_wear_fl": None, "tyre_wear_fr": None,
                "tyre_wear_rl": None, "tyre_wear_rr": None,
                "lap": d["lap_number"],
                "position": d["race_position"],
                "last_lap_ms": round(d["last_lap"] * 1000),
                "best_lap_ms": round(d["best_lap"] * 1000),
                "current_lap_ms": round(d["current_lap"] * 1000),
                "flag": None,  # Forza doesn't expose flag state
                "is_in_pit": False,
                "compound": None,
                "driving_mode": d.get("driving_mode", "menu"),
                "drifting": drift.get("drifting", False),
                "drift_angle": drift.get("drift_angle", 0),
                "peak_yaw_dps": drift.get("peak_yaw_dps", 0),
                "airborne": air.get("airborne", False),
                "location": location,
            }
        return None
    except Exception:
        return None


def _f1_status() -> dict:
    """
    Live-session summary for the WATCHALONG settings sub-section's
    read-only status block, plus the last 5 race control messages. Cheap
    soft-import/try-except, same as _msfs_status() above — OpenF1 being
    briefly unreachable must never break the settings GET.
    """
    try:
        from integrations.f1_watchalong import detect_live_session, _get
        session = detect_live_session()
        if not session:
            return {"active": False, "session_name": "", "race_control": []}
        rc = _get("race_control", cache_s=5, session_key=session["session_key"])
        recent = [r.get("message", "") for r in rc[-5:] if r.get("message")]
        return {
            "active": bool(session.get("is_live")),
            "session_name": f"{session.get('meeting_name', '?')} — {session.get('session_name', '?')}",
            "race_control": recent,
        }
    except Exception:
        return {"active": False, "session_name": "", "race_control": []}


def _ufc_status() -> dict:
    """
    Tonight's main event for the UFC WATCHALONG settings sub-section's
    read-only status block. Same soft-import/try-except pattern as
    _f1_status() — ESPN being briefly unreachable must never break the
    settings GET. Not included on the hot /state path for the same reason
    _f1_status() isn't (network call, not a cheap in-memory check).
    """
    try:
        from integrations.ufc_data import get_tonight_event
        event = get_tonight_event()
        if not event or not event.get("fights"):
            return {"active": False, "event_name": "", "main_event": ""}
        main = event["fights"][0]
        fighters = main["fighters"]
        names = " vs ".join(f["name"] for f in fighters)
        return {
            "active": bool(event.get("live")),
            "event_name": event.get("name", ""),
            "main_event": f"{names} — {main.get('weight_class', '?')}",
            # Raw structured fields (beyond the two formatted-for-display ones
            # above) for the HUD Stats Hub's own rendering.
            "venue": event.get("venue", ""),
            "date": event.get("date", ""),
            "weight_class": main.get("weight_class", "?"),
            "fighter1": fighters[0]["name"] if len(fighters) > 0 else "",
            "record1": fighters[0].get("record", "") if len(fighters) > 0 else "",
            "fighter2": fighters[1]["name"] if len(fighters) > 1 else "",
            "record2": fighters[1].get("record", "") if len(fighters) > 1 else "",
        }
    except Exception:
        return {"active": False, "event_name": "", "main_event": ""}


def _nba_status() -> dict:
    """
    Live NBA game status for the NBA WATCHALONG settings sub-section's
    read-only status block. Same soft-import/try-except pattern as
    _f1_status()/_ufc_status() -- BallDontLie being briefly unreachable
    must never break the settings GET. NBAWatchalong needs an explicit
    set_game() before get_status() returns anything (BallDontLie has no
    "give me whatever's live" endpoint the way OpenF1's session_key=latest
    does), so this activates one via detect_live_game() itself the same
    way _f1_status()/_ufc_status() detect their live session/event --
    this is a shared process-wide singleton, so it also primes the alert
    thread/analyst tools rather than conflicting with them.
    """
    try:
        from integrations.nba_data import get_nba
        nba = get_nba()
        if not nba._game_id:
            game = nba.detect_live_game()
            if game:
                nba.set_game(game.get("id"))
        status = nba.get_status()
        if not status.get("active"):
            return {"active": False, "game_name": ""}
        return {
            **status,  # raw fields (home_abbr/home_score/period_str/leading/margin/...) for the HUD Stats Hub
            "game_name": f"{status['away_team']} @ {status['home_team']} — {status['period_str']}",
            "score": f"{status['home_team']} {status['home_score']} - {status['away_team']} {status['away_score']}",
        }
    except Exception:
        return {"active": False, "game_name": ""}


def _nhl_status() -> dict:
    """Live NHL game status for the NHL WATCHALONG settings sub-section's
    read-only status block. Same pattern as _nba_status()."""
    try:
        from integrations.nhl_data import get_nhl
        nhl = get_nhl()
        if not nhl._game_id:
            game = nhl.detect_live_game()
            if game:
                nhl.set_game(game.get("id"))
        status = nhl.get_status()
        if not status.get("active"):
            return {"active": False, "game_name": ""}
        return {
            **status,  # raw fields (home_abbr/home_score/period_str/clock/leading/margin/...) for the HUD Stats Hub
            "game_name": f"{status['away_team']} @ {status['home_team']} — {status['period_str']}",
            "score": f"{status['home_team']} {status['home_score']} - {status['away_team']} {status['away_score']}",
        }
    except Exception:
        return {"active": False, "game_name": ""}


def _nfl_status() -> dict:
    """Live NFL game status for the NFL WATCHALONG settings sub-section's
    read-only status block. Same pattern as _nba_status()/_nhl_status()."""
    try:
        from integrations.nfl_data import get_nfl
        nfl = get_nfl()
        if not nfl._game_id:
            game = nfl.detect_live_game()
            if game:
                nfl.set_game(game.get("id"))
        status = nfl.get_status()
        if not status.get("active"):
            return {"active": False, "game_name": ""}
        return {
            **status,
            "game_name": f"{status['away_team']} @ {status['home_team']} — {status['period_str']}",
            "score": f"{status['home_team']} {status['home_score']} - {status['away_team']} {status['away_score']}",
        }
    except Exception:
        return {"active": False, "game_name": ""}


def _mlb_status() -> dict:
    """Live MLB game status for the MLB WATCHALONG settings sub-section's
    read-only status block. Same pattern as _nba_status()/_nhl_status()."""
    try:
        from integrations.mlb_data import get_mlb
        mlb = get_mlb()
        if not mlb._game_pk:
            game = mlb.detect_live_game()
            if game:
                mlb.set_game(game.get("gamePk"))
        status = mlb.get_status()
        if not status.get("active"):
            return {"active": False, "game_name": ""}
        return {
            **status,
            "game_name": f"{status['away_team']} @ {status['home_team']} — {status['inning_str']}",
            "score": f"{status['home_team']} {status['home_score']} - {status['away_team']} {status['away_score']}",
        }
    except Exception:
        return {"active": False, "game_name": ""}


def _dj_status() -> dict:
    """Current Radio DJ session status for the RADIO DJ settings
    sub-section. Unlike _f1_status()/_nba_status()/etc this is a cheap
    in-memory read (no network call) since RadioDJController just holds
    local session state -- still kept out of the hot /state path for
    consistency with the other watchalong-style status blocks, which all
    live in /settings instead."""
    try:
        from integrations.radio_dj import get_controller
        status = get_controller().get_status()
        if not status.get("active"):
            return {"active": False}
        return status
    except Exception:
        return {"active": False}


def _masterchef_status() -> dict:
    """Current MasterChef session status for the MASTERCHEF settings
    sub-section. Same cheap in-memory pattern as _dj_status() -- pure
    session-state read, no network call."""
    try:
        from integrations.masterchef import RECIPES, get_session
        session = get_session()
        if not session or not session.active:
            return {"active": False}
        recipe = RECIPES.get(session.current_dish)
        return {
            "active": True,
            "cuisine": session.cuisine,
            "menu": [RECIPES[d]["name"] for d in session.menu if d in RECIPES],
            "current_dish": recipe["name"] if recipe else session.current_dish,
            "current_step": session.current_step + 1 if recipe else 0,
            "total_steps": len(recipe["steps"]) if recipe else 0,
        }
    except Exception:
        return {"active": False}


def _whiplash_status() -> dict:
    """Current Whiplash metronome/groove/MIDI status for the WHIPLASH
    settings sub-section. Same cheap in-memory pattern as _dj_status()/
    _masterchef_status() -- pure session-state reads, no network call."""
    try:
        from integrations.whiplash import get_metronome, get_session, FUNK_GROOVES
        from integrations.whiplash_midi import get_listener

        metronome = get_metronome()
        session = get_session()
        midi = get_listener().snapshot()
        groove = FUNK_GROOVES.get(session.current_groove) if session.active else None

        return {
            "metronome_running": metronome.running,
            "bpm": round(metronome.bpm),
            "synced": metronome.is_synced(),
            "groove_active": session.active,
            "current_groove": groove["name"] if groove else "",
            "midi_available": midi["available"],
            "midi_connected": midi["running"],
            "midi_port": midi["port"],
            "clone_hero_song": session.clone_hero_song,
            "clone_hero_artist": session.clone_hero_artist,
        }
    except Exception:
        return {"metronome_running": False, "synced": False, "groove_active": False, "midi_available": False, "midi_connected": False}


def _query_mic_devices() -> list[dict]:
    """
    Live microphone list for the settings page, sourced from sounddevice
    rather than requiring config.yaml's voice.input_device_options to be
    hand-edited whenever hardware changes. Falls back to that static list
    if sounddevice isn't importable (e.g. no audio hardware/driver on this
    box) — same soft-dependency pattern as chromadb/headroom elsewhere.
    Normalizes both sources to {name, label, sample_rate} so the caller
    never needs to know which one it got.
    """
    try:
        import sounddevice as sd
        seen = set()
        out = []
        for dev in sd.query_devices():
            name = dev.get("name", "")
            if dev.get("max_input_channels", 0) > 0 and name and name not in seen:
                seen.add(name)
                out.append({
                    "name": name,
                    "label": name,
                    "sample_rate": int(dev.get("default_samplerate") or 48000),
                })
        if out:
            return out
    except Exception as e:
        log.debug(f"sounddevice mic query failed, falling back to config list: {e}")

    return [
        {
            "name": opt.get("name", ""),
            "label": opt.get("label", opt.get("name", "")),
            "sample_rate": opt.get("sample_rate", 48000),
        }
        for opt in config.get("voice.input_device_options", [])
    ]


# Agent Modes are full operational profiles (persona, tool permissions,
# focus) as opposed to the tone-only presets below. Hardcoded rather than
# scanned from personality/profiles/*.yaml's own "name" field, because
# q2_default.yaml and q2_guest.yaml both set name: "Q2" — scanning them
# produced two dropdown entries both labeled "Q2" with no way to tell
# them apart. These labels are the product-facing identity; the value is
# the exact agent.active_profile path config.yaml expects.
AGENT_MODES = [
    {"value": "profiles/q2_default.yaml",      "label": "Q2 (Default)"},
    {"value": "profiles/q2_guest.yaml",        "label": "Q2 Guest"},
    {"value": "profiles/first_officer.yaml",   "label": "First Officer (MSFS)"},
    {"value": "profiles/race_engineer.yaml",   "label": "Race Engineer (Sim Racing)"},
    {"value": "profiles/watchalong_live.yaml",   "label": "Watchalong -- Live"},
    {"value": "profiles/watchalong_replay.yaml", "label": "Watchalong -- Replay"},
    {"value": "profiles/popup_video.yaml",        "label": "Pop-Up Video"},
    {"value": "profiles/ship_computer.yaml",      "label": "Ship Computer (Elite Dangerous)"},
    {"value": "profiles/radio_dj.yaml",           "label": "Radio DJ"},
    {"value": "profiles/masterchef.yaml",         "label": "MasterChef"},
    {"value": "profiles/whiplash.yaml",           "label": "Whiplash"},
    {"value": "profiles/beavis_butthead.yaml",    "label": "Beavis & Butthead"},
    {"value": "profiles/circuit_builder.yaml",    "label": "Circuit Builder"},
    {"value": "profiles/game_companion.yaml",     "label": "Game Companion"},
    {"value": "profiles/game_show.yaml",          "label": "Game Show Host"},
]


# ---------------------------------------------------------------------------
# Face playback state (speaking / listening / envelope)
# ---------------------------------------------------------------------------

class _FaceState:
    """Thread-safe shared state between AudioIO.play() and the HTTP handler."""

    def __init__(self):
        self._lock = threading.Lock()
        self._speaking = False
        self._envelope: list[float] = []
        self._duration_s: float = 0.0
        self._started_at: float = 0.0
        self._listening = False
        self._thinking = False
        self._audio_level: float = 0.0

    def start_speaking(self, envelope: list[float], duration_s: float, start_at: Optional[float] = None):
        with self._lock:
            self._speaking = True
            self._envelope = envelope
            self._duration_s = duration_s
            self._started_at = start_at if start_at is not None else time.time()

    def stop_speaking(self):
        with self._lock:
            self._speaking = False

    def set_listening(self, is_listening: bool):
        with self._lock:
            self._listening = is_listening

    def set_thinking(self, is_thinking: bool):
        """Set while Q2 is processing/calling the LLM after recording stops."""
        with self._lock:
            self._thinking = is_thinking

    def set_audio_level(self, level: float):
        """Current VAD RMS level while recording — read by settings.html's
        live Energy Threshold meter via the /state endpoint."""
        with self._lock:
            self._audio_level = level

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "speaking": self._speaking,
                "envelope": self._envelope,
                "duration_s": self._duration_s,
                "started_at": self._started_at,
                "listening": self._listening,
                "thinking": self._thinking,
                "audio_level": self._audio_level,
            }


face_state = _FaceState()


# ---------------------------------------------------------------------------
# Controller events — pushed by voice/talk_button.py's ControllerBridge
# (running in main.py's process, same as this server), polled by the web
# app / settings page for the D-pad nav overlay and live button-test display.
# ---------------------------------------------------------------------------

from collections import deque as _deque

_controller_events: "_deque[dict]" = _deque(maxlen=30)


def emit_controller_event(event: dict):
    """Called by voice/talk_button.py's ControllerBridge when a controller
    action fires. Best-effort, in-memory only -- events older than the
    last 30 are dropped, and nothing here is persisted across restarts."""
    event = dict(event)
    event["ts"] = time.time()
    _controller_events.append(event)


# ---------------------------------------------------------------------------
# Game Show events -- pushed by tools/game_show.py (running in-process, same
# as voice/talk_button.py's controller events above), polled by the kiosk's
# game-show overlay (face/index.html). A separate deque/stream from
# _controller_events since game events carry a full state snapshot each
# time and are polled on their own cadence, only while the overlay is up.
# ---------------------------------------------------------------------------

_game_events: "_deque[dict]" = _deque(maxlen=20)


def emit_game_event(event: dict):
    event = dict(event)
    event["ts"] = time.time()
    _game_events.append(event)


# ---------------------------------------------------------------------------
# Settings state — runtime config readable/writable by the settings page
# ---------------------------------------------------------------------------

class _SettingsState:
    """
    Runtime settings bridge between the settings HTML page (via HTTP POST)
    and Q2's live config + voice loop. All mutations are thread-safe.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._restart_as_text_mode = False
        self._face_style = config.get("face.style", 1)
        # Pending changes for the voice loop to apply
        self._pending: dict = {}

    def get(self) -> dict:
        from config.loader import config
        from personality.dials import DIAL_BANDS
        from personality.builder import _resolve_profile_dials
        import subprocess

        # Fetch available sinks from pactl for the output device dropdown
        try:
            result = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True, text=True, timeout=3
            )
            sinks = [line.split("\t")[1] for line in result.stdout.strip().splitlines() if line]
        except Exception:
            sinks = []

        # Resolve current dial values from the active profile
        dials = _resolve_profile_dials(config.profile)
        dial_values = {name: getattr(dials, name) for name in DIAL_BANDS}
        dial_values["probability_narration"] = dials.probability_narration
        dial_values["wellness_checkins"] = dials.wellness_checkins

        # Split PRESETS into the 17 built-in CaptCanadaMan presets vs.
        # anything the user has saved via "Save to My Presets" — anything
        # that's neither a built-in nor the Q2/Q2_Guest agent-mode
        # baselines is, by construction, user-created (see
        # personality/presets.py's BUILTIN_PRESET_NAMES for why Q2/Q2_Guest
        # are excluded from both lists). Also collect each preset's actual
        # dial values so "Apply to dials" can populate the sliders purely
        # client-side without saving anything — the only thing that
        # persists a preset's values is the main Save button (which POSTs
        # "dials" the same way manual slider edits do).
        try:
            from personality.presets import PRESETS, BUILTIN_PRESET_NAMES
            builtin_presets = [n for n in BUILTIN_PRESET_NAMES if n in PRESETS]
            user_presets = [n for n in PRESETS if n not in BUILTIN_PRESET_NAMES and n not in ("Q2", "Q2_Guest")]
            preset_values = {}
            for pname in builtin_presets + user_presets:
                p = PRESETS[pname]
                vals = {name: getattr(p, name) for name in DIAL_BANDS}
                vals["probability_narration"] = p.probability_narration
                vals["wellness_checkins"] = p.wellness_checkins
                preset_values[pname] = vals
        except Exception:
            builtin_presets, user_presets, preset_values = [], [], {}

        # MSFS connection status for the First Officer section's live dot.
        msfs_active, msfs_aircraft = _msfs_status()

        # Active profile as the exact "profiles/x.yaml" path config.yaml
        # stores it as — matches the value used by the Agent Mode <select>
        # in AGENT_MODES above, so the dropdown reflects the real current
        # mode without a filename-vs-display-name mismatch.
        active_profile = config.get("agent.active_profile", "profiles/q2_default.yaml")

        with self._lock:
            return {
                "wake_word_enabled": config.get("voice.wake_word.enabled", False),
                "wake_word_sensitivity": config.get("voice.wake_word.sensitivity", 0.5),
                "vad_energy_thresh": config.get("voice.vad.energy_thresh", 300),
                "active_profile": active_profile,
                "llm_backend":    config.get("llm.backend", "claude"),
                "agent_modes": AGENT_MODES,
                "builtin_presets": builtin_presets,
                "user_presets": user_presets,
                "preset_values": preset_values,
                "output_device": config.get("voice.output_device", "pipewire"),
                "tts_voice": config.get("voice.deepgram_tts.model", "aura-2-zeus-en"),
                "face_style": config.get("face.style", 1),
                "available_sinks": sinks,
                "restart_as_text_mode": self._restart_as_text_mode,
                "dials": dial_values,
                "input_device": config.get("voice.input_device", "default"),
                "available_input_devices": _query_mic_devices(),
                "shipping": {
                    "preferred_services": config.get("purchasing.shipping.preferred_services",
                                                     ["Canada Post Xpresspost", "Canada Post"]),
                    "fallback": config.get("purchasing.shipping.fallback", "cheapest"),
                    "confirm_above_cad": config.get("purchasing.shipping.confirm_above_cad", 25.00),
                    "name": config.get("purchasing.shipping.address.name", ""),
                    "line1": config.get("purchasing.shipping.address.line1", ""),
                    "line2": config.get("purchasing.shipping.address.line2", ""),
                    "city": config.get("purchasing.shipping.address.city", ""),
                    "province": config.get("purchasing.shipping.address.province", ""),
                    "postal_code": config.get("purchasing.shipping.address.postal_code", ""),
                    "phone": config.get("purchasing.shipping.address.phone", ""),
                },
                "re_frequency": config.get("race_engineer.frequency", "normal"),
                "re_alerts": config.get("race_engineer.alerts", {}),
                "fo_frequency": config.get("first_officer.frequency", "normal"),
                "fo_announce_phase": config.get("first_officer.announce_phase", True),
                "fo_min_alt_agl": config.get("first_officer.min_alt_agl", 50),
                "fo_aircraft_type": config.get("first_officer.aircraft_type", "auto"),
                "fo_alerts": {
                    "altitude": config.get("first_officer.alerts.altitude", True),
                    "gear": config.get("first_officer.alerts.gear", True),
                    "fuel": config.get("first_officer.alerts.fuel", True),
                    "bank": config.get("first_officer.alerts.bank", True),
                    "autopilot": config.get("first_officer.alerts.autopilot", True),
                    "engine": config.get("first_officer.alerts.engine", True),
                    "waypoint": config.get("first_officer.alerts.waypoint", True),
                    "approach": config.get("first_officer.alerts.approach", True),
                    "weather": config.get("first_officer.alerts.weather", True),
                },
                "msfs_active": msfs_active,
                "msfs_aircraft": msfs_aircraft,
                "watchalong_mode": config.get("watchalong.mode", "live"),
                "watchalong_sport": config.get("watchalong.active_sport", "f1"),
                "watchalong": {
                    "mode": config.get("watchalong.mode", "live"),
                    "live": {
                        "frequency": config.get("watchalong.live.frequency", "normal"),
                    },
                    "replay": {
                        "active_event": config.get("watchalong.replay.active_event"),
                        "active_event_name": config.get("watchalong.replay.active_event_name", ""),
                        "active_fight_name": config.get("watchalong.replay.active_fight_name", ""),
                        "current_position": config.get("watchalong.replay.current_position", 0),
                    },
                    "sports": {
                        "f1": {
                            "enabled": config.get("watchalong.sports.f1.enabled", True),
                            "favourite_drivers": config.get("watchalong.sports.f1.favourite_drivers", []),
                        },
                        "ufc": {
                            "enabled": config.get("watchalong.sports.ufc.enabled", True),
                            "favourite_fighters": config.get("watchalong.sports.ufc.favourite_fighters", []),
                        },
                        "nba": {
                            "enabled": config.get("watchalong.sports.nba.enabled", True),
                            "favourite_teams": config.get("watchalong.sports.nba.favourite_teams", []),
                        },
                        "nhl": {
                            "enabled": config.get("watchalong.sports.nhl.enabled", True),
                            "favourite_teams": config.get("watchalong.sports.nhl.favourite_teams", []),
                        },
                        "formula_drift": {
                            "enabled": config.get("watchalong.sports.formula_drift.enabled", True),
                            "favourite_drivers": config.get("watchalong.sports.formula_drift.favourite_drivers", []),
                        },
                        "xgames": {
                            "enabled": config.get("watchalong.sports.xgames.enabled", True),
                            "discipline": config.get("watchalong.sports.xgames.discipline", "all"),
                            "favourite_athletes": config.get("watchalong.sports.xgames.favourite_athletes", []),
                        },
                    },
                },
                "f1_status": _f1_status(),
                "ufc_status": _ufc_status(),
                "nba_status": _nba_status(),
                "nhl_status": _nhl_status(),
                "nfl_status": _nfl_status(),
                "mlb_status": _mlb_status(),
                "dj_status": _dj_status(),
                "masterchef_status": _masterchef_status(),
                "whiplash_status": _whiplash_status(),
                # Read-only presence check, same boundary every other key in this
                # codebase respects: this settings panel writes config.yaml only,
                # never .env (that's setup_wizard.py's job) -- so there's no
                # editable key field here, just whether one's already set.
                "balldontlie_key_set": bool(os.environ.get("BALLDONTLIE_API_KEY")),
                "popup_video": {
                    "active_title": config.get("popup_video.active_title"),
                    "active_title_slug": config.get("popup_video.active_title_slug"),
                    "auto_advance": config.get("popup_video.auto_advance", False),
                },
                "ed_enabled": config.get("integrations.ed_telemetry.enabled", True),
                "ed_inara_enabled": config.get("ed.inara_enabled", True),
                "ed_inara_key_configured": bool(os.environ.get("INARA_API_KEY", "")),
                "ed_status": _ed_status(),
                "vernacular": _load_vernacular_state(),
                "game_show_difficulty": config.get("game_show.difficulty", 3),
            }

    def apply(self, changes: dict) -> dict:
        """
        Apply a batch of settings changes atomically, persisting each
        logical group to config.yaml (or the active profile's YAML for
        dial changes) immediately, and updating in-memory state so
        colours/face style/TTS voice/dials/race-engineer settings take
        effect without a restart. Returns {"restart_required": bool} —
        True if any change in this batch needs a process restart to take
        effect (mic, LLM backend, wake word).
        """
        from config.loader import config
        restart_required = False

        with self._lock:
            if "wake_word_enabled" in changes:
                new_val = bool(changes["wake_word_enabled"])
                if new_val != config.get("voice.wake_word.enabled", False):
                    restart_required = True
                config.raw.setdefault("voice", {}).setdefault("wake_word", {})["enabled"] = new_val
                config.save()
                log.info(f"Settings: wake_word_enabled -> {new_val} (restart required)")
            if "wake_word_sensitivity" in changes:
                val = max(0.0, min(1.0, float(changes["wake_word_sensitivity"])))
                if val != config.get("voice.wake_word.sensitivity", 0.5):
                    restart_required = True
                config.raw.setdefault("voice", {}).setdefault("wake_word", {})["sensitivity"] = val
                config.save()
                log.info(f"Settings: wake_word_sensitivity -> {val} (restart required)")
            if "vad_energy_thresh" in changes:
                # No restart needed -- voice/pipeline.py's VoiceActivityDetector
                # is built fresh from config.get("voice.vad.*") on every
                # recording call, so this takes effect on the very next
                # utterance, same process, no cross-process staleness to
                # worry about (unlike hud_server.py's separate-process case).
                val = max(50, min(2000, int(changes["vad_energy_thresh"])))
                config.raw.setdefault("voice", {}).setdefault("vad", {})["energy_thresh"] = val
                config.save()
                log.info(f"Settings: vad_energy_thresh -> {val}")
            if "active_profile" in changes:
                # Agent Mode switch (see AGENT_MODES). Accepts either the
                # full "profiles/x.yaml" path the UI sends or a bare stem —
                # Path(...).stem handles both. Validated against
                # list_profiles() so this can't be used to load an
                # arbitrary path outside personality/profiles/.
                # No restart needed: config.load_profile() updates this
                # process's in-memory config.profile immediately (read
                # fresh by build_system_prompt() every turn), persists via
                # config/personality_state.yaml, and _notify_webapp_reload()
                # below brings the separate webapp subprocess's agent up to
                # date too.
                from pathlib import Path as _P
                profile_stem = _P(str(changes["active_profile"])).stem
                if profile_stem in config.list_profiles():
                    try:
                        config.load_profile(profile_stem)
                        config.save()
                        log.info(f"Settings: agent mode -> {profile_stem}")
                        self._notify_webapp_reload()
                    except Exception as e:
                        log.warning(f"Settings: profile switch failed: {e}")
                else:
                    log.warning(f"Settings: unknown agent mode requested: {changes['active_profile']!r}")
            if "llm_backend" in changes:
                backend = str(changes["llm_backend"]).strip()
                if backend != config.get("llm.backend", "claude"):
                    restart_required = True
                config.raw.setdefault("llm", {})["backend"] = backend
                config.save()
                log.info(f"Settings: LLM backend -> {backend} (restart required)")
            if "llm_model" in changes:
                model = str(changes["llm_model"]).strip()
                # Applies to whichever backend this same request is switching
                # to (if any), else the currently-active one.
                target_backend = str(changes.get("llm_backend") or config.get("llm.backend", "claude"))
                if model:
                    config.raw.setdefault("llm", {}).setdefault(target_backend, {})["model"] = model
                    config.save()
                    log.info(f"Settings: LLM model ({target_backend}) -> {model} (restart required)")
            if "output_device" in changes:
                import subprocess
                try:
                    subprocess.run(
                        ["pactl", "set-default-sink", changes["output_device"]],
                        timeout=3, check=True
                    )
                    config.raw.setdefault("voice", {})["output_device"] = changes["output_device"]
                    config.save()
                    log.info(f"Settings: output_device -> {changes['output_device']}")
                except Exception as e:
                    log.warning(f"Settings: output device switch failed: {e}")

            # Dial values — apply live to the active profile's overrides so
            # the next LLM call in THIS process picks them up immediately,
            # and persist to config/personality_state.yaml (the source of
            # truth for the running instance, decoupled from the profile
            # template — see config/loader.py's module docstring) so the
            # change survives a restart and this profile's own last-used
            # dial state is remembered independently of other profiles.
            if "dials" in changes:
                from personality.dials import DIAL_BANDS
                dial_changes = changes["dials"]
                profile_overrides = config.profile.setdefault("dial_overrides", {})
                for name in DIAL_BANDS:
                    if name in dial_changes:
                        val = max(0, min(100, int(dial_changes[name])))
                        profile_overrides[name] = val
                if "probability_narration" in dial_changes:
                    profile_overrides["probability_narration"] = bool(dial_changes["probability_narration"])
                if "wellness_checkins" in dial_changes:
                    profile_overrides["wellness_checkins"] = str(dial_changes["wellness_checkins"])
                config.save_personality_state()
                log.info(f"Settings: dials updated — {list(dial_changes.keys())}")
                self._notify_webapp_reload()

            if changes.get("reset_profile_dials"):
                config.reset_profile_to_defaults()
                log.info("Settings: active profile dials reset to template defaults")
                self._notify_webapp_reload()

            # Delete a user-saved preset. "Apply to dials" (previewing a
            # preset's values) is deliberately client-side-only now — see
            # get()'s preset_values — so there's no server-side
            # "apply_preset" mutation anymore; a preset's values only ever
            # get persisted through the normal "dials" path above, once
            # the user reviews and hits the main Save button.
            if "delete_preset" in changes:
                preset_name = str(changes["delete_preset"]).strip()
                try:
                    self._delete_preset(preset_name)
                    log.info(f"Settings: preset '{preset_name}' deleted")
                except Exception as e:
                    log.warning(f"Settings: failed to delete preset '{preset_name}': {e}")
                    raise

            # Save as new named preset
            if "save_preset_as" in changes:
                preset_name = str(changes["save_preset_as"]).strip()
                if preset_name:
                    self._save_preset(preset_name, config.profile.get("dial_overrides", {}),
                                      config.profile.get("dial_preset", "Q2"))

            if changes.get("restart_as_text_mode"):
                self._restart_as_text_mode = True
                log.info("Settings: restart as text mode requested")

            if "input_device" in changes:
                device_name = str(changes["input_device"]).strip()
                if device_name != config.get("voice.input_device", "default"):
                    restart_required = True
                config.raw.setdefault("voice", {})["input_device"] = device_name
                # Also update sample_rate to match the device's preferred rate
                for opt in config.get("voice.input_device_options", []):
                    if opt.get("name", "").lower() in device_name.lower():
                        sr = opt.get("sample_rate")
                        if sr:
                            config.raw.setdefault("voice", {})["sample_rate"] = sr
                            log.info(f"Settings: input_device -> {device_name} (sample_rate -> {sr}Hz, restart required)")
                        break
                else:
                    log.info(f"Settings: input_device -> {device_name} (restart required)")
                config.save()

            if "face_style" in changes:
                style = int(changes["face_style"])
                # VALID_STYLES are the current, real styles (0=Triangle
                # Mosaic, 1=H9000 Terminal, 2=KITT, 3=KITT Hi-Con). Anything
                # outside that is a legacy pre-consolidation index (styles
                # 1-6 used to exist before H9000 Terminal absorbed them, old
                # style 7 became today's 1) — remap those, but pass current
                # valid styles through unchanged rather than defaulting them
                # to 1, which would make any newly-added style unselectable.
                VALID_STYLES = (0, 1, 2, 3)
                if style not in VALID_STYLES:
                    style = {7: 1}.get(style, 1)
                self._face_style = style
                config.raw.setdefault("face", {})["style"] = style
                config.save()
                log.info(f"Settings: face_style -> {style}")

            if "tts_voice" in changes:
                model = str(changes["tts_voice"]).strip()
                if model:
                    config.raw.setdefault("voice", {}).setdefault("deepgram_tts", {})["model"] = model
                    config.save()
                    config.save_personality_state()
                    log.info(f"Settings: tts_voice -> {model}")
                    # No restart needed in THIS process — voice/pipeline.py's
                    # get_tts() is a stateless factory that reads
                    # voice.deepgram_tts.model fresh on every call, so the
                    # very next reply already uses the new voice. The webapp
                    # runs as a separate process with its own config copy
                    # though (see webapp/server.py's get_agent()), so it still
                    # needs this notify to pick the change up before its own
                    # restart.
                    self._notify_webapp_reload()

            if "shipping" in changes:
                s = changes["shipping"]
                shipping = config.raw.setdefault("purchasing", {}).setdefault("shipping", {})
                addr = shipping.setdefault("address", {})
                if "preferred_services" in s:
                    # Accept either a list or a newline-separated string from the textarea
                    val = s["preferred_services"]
                    if isinstance(val, str):
                        val = [v.strip() for v in val.splitlines() if v.strip()]
                    shipping["preferred_services"] = val
                if "fallback" in s:
                    shipping["fallback"] = s["fallback"]
                if "confirm_above_cad" in s:
                    shipping["confirm_above_cad"] = max(0.0, float(s["confirm_above_cad"]))
                for field in ("name", "line1", "line2", "city", "province", "postal_code", "phone"):
                    if field in s:
                        addr[field] = str(s[field])
                config.save()
                log.info("Settings: shipping preferences updated")

            if "re_frequency" in changes:
                freq = str(changes["re_frequency"]).strip()
                if freq in ("off", "sparse", "normal", "chatty"):
                    config.raw.setdefault("race_engineer", {})["frequency"] = freq
                    config.save()
                    log.info(f"Settings: race_engineer.frequency -> {freq}")

            if "re_alerts" in changes:
                alerts = config.raw.setdefault("race_engineer", {}).setdefault("alerts", {})
                for key in ("fuel", "tyre_temp", "tyre_wear", "flags", "pit_window", "gap", "damage"):
                    if key in changes["re_alerts"]:
                        alerts[key] = bool(changes["re_alerts"][key])
                config.save()
                log.info(f"Settings: race_engineer.alerts updated — {list(changes['re_alerts'].keys())}")

            if "fo_frequency" in changes:
                freq = str(changes["fo_frequency"]).strip()
                if freq in ("off", "sparse", "normal", "chatty"):
                    config.raw.setdefault("first_officer", {})["frequency"] = freq
                    config.save()
                    log.info(f"Settings: first_officer.frequency -> {freq}")

            if "fo_announce_phase" in changes:
                config.raw.setdefault("first_officer", {})["announce_phase"] = bool(changes["fo_announce_phase"])
                config.save()
                log.info(f"Settings: first_officer.announce_phase -> {bool(changes['fo_announce_phase'])}")

            if "fo_min_alt_agl" in changes:
                val = max(0, min(500, int(changes["fo_min_alt_agl"])))
                config.raw.setdefault("first_officer", {})["min_alt_agl"] = val
                config.save()
                log.info(f"Settings: first_officer.min_alt_agl -> {val}")

            if "fo_aircraft_type" in changes:
                atype = str(changes["fo_aircraft_type"]).strip()
                if atype in ("auto", "ga_single", "ga_twin", "turboprop", "airliner", "fighter", "helicopter"):
                    config.raw.setdefault("first_officer", {})["aircraft_type"] = atype
                    config.save()
                    log.info(f"Settings: first_officer.aircraft_type -> {atype}")

            if "fo_alerts" in changes:
                alerts = config.raw.setdefault("first_officer", {}).setdefault("alerts", {})
                for key in ("altitude", "gear", "fuel", "bank", "autopilot", "engine", "waypoint", "approach", "weather"):
                    if key in changes["fo_alerts"]:
                        alerts[key] = bool(changes["fo_alerts"][key])
                config.save()
                log.info(f"Settings: first_officer.alerts updated — {list(changes['fo_alerts'].keys())}")

            if "watchalong_mode" in changes:
                mode = str(changes["watchalong_mode"]).strip()
                if mode in ("live", "replay"):
                    config.raw.setdefault("watchalong", {})["mode"] = mode
                    config.save()
                    log.info(f"Settings: watchalong.mode -> {mode}")

            if "watchalong_sport" in changes:
                # Validated against config.yaml's watchalong.sports block
                # (the same set Settings > Watchalong > Sport offers) rather
                # than a hardcoded f1/ufc-only pair left over from before
                # NBA/NHL/NFL/MLB/Formula Drift/X Games were added -- that
                # stale allowlist was silently dropping every sport switch
                # to one of those six.
                sport = str(changes["watchalong_sport"]).strip()
                if sport in config.raw.get("watchalong", {}).get("sports", {}):
                    config.raw.setdefault("watchalong", {})["active_sport"] = sport
                    config.save()
                    log.info(f"Settings: watchalong.active_sport -> {sport}")

            if "watchalong" in changes:
                # Sport-agnostic frequency + per-sport enable/favourites, all
                # nested under one watchalong: block (see config/config.yaml)
                # rather than the old separate f1_watchalong/ufc_watchalong
                # sections — one settings section now covers every sport.
                wa_changes = changes["watchalong"] or {}
                watchalong = config.raw.setdefault("watchalong", {})

                live_changes = wa_changes.get("live") or {}
                if "frequency" in live_changes:
                    freq = str(live_changes["frequency"]).strip()
                    if freq in ("off", "silent", "sparse", "normal", "chatty"):
                        watchalong.setdefault("live", {})["frequency"] = freq

                sports_changes = wa_changes.get("sports") or {}
                sports_cfg = watchalong.setdefault("sports", {})

                f1_changes = sports_changes.get("f1") or {}
                if f1_changes:
                    f1_cfg = sports_cfg.setdefault("f1", {})
                    if "enabled" in f1_changes:
                        f1_cfg["enabled"] = bool(f1_changes["enabled"])
                    if "favourite_drivers" in f1_changes:
                        val = f1_changes["favourite_drivers"]
                        if isinstance(val, str):
                            val = [v.strip().upper() for v in val.split(",") if v.strip()]
                        f1_cfg["favourite_drivers"] = val

                ufc_changes = sports_changes.get("ufc") or {}
                if ufc_changes:
                    ufc_cfg = sports_cfg.setdefault("ufc", {})
                    if "enabled" in ufc_changes:
                        ufc_cfg["enabled"] = bool(ufc_changes["enabled"])
                    if "favourite_fighters" in ufc_changes:
                        val = ufc_changes["favourite_fighters"]
                        if isinstance(val, str):
                            val = [v.strip() for v in val.split(",") if v.strip()]
                        ufc_cfg["favourite_fighters"] = val

                nba_changes = sports_changes.get("nba") or {}
                if nba_changes:
                    nba_cfg = sports_cfg.setdefault("nba", {})
                    if "enabled" in nba_changes:
                        nba_cfg["enabled"] = bool(nba_changes["enabled"])
                    if "favourite_teams" in nba_changes:
                        val = nba_changes["favourite_teams"]
                        if isinstance(val, str):
                            val = [v.strip() for v in val.split(",") if v.strip()]
                        nba_cfg["favourite_teams"] = val

                nhl_changes = sports_changes.get("nhl") or {}
                if nhl_changes:
                    nhl_cfg = sports_cfg.setdefault("nhl", {})
                    if "enabled" in nhl_changes:
                        nhl_cfg["enabled"] = bool(nhl_changes["enabled"])
                    if "favourite_teams" in nhl_changes:
                        val = nhl_changes["favourite_teams"]
                        if isinstance(val, str):
                            val = [v.strip().upper() for v in val.split(",") if v.strip()]
                        nhl_cfg["favourite_teams"] = val

                fd_changes = sports_changes.get("formula_drift") or {}
                if fd_changes:
                    fd_cfg = sports_cfg.setdefault("formula_drift", {})
                    if "enabled" in fd_changes:
                        fd_cfg["enabled"] = bool(fd_changes["enabled"])
                    if "favourite_drivers" in fd_changes:
                        val = fd_changes["favourite_drivers"]
                        if isinstance(val, str):
                            val = [v.strip() for v in val.split(",") if v.strip()]
                        fd_cfg["favourite_drivers"] = val

                xg_changes = sports_changes.get("xgames") or {}
                if xg_changes:
                    xg_cfg = sports_cfg.setdefault("xgames", {})
                    if "enabled" in xg_changes:
                        xg_cfg["enabled"] = bool(xg_changes["enabled"])
                    if "discipline" in xg_changes:
                        xg_cfg["discipline"] = str(xg_changes["discipline"]).strip() or "all"
                    if "favourite_athletes" in xg_changes:
                        val = xg_changes["favourite_athletes"]
                        if isinstance(val, str):
                            val = [v.strip() for v in val.split(",") if v.strip()]
                        xg_cfg["favourite_athletes"] = val

                config.save()
                log.info(f"Settings: watchalong updated — {list(wa_changes.keys())}")

            if changes.get("watchalong_clear_replay"):
                # Shared by both sports' "Clear Session"/"Clear Fight"
                # buttons — only one sport's replay is ever active at a
                # time (watchalong.active_sport), so clearing always means
                # resetting this same slot regardless of which sport's
                # button was clicked.
                replay_cfg = config.raw.setdefault("watchalong", {}).setdefault("replay", {})
                replay_cfg["active_event"] = None
                replay_cfg["active_event_name"] = ""
                replay_cfg["active_fight_id"] = None
                replay_cfg["active_fight_name"] = ""
                replay_cfg["current_position"] = 0
                config.save()
                log.info("Settings: watchalong active replay session cleared")

            if changes.get("popup_clear"):
                popup_cfg = config.raw.setdefault("popup_video", {})
                popup_cfg["active_title"] = None
                popup_cfg["active_title_slug"] = None
                config.save()
                log.info("Settings: popup_video active session cleared")

            if "popup_auto_advance" in changes:
                config.raw.setdefault("popup_video", {})["auto_advance"] = bool(changes["popup_auto_advance"])
                config.save()

            if "ed_enabled" in changes:
                new_val = bool(changes["ed_enabled"])
                if new_val != config.get("integrations.ed_telemetry.enabled", True):
                    restart_required = True  # UDP listener is only started once, in main.py's boot sequence
                config.raw.setdefault("integrations", {}).setdefault("ed_telemetry", {})["enabled"] = new_val
                config.save()
                log.info(f"Settings: integrations.ed_telemetry.enabled -> {new_val}")

            if "ed_inara_enabled" in changes:
                config.raw.setdefault("ed", {})["inara_enabled"] = bool(changes["ed_inara_enabled"])
                config.save()
                log.info(f"Settings: ed.inara_enabled -> {bool(changes['ed_inara_enabled'])}")

            if "game_show_difficulty" in changes:
                diff = max(1, min(5, int(changes["game_show_difficulty"])))
                config.raw.setdefault("game_show", {})["difficulty"] = diff
                config.save()
                log.info(f"Settings: game_show.difficulty -> {diff}")

        return {"restart_required": restart_required}

    def _notify_webapp_reload(self):
        """
        Tell the webapp subprocess to reload its personality state. main.py
        always runs the webapp as a SEPARATE OS process with its own
        IMQ2Agent/config singleton (see webapp/server.py's get_agent()) —
        settings changes made here only update THIS process's memory and
        the on-disk state files, so without this call the webapp's agent
        would keep running stale personality until it's itself restarted.
        Best-effort: the webapp may not be running (e.g. text-mode-only
        dev testing), so a failure here is logged, never raised.
        """
        try:
            import requests
            port = config.get("webapp.port", 8766)
            requests.post(f"http://127.0.0.1:{port}/reload-personality", timeout=2)
        except Exception as e:
            log.debug(f"Settings: webapp reload-personality notify failed (webapp not running?): {e}")

    def _save_preset(self, name: str, dial_overrides: dict, base_preset: str):
        """Persist a new named preset to personality/presets.py at runtime."""
        try:
            from personality.dials import PersonalityDials
            from personality.presets import PRESETS, get_preset
            base = get_preset(base_preset)
            merged = {k: getattr(base, k) for k in PersonalityDials.__dataclass_fields__}
            merged.update(dial_overrides)
            new_preset = PersonalityDials.from_dict(merged)
            PRESETS[name] = new_preset
            log.info(f"Settings: preset '{name}' saved to runtime registry")
            # Also write to presets.py so it persists across restarts
            self._write_preset_to_file(name, merged)
        except Exception as e:
            log.warning(f"Settings: failed to save preset '{name}': {e}")

    def _write_preset_to_file(self, name: str, dial_values: dict):
        """Append a new preset entry to personality/presets.py."""
        try:
            from pathlib import Path
            presets_path = Path(__file__).parent.parent / "personality" / "presets.py"
            content = presets_path.read_text()
            # Build the new preset entry
            numeric_dials = {k: v for k, v in dial_values.items()
                           if isinstance(v, int) and k not in ('probability_narration', 'wellness_checkins')}
            bool_dials = {k: v for k, v in dial_values.items() if isinstance(v, bool)}
            str_dials = {k: v for k, v in dial_values.items() if isinstance(v, str)}
            lines = [f'\n    "{name}": PersonalityDials.from_dict({{']
            for k, v in numeric_dials.items():
                lines.append(f'        "{k}": {v},')
            for k, v in bool_dials.items():
                lines.append(f'        "{k}": {str(v)},')
            for k, v in str_dials.items():
                lines.append(f'        "{k}": "{v}",')
            lines.append('    }),')
            entry = '\n'.join(lines)
            # Insert before the closing brace of PRESETS dict
            content = content.replace('\n}\n', entry + '\n}\n')
            presets_path.write_text(content)
            log.info(f"Settings: preset '{name}' written to presets.py")
        except Exception as e:
            log.warning(f"Settings: failed to write preset to file: {e}")

    def _delete_preset(self, name: str):
        """
        Remove a user-saved preset from both the runtime PRESETS dict and
        personality/presets.py. Refuses to delete the 17 built-in
        CaptCanadaMan presets or the Q2/Q2_Guest agent-mode baselines (the
        settings UI shouldn't offer a delete button for those either, but
        this is the actual enforcement point), and refuses to delete a
        preset a shipped profile still references as its dial_preset —
        that would make _resolve_profile_dials() raise KeyError on every
        turn for that profile.
        """
        from personality.presets import PRESETS, BUILTIN_PRESET_NAMES
        if name in BUILTIN_PRESET_NAMES or name in ("Q2", "Q2_Guest"):
            raise ValueError(f"'{name}' is a built-in preset and cannot be deleted")
        if name not in PRESETS:
            raise ValueError(f"Preset '{name}' not found")

        import yaml
        for stem in config.list_profiles():
            try:
                with open(PROFILES_DIR / f"{stem}.yaml", "r") as f:
                    prof = yaml.safe_load(f) or {}
                if prof.get("dial_preset") == name:
                    raise ValueError(f"Preset '{name}' is still used by profile '{stem}' — cannot delete")
            except ValueError:
                raise
            except Exception:
                continue

        del PRESETS[name]
        self._delete_preset_from_file(name)

    def _delete_preset_from_file(self, name: str):
        """Remove a preset entry from personality/presets.py by name."""
        try:
            import re
            from pathlib import Path
            presets_path = Path(__file__).parent.parent / "personality" / "presets.py"
            content = presets_path.read_text()
            # Matches exactly the block _write_preset_to_file() appends:
            # from the preset's own opening line through its own closing
            # "    }),", non-greedy so it stops at this preset's close
            # rather than a later preset's.
            pattern = re.compile(
                r'\n    "' + re.escape(name) + r'": PersonalityDials\.from_dict\(\{.*?\n    \}\),',
                re.DOTALL,
            )
            new_content, n = pattern.subn('', content, count=1)
            if n > 0:
                presets_path.write_text(new_content)
                log.info(f"Settings: preset '{name}' removed from presets.py")
            else:
                log.warning(f"Settings: could not find preset '{name}' block in presets.py to remove")
        except Exception as e:
            log.warning(f"Settings: failed to delete preset from file: {e}")

    def cycle_face_style(self) -> int:
        """Advances face_style 0->1->2->3->0 and persists it -- same target
        field and config path as apply()'s "face_style" branch above, just
        triggered from the controller's cycle_face action instead of a
        POST /settings body from the settings page's dropdown."""
        with self._lock:
            style = (self._face_style + 1) % 4
            self._face_style = style
        config.raw.setdefault("face", {})["style"] = style
        config.save()
        log.info(f"Settings: face_style -> {style} (controller cycle)")
        return style

    def consume_restart_request(self) -> bool:
        """Returns True once if a text-mode restart was requested, then clears it."""
        with self._lock:
            if self._restart_as_text_mode:
                self._restart_as_text_mode = False
                return True
            return False

    def get_colours(self) -> dict:
        with self._lock:
            return {
                "face_style": self._face_style,
            }


settings_state = _SettingsState()


# ---------------------------------------------------------------------------
# Ledger helpers — called by the HTTP handler for /ledger GET and POST
# ---------------------------------------------------------------------------

def _get_ledger_data() -> dict:
    """Return gift card balances and recent purchase history for the settings page."""
    try:
        from purchasing.ledger import BudgetLedger
        ledger = BudgetLedger()
        cards = ledger.list_gift_cards(include_inactive=False)
        history = ledger.get_purchase_history(limit=20)
        total = ledger.total_available_balance()
        ledger.close()
        # Mask card codes — show only last 4 chars in the list view for safety;
        # the full code is only ever retrieved programmatically during checkout,
        # not displayed in the UI where it could be shoulder-surfed.
        for card in cards:
            code = card.get("card_code", "")
            card["card_code_masked"] = f"****{code[-4:]}" if len(code) >= 4 else ("****" if code else "")
            del card["card_code"]  # never send raw code to the browser
            # payment_type is safe to send — it's not a credential
        return {"ok": True, "cards": cards, "history": history, "total_balance": total}
    except Exception as e:
        return {"ok": False, "error": str(e), "cards": [], "history": [], "total_balance": 0}


def _apply_ledger_action(action: dict) -> dict:
    """Handle add_card and deactivate_card actions from the settings page."""
    try:
        from purchasing.ledger import BudgetLedger
        ledger = BudgetLedger()
        cmd = action.get("cmd")

        if cmd == "add_card":
            label        = str(action.get("label", "")).strip()
            amount       = float(action.get("amount", 0))
            code         = str(action.get("card_code", "")).strip()
            payment_type = str(action.get("payment_type", "site_gc")).strip()
            if not label:
                return {"ok": False, "error": "Label is required."}
            if amount <= 0:
                return {"ok": False, "error": "Amount must be greater than zero."}
            card_id = ledger.add_gift_card(label=label, amount=amount,
                                           card_code=code, payment_type=payment_type)
            ledger.close()
            log.info(f"Ledger: card added via settings — '{label}' ${amount:.2f} [{payment_type}] (id={card_id})")
            return {"ok": True, "card_id": card_id}

        elif cmd == "deactivate_card":
            card_id = int(action.get("card_id", 0))
            ledger.deactivate_gift_card(card_id)
            ledger.close()
            log.info(f"Ledger: card {card_id} deactivated via settings")
            return {"ok": True}

        else:
            ledger.close()
            return {"ok": False, "error": f"Unknown ledger command: {cmd}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _FaceRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default request logging

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_file(INDEX_PATH, "text/html")
        elif self.path == "/settings.html":
            self._serve_file(SETTINGS_PATH, "text/html")
        elif self.path == "/state":
            data = face_state.snapshot()
            data.update(settings_state.get_colours())
            msfs_active, msfs_aircraft = _msfs_status()
            data["msfs_active"] = msfs_active
            data["msfs_aircraft"] = msfs_aircraft
            data["telemetry"] = _telemetry_status()
            data["ed_status"] = _ed_status()
            # NOTE: f1_status is deliberately NOT included here. Unlike
            # _msfs_status() (a cheap local in-memory check), _f1_status()
            # calls the remote OpenF1 API — putting it on this hot, high-
            # frequency /state path (also driving the audio waveform) would
            # risk stalling the face display on a slow/dropped network call.
            # It's only served from GET /settings, polled at a modest
            # interval while the WATCHALONG section is actually open.
            self._serve_json(data)
        elif self.path == "/settings":
            self._serve_json(settings_state.get())
        elif self.path == "/controller/state":
            try:
                from voice.controller import get_controller
                ctrl = get_controller()
                self._serve_json(ctrl.get_state() if ctrl else {"connected": False, "mapping": {}})
            except Exception as e:
                self._serve_json({"connected": False, "mapping": {}, "error": str(e)})
        elif self.path == "/controllers/all":
            # Master (Flipper WiFi preferred, 8BitDo BT fallback) + P2-P4
            # USB player status, for the settings page's Players section.
            try:
                from voice.controller import get_controller
                from voice.controller_server import get_flipper_server
                from voice.multi_controller import get_multi_controller

                flipper = get_flipper_server()
                bt = get_controller()
                multi = get_multi_controller()

                self._serve_json({
                    "master_bt": bt.get_state() if bt else {"connected": False},
                    "master_flipper": flipper.get_state() if flipper else {"connected": False},
                    "active_master": "flipper" if (flipper and flipper.connected) else "bt",
                    "players": multi.get_all_states().get("players", {}) if multi else {},
                })
            except Exception as e:
                self._serve_json({"master_bt": {"connected": False}, "master_flipper": {"connected": False}, "active_master": "bt", "players": {}, "error": str(e)})
        elif self.path == "/controllers/scan-usb":
            try:
                from voice.multi_controller import get_multi_controller
                multi = get_multi_controller()
                self._serve_json({"devices": multi.detect_usb_gamepads() if multi else []})
            except Exception as e:
                self._serve_json({"devices": [], "error": str(e)})
        elif self.path.startswith("/controller/events"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            try:
                since = float(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0.0
            events = [e for e in _controller_events if e.get("ts", 0) > since]
            self._serve_json({"events": events})
        elif self.path.startswith("/game-events"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            try:
                since = float(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0.0
            events = [e for e in _game_events if e.get("ts", 0) > since]
            self._serve_json({"events": events})
        elif self.path == "/game-state":
            # Lets the kiosk recover mid-game after a reload (e.g. the
            # scanline-heavy overlay animation restarts a lot during dev)
            # without waiting on the next pushed event.
            try:
                from integrations.game_show import get_game
                game = get_game()
                self._serve_json(game.to_dict() if game else {"active": False})
            except Exception:
                self._serve_json({"active": False})
        elif self.path == "/vernacular":
            self._serve_json(_load_vernacular_state())
        elif self.path == "/ledger":
            self._serve_json(_get_ledger_data())
        elif self.path == "/bb/session":
            try:
                from integrations.beavis_butthead import get_session
                self._serve_json(get_session().get_status())
            except Exception as e:
                self._serve_json({"error": str(e)})
        elif self.path == "/bb/replay_list":
            try:
                from integrations.beavis_butthead import get_history
                self._serve_json({"list": get_history().get_replay_allowed()})
            except Exception as e:
                self._serve_json({"error": str(e)})
        elif self.path == "/game/session":
            # In-process read of integrations.game_companion's session
            # singleton -- same reasoning as the /bb/session and
            # /circuit/active routes below: this is real agent-process
            # state (set by the start_game_session/update_game_session
            # tools), not something a separate proxying process could see.
            try:
                from integrations.game_companion import get_session
                session = get_session()
                if session:
                    self._serve_json({"active": True, **session.to_dict()})
                else:
                    self._serve_json({"active": False})
            except Exception as e:
                self._serve_json({"active": False, "error": str(e)})
        elif self.path == "/game/history":
            try:
                from integrations.game_companion import get_history
                self._serve_json({"history": get_history().recent(10)})
            except Exception as e:
                self._serve_json({"history": [], "error": str(e)})
        elif self.path == "/circuit/active":
            # In-process read of tools.circuit_builder's _active_project --
            # unlike the read-only /api/circuit/projects|component(s) routes
            # (which hud_server.py serves directly, since they only ever
            # touch disk/static data), "active project" is real agent-
            # process singleton state, so it has to be read from here, the
            # same reasoning as the /bb/* routes above.
            try:
                from tools.circuit_builder import get_active_project
                proj = get_active_project()
                self._serve_json({"project": proj.to_dict() if proj else None})
            except Exception as e:
                self._serve_json({"error": str(e)})
        elif self.path.startswith("/log-tail"):
            # Consumed by the H9000 Terminal face style (index.html) for its
            # scrolling log panel — must live here since that page is served
            # from this process/port, not the webapp server.
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            try:
                n = int(qs.get("n", ["20"])[0])
            except ValueError:
                n = 20
            text = ""
            log_file = config.get("logging.file")
            if log_file:
                try:
                    log_path = FACE_DIR.parent / log_file
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        text = "".join(f.readlines()[-n:])
                except FileNotFoundError:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))
        elif self.path.startswith("/photo"):
            # Full-screen display of a saved photo file
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            file_path = qs.get("file", [""])[0]
            try:
                from pathlib import Path as _Path
                import base64 as _b64
                img = _Path(file_path).read_bytes()
                b64 = _b64.b64encode(img).decode()
                ext = _Path(file_path).suffix.lower()
                mime = "image/png" if ext == ".png" else "image/jpeg"
                html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  html,body{{margin:0;padding:0;background:#000;width:100vw;height:100vh;
             display:flex;align-items:center;justify-content:center;overflow:hidden}}
  img{{max-width:100vw;max-height:100vh;object-fit:contain}}
  #overlay{{position:fixed;bottom:20px;right:20px;color:rgba(255,255,255,0.5);
            font-family:monospace;font-size:12px;text-align:right}}
</style></head>
<body>
<img src="data:{mime};base64,{b64}">
<div id="overlay">{_Path(file_path).name}<br>H — back to face</div>
<script>
  document.addEventListener('keydown', e => {{
    if (e.key.toLowerCase() === 'h') location.href = '/';
  }});
</script>
</body></html>"""
                self._serve_html(html)
            except Exception as e:
                self._serve_html(f"<h2 style='color:#f55;font-family:monospace'>Photo error: {e}</h2>")

        elif self.path.startswith("/camera/photo"):
            # Full-screen photo page — captures a fresh frame and displays it.
            # Auto-returns to the face after 10 seconds (or press H to go back).
            try:
                from integrations.webcam import webcam
                if not webcam.is_running:
                    webcam.start()
                    import time; time.sleep(0.4)
                jpeg = webcam.grab_jpeg(1920, 1080, quality=92)
                if not jpeg:
                    self._serve_html("<h2 style='color:#fff;font-family:monospace'>No frame available</h2>")
                    return
                import base64
                b64 = base64.b64encode(jpeg).decode()
                html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  html,body{{margin:0;padding:0;background:#000;width:100vw;height:100vh;
             display:flex;align-items:center;justify-content:center;overflow:hidden}}
  img{{max-width:100vw;max-height:100vh;object-fit:contain}}
  #overlay{{position:fixed;bottom:20px;right:20px;color:rgba(255,255,255,0.4);
            font-family:monospace;font-size:13px;text-align:right}}
  #bar{{position:fixed;bottom:0;left:0;height:3px;background:#ff2fb0;
        transition:width linear}}
</style></head>
<body>
<img src="data:image/jpeg;base64,{b64}">
<div id="overlay">H — back to face<br><span id="countdown">10</span>s</div>
<div id="bar" style="width:100%"></div>
<script>
  let t = 10;
  const bar = document.getElementById('bar');
  const cd  = document.getElementById('countdown');
  const iv = setInterval(() => {{
    t--;
    cd.textContent = t;
    bar.style.width = (t/10*100) + '%';
    if (t <= 0) {{ clearInterval(iv); location.href = '/'; }}
  }}, 1000);
  document.addEventListener('keydown', e => {{
    if (e.key.toLowerCase() === 'h') {{ clearInterval(iv); location.href = '/'; }}
  }});
</script>
</body></html>"""
                self._serve_html(html)
            except Exception as e:
                self._serve_html(f"<h2 style='color:#f55;font-family:monospace'>Camera error: {e}</h2>")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                changes = json.loads(body)
                result = settings_state.apply(changes)
                self._serve_json({"ok": True, "restart_required": result.get("restart_required", False)})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/controller/mapping":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from voice.controller import get_controller
                data = json.loads(body)
                mapping = data.get("mapping", {})
                ctrl = get_controller()
                if ctrl and mapping:
                    ctrl.update_mapping(mapping)
                    config.raw.setdefault("controller", {})["mapping"] = mapping
                    config.save()
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/flipper/mapping":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from voice.controller_server import get_flipper_server
                data = json.loads(body)
                mapping = data.get("mapping", {})
                flipper = get_flipper_server()
                if flipper and mapping:
                    flipper.update_mapping(mapping)
                    config.raw.setdefault("flipper", {})["mapping"] = mapping
                    config.save()
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/controllers/assign":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from voice.multi_controller import get_multi_controller
                data = json.loads(body) if body else {}
                player_id = data.get("player_id", "")
                path = data.get("device_path", "")
                multi = get_multi_controller()
                if not multi:
                    self._serve_json({"ok": False, "error": "Not initialised"})
                else:
                    result = multi.add_player(player_id, path)
                    if player_id in ("p2", "p3", "p4"):
                        config.raw.setdefault("controllers", {}).setdefault(player_id, {})["device_path"] = path
                        config.save()
                    self._serve_json({"ok": True, "result": result})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/controllers/remove":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from voice.multi_controller import get_multi_controller
                data = json.loads(body) if body else {}
                player_id = data.get("player_id", "")
                multi = get_multi_controller()
                if not multi:
                    self._serve_json({"ok": False, "error": "Not initialised"})
                else:
                    result = multi.remove_player(player_id)
                    if player_id in ("p2", "p3", "p4"):
                        config.raw.setdefault("controllers", {}).setdefault(player_id, {})["device_path"] = ""
                        config.save()
                    self._serve_json({"ok": True, "result": result})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/ledger":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                action = json.loads(body)
                result = _apply_ledger_action(action)
                self._serve_json(result)
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/vernacular":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from personality.vernacular import save_vernacular, build_vernacular_prompt
                state = json.loads(body)
                save_vernacular(state)
                # No cache to invalidate — build_system_prompt() calls
                # build_vernacular_prompt() fresh every turn, so the write
                # above is all "hot-reload" requires. The preview tries a
                # real round-trip through Q2's active LLM if the webapp is
                # up (most honest demonstration); falls back to just
                # showing the generated prompt block if it isn't.
                preview = None
                try:
                    import requests
                    webapp_port = config.get("webapp.port", 8766)
                    r = requests.post(
                        f"http://localhost:{webapp_port}/chat",
                        json={"message": "What's 2 + 2?"},
                        timeout=15,
                    )
                    if r.ok:
                        preview = r.json().get("reply")
                except Exception:
                    preview = None
                if not preview:
                    block = build_vernacular_prompt(state)
                    preview = block if block else "(Vernacular is disabled — nothing would be injected.)"
                self._serve_json({"ok": True, "preview": preview})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/dj/skip":
            try:
                from tools.radio_dj import dj_skip
                self._serve_json({"ok": True, "message": dj_skip()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/dj/stop":
            try:
                from tools.radio_dj import dj_stop
                self._serve_json({"ok": True, "message": dj_stop()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/dj/start_preset":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.radio_dj import start_preset_set
                self._serve_json({"ok": True, "message": start_preset_set(data.get("preset", ""))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/dj/start_free_choice":
            # generate_dj_set() calls out to the LLM (and web search) and
            # can take several seconds -- fire it in a background thread
            # so the settings-panel button gets an immediate ack rather
            # than the HTTP request hanging on the whole generation.
            try:
                def _generate():
                    from tools.radio_dj import generate_dj_set
                    generate_dj_set(theme="free choice")
                threading.Thread(target=_generate, daemon=True).start()
                self._serve_json({"ok": True, "message": "Generating your set -- Q2 will announce it shortly."})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/mc/plan":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.masterchef import plan_meal
                self._serve_json({"ok": True, "message": plan_meal(cuisine=data.get("cuisine", "free"))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/mc/next_step":
            try:
                from tools.masterchef import next_step
                self._serve_json({"ok": True, "message": next_step()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/mc/repeat_step":
            try:
                from tools.masterchef import get_current_step
                self._serve_json({"ok": True, "message": get_current_step()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/mc/show_recipe":
            try:
                from integrations.masterchef import RECIPES, get_session
                from tools.masterchef import get_full_recipe
                session = get_session()
                if not session or not session.active:
                    self._serve_json({"ok": False, "error": "No active recipe."})
                else:
                    self._serve_json({"ok": True, "message": get_full_recipe(session.current_dish)})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/wh/start_metronome":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.whiplash import start_metronome
                self._serve_json({"ok": True, "message": start_metronome(bpm=int(data.get("bpm", 100)))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/wh/stop_metronome":
            try:
                from tools.whiplash import stop_metronome
                self._serve_json({"ok": True, "message": stop_metronome()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/wh/sync":
            try:
                from tools.whiplash import sync_metronome
                self._serve_json({"ok": True, "message": sync_metronome()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/wh/start_groove":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.whiplash import start_groove_practice
                self._serve_json({"ok": True, "message": start_groove_practice(data.get("groove", ""))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/wh/stats":
            try:
                from tools.whiplash import get_timing_stats
                self._serve_json({"ok": True, "message": get_timing_stats()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/candidates":
            try:
                from tools.beavis_butthead import generate_video_candidates
                from integrations.beavis_butthead import get_session
                generate_video_candidates()  # populates the session's candidate list as a side effect
                self._serve_json({"ok": True, "candidates": get_session().candidates})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/select":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.beavis_butthead import select_videos
                from integrations.beavis_butthead import get_session
                response = select_videos(data.get("selection", ""))
                self._serve_json({"ok": True, "response": response, "session": get_session().get_status()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/start_video":
            try:
                from tools.beavis_butthead import start_video
                from integrations.beavis_butthead import get_session
                commentary = start_video()
                sess = get_session()
                self._serve_json({"ok": True, "commentary": commentary, "video": sess.current_video, "q2_is": sess.q2_is})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/react":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.beavis_butthead import react_to_video
                self._serve_json({"ok": True, "commentary": react_to_video(data.get("moment", ""))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/video_end":
            try:
                from tools.beavis_butthead import video_end_commentary
                self._serve_json({"ok": True, "commentary": video_end_commentary()})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/user_comment":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.beavis_butthead import user_comment
                self._serve_json({"ok": True, "reaction": user_comment(data.get("comment", ""))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/toggle_nice_guy":
            try:
                from tools.beavis_butthead import toggle_nice_guy
                from integrations.beavis_butthead import get_session
                commentary = toggle_nice_guy()
                self._serve_json({"ok": True, "nice_guy": get_session().nice_guy, "commentary": commentary})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/swap_chars":
            try:
                from tools.beavis_butthead import swap_characters
                from integrations.beavis_butthead import get_session
                commentary = swap_characters()
                self._serve_json({"ok": True, "q2_is": get_session().q2_is, "commentary": commentary})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/replay":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.beavis_butthead import set_replay
                self._serve_json({"ok": True, "commentary": set_replay(data.get("allowed", True))})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/bb/next_video":
            try:
                from tools.beavis_butthead import next_video
                from integrations.beavis_butthead import get_session
                commentary = next_video()
                sess = get_session()
                self._serve_json({"ok": True, "commentary": commentary, "video": sess.current_video, "q2_is": sess.q2_is})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/circuit/load":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.circuit_builder import load_project
                message = load_project(data.get("project_id", ""))
                from tools.circuit_builder import get_active_project
                proj = get_active_project()
                self._serve_json({"ok": True, "message": message, "project": proj.to_dict() if proj else None})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/circuit/create":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body) if body else {}
                from tools.circuit_builder import create_project_from_json, get_active_project
                message = create_project_from_json(data.get("circuit_json", ""))
                proj = get_active_project()
                self._serve_json({"ok": True, "message": message, "project": proj.to_dict() if proj else None})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/toggle-menu":
            # Signal the kiosk (face/index.html) to navigate to the settings
            # page. Bare route (no /face-api prefix) -- that prefix only
            # exists on webapp/server.py's generic proxy, which forwards
            # /face-api/toggle-menu here as plain /toggle-menu. In
            # practice voice/talk_button.py's ControllerBridge calls
            # emit_controller_event() directly since it runs in this same
            # process; this route exists for any other/future caller.
            try:
                emit_controller_event({"type": "ui", "action": "toggle_menu"})
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/cycle-face":
            # See /toggle-menu above re: bare path. Cycles and persists
            # the face style, then pushes a "ui" event so the kiosk updates
            # instantly instead of waiting for its 2s /state style-sync poll.
            try:
                style = settings_state.cycle_face_style()
                emit_controller_event({"type": "ui", "action": "cycle_face", "style": style})
                self._serve_json({"ok": True, "style": style})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/mode-select":
            # See /toggle-menu above re: bare path. Signals the kiosk to
            # open/close its mode-select overlay (face/index.html).
            try:
                emit_controller_event({"type": "ui", "action": "mode_select"})
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/nav-mode":
            # Lets the mode-select overlay explicitly force A/B into
            # nav_confirm/nav_back (via ControllerManager.set_nav_mode) for
            # as long as it's open, rather than toggling the same nav_mode
            # flag the mobile PWA's D-pad navigation uses -- see
            # voice/controller.py's set_nav_mode() docstring.
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from voice.controller import get_controller
                data = json.loads(body) if body else {}
                ctrl = get_controller()
                if ctrl:
                    ctrl.set_nav_mode(bool(data.get("enabled", False)))
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/ptt":
            # Web app "IN-APP" PTT source (webapp/index.html's face-mode
            # toggle) -- fires the SAME talk_button_state the physical
            # 8BitDo/Flipper controller uses, so this triggers the Pi's own
            # microphone remotely, not the phone's. That's an intentional,
            # explicitly-confirmed choice (not a mistake) -- see the
            # AT HOME/IN-APP toggle in webapp/index.html for the other
            # half of this: taps only reach this route in IN-APP mode.
            # talk_button_state.signal_toggle() toggles start/stop on every
            # "press" (matching the physical controller's PTT semantics
            # exactly) -- "release" is intentionally a no-op, same as
            # ControllerBridge._ptt() ignores it.
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from voice.talk_button import talk_button_state
                data = json.loads(body) if body else {}
                state = data.get("state", "press")
                if state == "press":
                    talk_button_state.signal_toggle()
                self._serve_json({"ok": True, "state": state})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/game-answer":
            # Controller D-pad fast path for the game-show overlay --
            # bypasses the LLM/chat-turn entirely (unlike answering by
            # voice), so this is silent: no host commentary, just the
            # on-screen result. See tools/game_show.py's answer_question().
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from tools.game_show import answer_question
                data = json.loads(body) if body else {}
                result = answer_question(str(data.get("answer", "")))
                self._serve_json({"ok": True, "result": result})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/game-lifeline":
            # See /game-answer above re: silent controller fast path.
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                from tools.game_show import use_lifeline
                data = json.loads(body) if body else {}
                result = use_lifeline(str(data.get("lifeline", "")))
                self._serve_json({"ok": True, "result": result})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/game-walk":
            # See /game-answer above re: silent controller fast path.
            try:
                from tools.game_show import walk_away
                result = walk_away()
                self._serve_json({"ok": True, "result": result})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/restart":
            # Same flag file main.py's voice/text loops already poll each
            # cycle — mirrors webapp/server.py's /restart so "Restart Q2"
            # works identically whether the settings page is loaded direct
            # on this port or proxied through the webapp's /face-api/<path>.
            try:
                RESTART_FLAG.touch()
                log.info("Settings: restart requested")
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        # Allow cross-origin requests from the kiosk pages
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_html(self, html: str):
        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _serve_file(self, path: Path, content_type: str):
        try:
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _serve_json(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server_instance: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None


def start_face_server(port: int = 8765):
    global _server_instance, _server_thread
    if _server_instance is not None:
        log.warning("Face server already running.")
        return
    _server_instance = ThreadingHTTPServer(("127.0.0.1", port), _FaceRequestHandler)
    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    _server_thread.start()
    log.info(f"Face server running at http://127.0.0.1:{port}")


def stop_face_server():
    global _server_instance
    if _server_instance is not None:
        _server_instance.shutdown()
        _server_instance = None
        log.info("Face server stopped.")
