"""
IMQ2 F1 Analyst Tools
Spoken-text wrappers over integrations/f1_watchalong.py, used by both
watchalong agent modes (personality/profiles/watchalong_live.yaml and
watchalong_replay.yaml) whenever config.yaml's watchalong.active_sport is
"f1" — see that file's module docstring for the two-profile, per-sport-
config architecture.

Live functions (get_f1_status, get_f1_driver, f1_race_alert) always resolve
the session via detect_live_session() themselves. Replay functions
(get_replay_lap, get_replay_status, list_f1_races) work off the active
replay session stored in config.yaml's watchalong.replay block (shared
with UFC's replay state — only one sport's replay is ever active at once),
set by set_replay_session() during Watchalong Replay activation.
"""
import logging

log = logging.getLogger(__name__)

_NO_LIVE_SESSION = "[f1] No live or recent F1 session detected."


# ---------------------------------------------------------------------------
# Live (Watchalong)
# ---------------------------------------------------------------------------

def get_f1_status(fields: str = "summary") -> str:
    """fields: 'summary' | 'positions' | 'tyres' | 'weather' | 'all'"""
    try:
        from integrations.f1_watchalong import (
            detect_live_session, get_current_positions, get_current_lap_number,
            get_current_tyres, get_weather,
        )

        session = detect_live_session()
        if not session:
            return _NO_LIVE_SESSION

        session_key = session["session_key"]
        positions = get_current_positions(session_key)
        if not positions:
            return f"[f1] {session.get('session_name', 'Session')} found but no live timing data yet."

        current_lap = get_current_lap_number(session_key)
        header = (
            f"{session.get('meeting_name', session.get('circuit_short_name', 'Session'))} "
            f"{session.get('session_name', '')} — lap {current_lap}"
        )

        def summary() -> str:
            lines = [header]
            for row in positions[:5]:
                gap = row.get("gap_to_leader")
                gap_str = "leader" if row["position"] == 1 else (f"+{gap:.1f}s" if gap is not None else "?")
                lines.append(f"P{row['position']} {row['name']} ({row['team']}) {gap_str}")
            return "\n".join(lines)

        if fields == "summary":
            return summary()

        if fields == "positions":
            lines = [header]
            for row in positions:
                gap = row.get("gap_to_leader")
                gap_str = "leader" if row["position"] == 1 else (f"+{gap:.1f}s" if gap is not None else "?")
                lines.append(f"P{row['position']} {row['name']} ({row['team']}) {gap_str}")
            return "\n".join(lines)

        if fields == "tyres":
            tyres = get_current_tyres(session_key)
            lines = [header]
            for row in positions:
                t = tyres.get(row["driver_number"], {})
                lines.append(f"P{row['position']} {row['name']}: {t.get('compound', '?')} ({t.get('age', '?')} laps)")
            return "\n".join(lines)

        if fields == "weather":
            w = get_weather(session_key)
            if not w:
                return f"{header}\nNo weather data yet."
            return (
                f"{header}\nAir {w.get('air_temperature')}C, track {w.get('track_temperature')}C, "
                f"humidity {w.get('humidity')}%, wind {w.get('wind_speed')}m/s"
                + (", RAIN" if w.get("rainfall") else "")
            )

        if fields == "all":
            return "\n\n".join([
                summary(),
                get_f1_status(fields="tyres"),
                get_f1_status(fields="weather"),
            ])

        return f"Unknown fields value: {fields}"

    except Exception as e:
        log.error(f"get_f1_status error: {e}", exc_info=True)
        return f"[f1] Error: {e}"


def get_f1_driver(driver: str = "") -> str:
    """driver: number, acronym (VER), or (partial) name."""
    if not driver:
        return "[f1] Provide a driver number, acronym, or name."
    try:
        from integrations.f1_watchalong import (
            detect_live_session, get_current_positions, get_current_tyres, resolve_driver,
        )

        session = detect_live_session()
        if not session:
            return _NO_LIVE_SESSION
        session_key = session["session_key"]

        d = resolve_driver(session_key, driver)
        if not d:
            return f"[f1] Could not find a driver matching '{driver}'."
        dn = d["driver_number"]

        positions = get_current_positions(session_key)
        row = next((r for r in positions if r["driver_number"] == dn), None)
        if not row:
            return f"{d.get('full_name', driver)} — no live timing data yet."

        tyres = get_current_tyres(session_key).get(dn, {})
        gap = row.get("gap_to_leader")
        gap_str = "leading" if row["position"] == 1 else (f"+{gap:.1f}s to leader" if gap is not None else "gap unknown")

        return (
            f"{d.get('full_name', driver)} ({d.get('team_name', '?')}): P{row['position']}, {gap_str}. "
            f"Tyres: {tyres.get('compound', '?')}, {tyres.get('age', '?')} laps old."
        )
    except Exception as e:
        log.error(f"get_f1_driver error: {e}", exc_info=True)
        return f"[f1] Error: {e}"


def f1_race_alert() -> str:
    """
    On-demand version of the same event check main.py's proactive alert
    thread polls every 5s (see integrations.f1_watchalong.get_watcher()).
    Useful when the user explicitly asks 'anything happening?' rather than
    waiting for a proactive callout.
    """
    try:
        from integrations.f1_watchalong import detect_live_session, get_watcher

        session = detect_live_session()
        if not session or not session.get("is_live"):
            return _NO_LIVE_SESSION

        events = get_watcher().check_new_events(session["session_key"])
        if not events:
            return "No new events since the last check."
        return " ".join(text for _category, text in events)
    except Exception as e:
        log.error(f"f1_race_alert error: {e}", exc_info=True)
        return f"[f1] Error: {e}"


