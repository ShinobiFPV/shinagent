"""
IMQ2 Race Engineer — Telemetry Source Resolution
Shared helper so the Forza and AC race engineer tools can each pick
whichever sim is actually live, instead of hard-coding one source.
Used by both tools/race_engineer.py and tools/race_engineer_ac.py.
"""
from typing import Optional


def active_source() -> Optional[str]:
    """
    Returns 'forza', 'ac', or None if neither listener has received a
    packet recently. If both listeners are active, prefers whichever
    has the most recent packet.
    """
    from integrations import forza_telemetry, ac_telemetry

    forza_active = forza_telemetry.is_active()
    ac_active = ac_telemetry.is_active()

    if forza_active and ac_active:
        return "forza" if forza_telemetry.last_packet_time() >= ac_telemetry.last_packet_time() else "ac"
    if forza_active:
        return "forza"
    if ac_active:
        return "ac"
    return None
