"""
Game Detector
Monitors running processes for supported games.
"""

import sys

import psutil

GAME_MAP = {
    "acc.exe": {"name": "Assetto Corsa Competizione", "bridge": "ac"},
    "acs.exe": {"name": "Assetto Corsa 1", "bridge": "ac"},
    "ac2.exe": {"name": "Assetto Corsa EVO", "bridge": "ac"},
    "flightsimulator.exe": {"name": "MSFS 2020", "bridge": "msfs"},
    "flightsimulator2024.exe": {"name": "MSFS 2024", "bridge": "msfs"},
    "elitedangerous64.exe": {"name": "Elite Dangerous", "bridge": "ed"},
    "forzamotorsport.exe": {"name": "Forza Motorsport", "bridge": None},
    "forzahorizon5.exe": {"name": "Forza Horizon 5", "bridge": None},
    "forzahorizon6.exe": {"name": "Forza Horizon 6", "bridge": None},
}


def get_running_games() -> list:
    """Return list of detected running games."""
    if sys.platform != "win32":
        return []

    found = []
    try:
        for proc in psutil.process_iter(["name", "pid"]):
            name = (proc.info["name"] or "").lower()
            if name in GAME_MAP:
                game = GAME_MAP[name]
                found.append({
                    "process": name,
                    "name": game["name"],
                    "bridge": game["bridge"],
                    "pid": proc.info["pid"],
                })
    except Exception:
        pass

    return found