# ---------------------------------------------------------------------------
# Replay (Watchalong Replay)
# ---------------------------------------------------------------------------

def set_replay_session(session_key: int, session_info: dict) -> None:
    """Persist the active replay session so it survives a restart."""
    from config.loader import config
    watchalong = config.raw.setdefault("watchalong", {})
    watchalong["active_sport"] = "f1"
    replay_cfg = watchalong.setdefault("replay", {})
    replay_cfg["active_event"] = session_key
    replay_cfg["active_event_name"] = (
        f"{session_info.get('meeting_name', '?')} — {session_info.get('session_name', '?')}"
    )
    replay_cfg["current_position"] = 0
    config.save()
    log.info(f"watchalong.replay: active F1 session set to {replay_cfg['active_event_name']} ({session_key})")


def _active_replay_session_key():
    from config.loader import config
    # Guard against reading a UFC event_id as an F1 session key — the two
    # sports share this same replay slot, only one can be active at a time.
    if config.get("watchalong.active_sport") != "f1":
        return None
    return config.get("watchalong.replay.active_event", None)


def start_replay_session(query: str, session_type: str = "Race") -> str:
    """
    Find a historical race by free-text query (e.g. 'Monaco 2024', 'last
    year Singapore') and activate it as the active Watchalong Replay
    session. This is the tool call behind the replay activation flow:
    the user names a race, Q2 resolves it via search_races()/
    get_race_session() and confirms before waiting for lap callouts.
    """
    try:
        from integrations.f1_watchalong import search_races, get_race_session

        session = get_race_session(query, session_type=session_type)
        if not session:
            matches = search_races(query)
            if not matches:
                return f"[f1_replay] Couldn't find a race matching '{query}'. Try '<race name> <year>'."
            # Multiple plausible meetings but no exact session — surface
            # the top match's name so Q2 can ask the user to confirm/narrow.
            top = matches[0]
            return (
                f"[f1_replay] Found '{top['name']}' ({top['year']}) but no {session_type} session on record. "
                f"Ask the user to confirm the race or try a different session type."
            )

        set_replay_session(session["session_key"], session)
        laps = session.get("total_laps", 0) or 0
        return (
            f"Replay session activated: {session.get('meeting_name', '?')} — "
            f"{session.get('session_name', '?')}, circuit {session.get('circuit_short_name', '?')}"
            + (f", {laps} laps" if laps else "")
            + ". Say a lap number any time for a briefing."
        )
    except Exception as e:
        log.error(f"start_replay_session error: {e}", exc_info=True)
        return f"[f1_replay] Error: {e}"


def get_replay_lap(lap_number: int) -> str:
    """Main Watchalong Replay tool — get_lap_narrative() for the given lap."""
    session_key = _active_replay_session_key()
    if not session_key:
        return "[f1_replay] No active replay session. Ask the user which race he's watching first."
    try:
        from config.loader import config
        from integrations.f1_watchalong import get_lap_narrative
        replay_cfg = config.raw.setdefault("watchalong", {}).setdefault("replay", {})
        replay_cfg["current_position"] = max(replay_cfg.get("current_position", 0) or 0, lap_number)
        config.save()
        return get_lap_narrative(session_key, lap_number)
    except Exception as e:
        log.error(f"get_replay_lap error: {e}", exc_info=True)
        return f"[f1_replay] Error: {e}"


def get_replay_status(lap_number: int, fields: str = "summary") -> str:
    """Like get_f1_status() but for a specific historical lap. fields: 'summary' | 'tyres'"""
    session_key = _active_replay_session_key()
    if not session_key:
        return "[f1_replay] No active replay session. Ask the user which race he's watching first."
    try:
        from integrations.f1_watchalong import get_lap_data
        data = get_lap_data(session_key, lap_number)
        if not data["positions"]:
            return f"[f1_replay] No data for lap {lap_number}."

        if fields == "tyres":
            lines = [f"Lap {lap_number} — tyres:"]
            for row in data["positions"]:
                lines.append(f"P{row['position']} {row['acronym']}: {row['compound']} ({row['tyre_age']} laps)")
            return "\n".join(lines)

        lines = [f"Lap {lap_number}:"]
        for row in data["positions"][:10]:
            lines.append(f"P{row['position']} {row['acronym']} ({row['team']}) — {row['compound']}")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"get_replay_status error: {e}", exc_info=True)
        return f"[f1_replay] Error: {e}"


def list_f1_races(year: int = None) -> str:
    """'What F1 races do you have data for?' — formatted by year."""
    try:
        from datetime import datetime, timezone
        from integrations.f1_watchalong import list_recent_races

        if year:
            races = list_recent_races(year=year)
            if not races:
                return f"[f1] No race data found for {year}."
            names = ", ".join(r["name"] for r in races)
            return f"{year}: {names}"

        # No year given — summarize 2023 to present, OpenF1's coverage window.
        now_year = datetime.now(timezone.utc).year
        lines = []
        for y in range(now_year, 2022, -1):
            races = list_recent_races(year=y)
            if races:
                lines.append(f"{y}: {', '.join(r['name'] for r in races)}")
        if not lines:
            return "[f1] No race data available."
        return "\n".join(lines)
    except Exception as e:
        log.error(f"list_f1_races error: {e}", exc_info=True)
        return f"[f1] Error: {e}"
