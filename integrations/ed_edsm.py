"""
IMQ2 EDSM API Client
Elite Dangerous galaxy data via EDSM (https://www.edsm.net/api-v1/) — free,
no API key required for the read-only queries used here.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

EDSM_BASE_URL = "https://www.edsm.net/api-v1"
# Repo-relative like ufc_data.py/popup_video.py's cache dirs, not
# Path.home()-relative -- the old form silently pointed at the wrong
# directory on any machine where the repo isn't cloned to exactly
# ~/imq2 (e.g. this dev machine, or anyone using a custom clone path).
# Same directory as ed_inara.py's cache -- both ED data sources share it.
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "ed"
CACHE_TTL_S = 1800  # 30 minutes


def _cache_path(kind: str, key: str) -> Path:
    digest = hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"edsm_{kind}_{digest}.json"


def _cache_get(kind: str, key: str) -> Optional[dict]:
    path = _cache_path(kind, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("_cached_at", 0) > CACHE_TTL_S:
            return None
        return data.get("_value")
    except Exception:
        return None


def _cache_set(kind: str, key: str, value: dict):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(kind, key).write_text(json.dumps({"_cached_at": time.time(), "_value": value}))
    except Exception as e:
        log.debug(f"ed_edsm cache write failed: {e}")


def _get(path: str, params: dict) -> Optional[dict]:
    try:
        r = requests.get(f"{EDSM_BASE_URL}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"ed_edsm: GET {path} failed: {e}")
        return None


def get_system(name: str) -> dict:
    """Coordinates, government, allegiance, population, permit requirements."""
    cached = _cache_get("system", name)
    if cached is not None:
        return cached

    data = _get("/system", {"systemName": name, "showInformation": 1, "showPermit": 1})
    if not data or not data.get("name"):
        result = {"ok": False, "error": f"No EDSM data for system '{name}'."}
    else:
        result = {"ok": True, "data": data}

    _cache_set("system", name, result)
    return result


def get_system_bodies(name: str) -> dict:
    """List of bodies (stars/planets/moons) in a system."""
    cached = _cache_get("bodies", name)
    if cached is not None:
        return cached

    data = _get("/bodies", {"systemName": name})
    if not data or not data.get("bodies"):
        result = {"ok": False, "error": f"No EDSM body data for system '{name}'."}
    else:
        result = {"ok": True, "system": name, "bodies": data["bodies"]}

    _cache_set("bodies", name, result)
    return result


def get_nearest_systems(x: float, y: float, z: float, radius: int = 50) -> dict:
    """Systems within `radius` ly of the given galactic coordinates."""
    key = f"{x}_{y}_{z}_{radius}"
    cached = _cache_get("sphere", key)
    if cached is not None:
        return cached

    data = _get("/sphere-systems", {"x": x, "y": y, "z": z, "radius": radius, "showInformation": 1})
    if data is None:
        result = {"ok": False, "error": "EDSM nearest-systems query failed."}
    else:
        result = {"ok": True, "systems": data}

    _cache_set("sphere", key, result)
    return result


def get_system_traffic(name: str) -> dict:
    """Recent traffic report for a system."""
    cached = _cache_get("traffic", name)
    if cached is not None:
        return cached

    data = _get("/traffic", {"systemName": name})
    if not data or ("discovered" not in data and "traffic" not in data):
        result = {"ok": False, "error": f"No EDSM traffic data for system '{name}'."}
    else:
        result = {"ok": True, "system": name, "data": data}

    _cache_set("traffic", name, result)
    return result


def get_route(from_system: str, to_system: str) -> dict:
    """Plotted jump route between two systems."""
    key = f"{from_system}|{to_system}"
    cached = _cache_get("route", key)
    if cached is not None:
        return cached

    data = _get("/route", {"fromSystem": from_system, "toSystem": to_system})
    if not data or not data.get("result"):
        result = {"ok": False, "error": f"No EDSM route from '{from_system}' to '{to_system}'."}
    else:
        result = {"ok": True, "from": from_system, "to": to_system, "data": data}

    _cache_set("route", key, result)
    return result
