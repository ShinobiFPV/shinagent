"""
IMQ2 Forza Open World Co-Pilot Tools
Excited-passenger commentary and a personal landmark map for Forza's open
world mode (as opposed to tools/race_engineer.py, which is scored-race
telemetry). driving_mode/drift/air come from integrations/forza_telemetry.py;
location data comes from integrations/forza_location.py.
"""
import logging

log = logging.getLogger(__name__)


def get_driving_vibe() -> str:
    """Natural-language description of what's happening in the car right
    now -- speed, drift, airtime, mode. For open-world status checks."""
    try:
        from integrations.forza_telemetry import get_snapshot, is_active
        if not is_active():
            return "[forza_openworld] No telemetry -- is Forza running with Data Out enabled?"

        snap = get_snapshot()
        if not snap:
            return "[forza_openworld] No data yet -- drive for a moment."

        speed_kmh = snap.get("speed", 0) * 3.6
        drift = snap.get("drift", {})
        air = snap.get("air", {})

        if air.get("airborne"):
            return f"We're airborne at {speed_kmh:.0f} km/h."

        recent_air = air.get("recent_event")
        if recent_air and recent_air.get("airtime", 0) > 0.5:
            return (f"Just landed from a {recent_air['airtime']:.1f} second jump "
                    f"at {recent_air.get('speed_kmh', speed_kmh):.0f} km/h.")

        if drift.get("drifting"):
            return f"Drifting at {drift.get('drift_angle', 0):.0f} degrees, {drift.get('drift_duration', 0):.1f} seconds deep."

        if speed_kmh < 5:
            return "Sitting still."

        return f"Cruising at {speed_kmh:.0f} km/h."

    except Exception as e:
        log.error(f"get_driving_vibe error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def mark_location(name: str) -> str:
    """Save the current FH6 GPS position as a named landmark."""
    try:
        from integrations.forza_telemetry import get_snapshot, is_active
        from integrations.forza_location import get_location_system

        if not is_active():
            return "[forza_openworld] No telemetry -- is Forza running with Data Out enabled?"
        snap = get_snapshot()
        if not snap:
            return "[forza_openworld] No data yet -- drive for a moment."

        x, y, z = snap.get("pos_x", 0), snap.get("pos_y", 0), snap.get("pos_z", 0)
        return get_location_system().add_landmark(name, x, z, y)

    except Exception as e:
        log.error(f"mark_location error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def where_are_we() -> str:
    """Location description based on the current FH6 GPS position."""
    try:
        from integrations.forza_telemetry import get_snapshot, is_active
        from integrations.forza_location import get_location_system

        if not is_active():
            return "[forza_openworld] No telemetry -- is Forza running with Data Out enabled?"
        snap = get_snapshot()
        if not snap:
            return "[forza_openworld] No data yet -- drive for a moment."

        x, z = snap.get("pos_x", 0), snap.get("pos_z", 0)
        return get_location_system().get_nearby_description(x, z)

    except Exception as e:
        log.error(f"where_are_we error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def list_locations() -> str:
    """List all known FH6 landmarks (builtin + personal + imported) and,
    for personal ones, their visit counts."""
    try:
        from integrations.forza_location import get_location_system
        loc = get_location_system()
        landmarks = loc.list_landmarks()
        if not landmarks:
            return "No locations known yet. Say 'mark this location' while driving to build your map."

        summary = loc.import_summary()
        by_source = ", ".join(f"{k}({v})" for k, v in summary["by_source"].items())
        lines = [f"{summary['total']} known locations -- {by_source}:"]
        sorted_lm = sorted(landmarks, key=lambda lm: lm.get("visits", 0), reverse=True)
        for lm in sorted_lm[:30]:
            visit_s = f" ({lm['visits']} visits)" if lm.get("source") == "personal" and lm.get("visits") else ""
            lines.append(f"{lm['name']}{visit_s}")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"list_locations error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def remove_location(name: str) -> str:
    """Remove a saved FH6 landmark by name (personal landmarks only)."""
    try:
        from integrations.forza_location import get_location_system
        return get_location_system().remove_landmark(name)
    except Exception as e:
        log.error(f"remove_location error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def list_nearby_landmarks(radius_m: int = 500) -> str:
    """List known landmarks within radius_m of the current position."""
    try:
        from integrations.forza_telemetry import get_snapshot, is_active
        from integrations.forza_location import get_location_system

        if not is_active():
            return "[forza_openworld] No telemetry -- is Forza running with Data Out enabled?"
        snap = get_snapshot()
        if not snap:
            return "[forza_openworld] No data yet -- drive for a moment."

        x, z = snap.get("pos_x", 0), snap.get("pos_z", 0)
        near = get_location_system().nearby(x, z, radius_m)
        if not near:
            return f"Nothing known within {radius_m}m."

        lines = [f"{lm['name']} {lm['distance']:.0f}m away" for lm in near[:10]]
        return "You've got " + ", ".join(lines) + "."
    except Exception as e:
        log.error(f"list_nearby_landmarks error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def get_location_callout_info(name: str) -> str:
    """Full notes/context on a specific named landmark, for 'tell me about X'."""
    try:
        from integrations.forza_location import get_location_system
        lm = get_location_system().get_landmark(name)
        if not lm:
            return f"[forza_openworld] Don't know '{name}'. Try list_locations to see what's mapped."

        lines = [lm["name"]]
        if lm.get("region"):
            lines.append(f"({lm['region']})")
        header = " ".join(lines)
        notes = lm.get("notes", "") or "No notes recorded for this location."
        return f"{header} -- {notes}"
    except Exception as e:
        log.error(f"get_location_callout_info error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def import_location_map(file_path: str) -> str:
    """Import a community FH6 landmark map (JSON file), or pass 'reload'
    to re-scan data/fh6_maps/ and the personal map from disk."""
    try:
        from integrations.forza_location import get_location_system
        loc = get_location_system()
        if file_path.strip().lower() == "reload":
            return loc.reload_all_sources()
        return loc.import_map_file(file_path)
    except Exception as e:
        log.error(f"import_location_map error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def export_personal_map(name: str = "my_fh6_map") -> str:
    """Export the user's own personal landmarks to data/fh6_maps/{name}.json
    for sharing with the community."""
    try:
        from integrations.forza_location import get_location_system
        return get_location_system().export_personal(name)
    except Exception as e:
        log.error(f"export_personal_map error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def get_race_status() -> str:
    """Current FH6 race status -- position, lap, lap times, positions
    gained/lost. No lap total or "laps remaining": FH6's telemetry only
    exposes the current lap number, not a total, so that can't be derived
    honestly (see integrations/forza_telemetry.py's RaceStateTracker
    docstring) -- same reason get_driving_vibe/race alerts never claim a
    gap-to-leader time."""
    try:
        from integrations.forza_telemetry import get_snapshot, is_active
        if not is_active():
            return "[forza_openworld] No telemetry -- is Forza running with Data Out enabled?"
        snap = get_snapshot()
        if not snap:
            return "[forza_openworld] No data yet -- drive for a moment."

        if snap.get("driving_mode") != "race":
            return "Not in a race."

        summary = snap.get("race_summary")
        if not summary:
            return "Race data not available yet."

        pos = summary.get("position", 0)
        lap = summary.get("lap", 0)
        last_lap = summary.get("last_lap_time", "--:--.---")
        best_lap = summary.get("best_lap_time", "--:--.---")
        gained = summary.get("positions_gained", 0)
        lost = summary.get("positions_lost", 0)

        lines = []
        if pos == 1:
            lines.append("You're leading.")
        elif pos > 0:
            lines.append(f"Running P{pos}.")

        if lap > 0:
            lines.append(f"Lap {lap}.")
        if last_lap != "--:--.---":
            lines.append(f"Last lap: {last_lap}.")
        if best_lap != "--:--.---":
            lines.append(f"Best: {best_lap}.")

        if gained > 0:
            lines.append(f"Up {gained} positions from the start.")
        elif lost > 0:
            lines.append(f"Down {lost} positions from the start.")

        return " ".join(lines) if lines else "Race in progress."

    except Exception as e:
        log.error(f"get_race_status error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"


def get_drift_stats() -> str:
    """Recent drift session summary -- duration, angle, peak yaw, score."""
    try:
        from integrations.forza_telemetry import get_snapshot, is_active
        if not is_active():
            return "[forza_openworld] No telemetry -- is Forza running with Data Out enabled?"
        snap = get_snapshot()
        if not snap:
            return "[forza_openworld] No data yet -- drive for a moment."

        recent = snap.get("drift", {}).get("recent_drifts", [])
        if not recent:
            return "No drifts recorded this session."

        lines = []
        for d in reversed(recent):
            lines.append(
                f"{d.get('score', 'mild').upper()} -- {d.get('duration', 0):.1f}s, "
                f"{d.get('angle', 0):.0f}° angle, {d.get('peak_yaw', 0):.0f}°/s yaw"
            )
        return "Recent drifts:\n" + "\n".join(lines)

    except Exception as e:
        log.error(f"get_drift_stats error: {e}", exc_info=True)
        return f"[forza_openworld] Error: {e}"
