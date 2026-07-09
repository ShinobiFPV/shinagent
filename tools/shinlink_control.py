"""
IMQ2 ShinLink OS Control
Sends Tier 1 discrete control actions to ShinLink OS's agent bridge
(shinlink-os/ground/agent_bridge.py) -- protocol switching, preset loading,
network-link start/stop, ADS-B/AIS start/stop, VTX channel/band/power, and
antenna tracker reset. Mirrors tools/first_officer.py's control_aircraft
dispatch shape: the LLM has already resolved a natural-language request
into a structured (action, value) pair before this runs; no NLP happens
here.

Deliberately NOT included: arm/disarm, mode changes, motor tests, or any
RC channel override. See shinlink-os/ground/agent_bridge.py's module
docstring for why those are a separate, more carefully-guarded pass.
"""
import logging

log = logging.getLogger(__name__)


def shinlink_control(action: str, value=None) -> str:
    from integrations.shinlink_bridge import get_bridge
    bridge = get_bridge()

    if not bridge.is_reachable():
        return "ShinLink OS isn't running right now."

    result = bridge.send_command(action, value)

    if not result.get("ok"):
        # Bad input (400), preset-not-found (404), and operational
        # failures (200 ok:false) all come back through this same
        # {"ok": False, "error": ...} shape -- surface whatever message
        # the bridge gave, conversationally.
        error = result.get("error", "Unknown error")
        available = result.get("available_presets")
        if available:
            error += f". Available presets: {', '.join(available)}"
        return f"[shinlink_control] {error}"

    # load_preset and set_vtx return small dicts, not ready-made strings
    # like the other actions -- format them here rather than in the
    # bridge, matching how other tools turn raw integration data into a
    # conversational sentence (e.g. RCTelemetryTool).
    data = result.get("result")
    if action == "load_preset" and isinstance(data, dict):
        proto_note = f", protocol {data['protocol']}" if data.get("protocol") else ""
        return f"Preset '{data.get('name', value)}' loaded{proto_note}."
    if action == "switch_protocol":
        return f"Output protocol switched to {data}."
    if action == "start_network":
        return f"Network link started to {data}."
    if action == "stop_network":
        return "Network link stopped."
    if action in ("start_adsb", "start_ais", "stop_adsb", "stop_ais"):
        label = "ADS-B" if data == "adsb" else str(data).upper()
        verb = "started" if action.startswith("start_") else "stopped"
        return f"{label} {verb}."
    if action == "set_vtx" and isinstance(data, dict):
        parts = [v for v in (data.get("channel"), data.get("power")) if v]
        return f"VTX updated: {', '.join(parts)}." if parts else "VTX updated."
    if action == "reset_tracker":
        return "Antenna tracker servos centred."
    return f"Action {action} completed."
