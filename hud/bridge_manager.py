"""
Bridge Manager
Launches and monitors Windows bridge scripts.
Windows only -- Pi/Linux users see a warning in the HUD.
"""

import subprocess
import sys
from pathlib import Path

# Bridge definitions
BRIDGES = {
    "ac": {
        "name": "Assetto Corsa Bridge",
        "script": "windows/ac_bridge.py",
        "games": ["ac2.exe", "acs.exe", "ACC.exe"],
        "port": 8001,
        "icon": "AC",
        "color": "#ff4400",
    },
    "msfs": {
        "name": "MSFS Bridge",
        "script": "windows/msfs_bridge.py",
        "games": ["FlightSimulator.exe", "FlightSimulator2024.exe"],
        "port": 8002,
        "icon": "FS",
        "color": "#0088ff",
    },
    "ed": {
        "name": "Elite Dangerous Bridge",
        "script": "windows/ed_bridge.py",
        "games": ["EliteDangerous64.exe"],
        "port": 8003,
        "icon": "ED",
        "color": "#ff8c00",
    },
    "acc_setup": {
        "name": "ACC Setup Manager",
        "script": "windows/acc_setup_manager.py",
        "games": [],  # not game-triggered
        "port": 8092,
        "icon": "STP",
        "color": "#e8002a",
    },
}

# Running bridge processes
_processes = {}


def is_windows() -> bool:
    return sys.platform == "win32"


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def get_bridge_status() -> dict:
    if not is_windows():
        return {
            "platform_warning": True,
            "message": "Bridge management is Windows only. "
                       "Run bridge scripts manually on your Windows PC.",
            "bridges": {},
        }

    result = {}
    for key, bridge in BRIDGES.items():
        proc = _processes.get(key)
        running = False
        pid = None

        if proc and proc.poll() is None:
            running = True
            pid = proc.pid

        result[key] = {
            "name": bridge["name"],
            "running": running,
            "pid": pid,
            "port": bridge["port"],
            "icon": bridge["icon"],
            "color": bridge["color"],
            "script": bridge["script"],
        }

    return {"platform_warning": False, "bridges": result}


def start_bridge(bridge_name: str, q2_host: str) -> dict:
    if not is_windows():
        return {"ok": False, "error": "Windows only"}

    if bridge_name not in BRIDGES:
        return {"ok": False, "error": f"Unknown bridge: {bridge_name}"}

    bridge = BRIDGES[bridge_name]
    script = get_project_root() / bridge["script"]

    if not script.exists():
        return {"ok": False, "error": f"Script not found: {script}"}

    # Don't start if already running
    proc = _processes.get(bridge_name)
    if proc and proc.poll() is None:
        return {"ok": True, "message": "Already running", "pid": proc.pid}

    try:
        # subprocess.CREATE_NO_WINDOW only exists on Windows -- the ternary
        # below is safe on other platforms because Python only evaluates
        # whichever branch is selected, never both, so is_windows() being
        # False here means that attribute is never actually accessed.
        proc = subprocess.Popen(
            [sys.executable, str(script), "--host", q2_host],
            cwd=str(get_project_root()),
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        _processes[bridge_name] = proc
        return {"ok": True, "pid": proc.pid, "name": bridge["name"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stop_bridge(bridge_name: str) -> dict:
    proc = _processes.get(bridge_name)
    if not proc or proc.poll() is not None:
        return {"ok": True, "message": "Not running"}

    try:
        proc.terminate()
        proc.wait(timeout=5)
        del _processes[bridge_name]
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def auto_start_for_game(game_name: str, q2_host: str) -> list:
    """Start bridges that match a detected game. Returns list of started."""
    started = []
    for key, bridge in BRIDGES.items():
        if game_name.lower() in [g.lower() for g in bridge["games"]]:
            result = start_bridge(key, q2_host)
            if result.get("ok"):
                started.append(key)
    return started
