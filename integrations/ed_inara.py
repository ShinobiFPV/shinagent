"""
IMQ2 INARA API Client
Elite Dangerous galaxy data — commodity prices, systems, stations, engineers,
ships, and materials — via the INARA API (https://inara.cz/inara-api.php).

Requires INARA_API_KEY in .env (free with an INARA account — inara.cz >
profile > API key). Falls back to scraping the public INARA web pages for
queries the API doesn't cover (currently: engineers).

Soft dependency: BeautifulSoup (bs4) is only needed for the scraping
fallback path — a missing install degrades that one query to an error
string rather than crashing anything else.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote as _urlquote

import requests

log = logging.getLogger(__name__)

INARA_API_URL = "https://inara.cz/inara-api.php"
# Repo-relative like ufc_data.py/popup_video.py's cache dirs, not
# Path.home()-relative -- the old form silently pointed at the wrong
# directory on any machine where the repo isn't cloned to exactly
# ~/imq2 (e.g. this dev machine, or anyone using a custom clone path).
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "ed"

# Cache TTLs by query kind, per the spec: commodity prices move fast,
# system/station/engineer/ship/material data is closer to static.
_TTL_S = {
    "commodity": 3600,       # 1 hour
    "system": 86400,         # 24 hours
    "station": 86400,
    "engineer": 86400,
    "ship": 86400,
    "material": 86400,
}


def _cache_path(kind: str, key: str) -> Path:
    digest = hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{kind}_{digest}.json"


def _cache_get(kind: str, key: str) -> Optional[dict]:
    path = _cache_path(kind, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("_cached_at", 0) > _TTL_S.get(kind, 3600):
            return None
        return data.get("_value")
    except Exception:
        return None


def _cache_set(kind: str, key: str, value: dict):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(kind, key)
        path.write_text(json.dumps({"_cached_at": time.time(), "_value": value}))
    except Exception as e:
        log.debug(f"ed_inara cache write failed: {e}")


def _call_event(event_name: str, event_data: dict) -> Optional[dict]:
    """
    POST a single event to the INARA API. Returns the response's eventData
    dict, or None if the key is missing, the request fails, or INARA
    reports a non-200 status for this event.
    """
    api_key = os.environ.get("INARA_API_KEY", "")
    if not api_key:
        log.warning("ed_inara: INARA_API_KEY not set")
        return None

    payload = {
        "header": {
            "appName": "IMQ2 Ship Computer",
            "appVersion": "1.0",
            "isDeveloped": True,
            "APIkey": api_key,
        },
        "events": [{
            "eventName": event_name,
            "eventTimestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "eventData": event_data,
        }],
    }

    try:
        r = requests.post(INARA_API_URL, json=payload, timeout=10)
        r.raise_for_status()
        body = r.json()
        events = body.get("events", [])
        if not events:
            return None
        event = events[0]
        if event.get("eventStatus") != 200:
            log.debug(f"ed_inara: {event_name} returned status {event.get('eventStatus')}: {event.get('eventStatusText')}")
            return None
        return event.get("eventData")
    except Exception as e:
        log.warning(f"ed_inara: {event_name} request failed: {e}")
        return None


def _scrape(url: str) -> Optional["BeautifulSoup"]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("ed_inara: beautifulsoup4 not installed — scraping fallback unavailable")
        return None
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"ed_inara: scrape of {url} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def search_commodity(name: str) -> dict:
    """Stations buying/selling a commodity, with prices and quantities."""
    cached = _cache_get("commodity", name)
    if cached is not None:
        return cached

    data = _call_event("getMarketInfoExact", {"itemName": name})
    if data is None:
        result = {"ok": False, "error": f"No INARA data for commodity '{name}'."}
    else:
        result = {"ok": True, "commodity": name, "data": data}

    _cache_set("commodity", name, result)
    return result


def search_system(name: str) -> dict:
    """System economy, government, security, and stations list."""
    cached = _cache_get("system", name)
    if cached is not None:
        return cached

    data = _call_event("getStarSystem", {"starsystemName": name})
    if data is None:
        result = {"ok": False, "error": f"No INARA data for system '{name}'."}
    else:
        result = {"ok": True, "system": name, "data": data}

    _cache_set("system", name, result)
    return result


def search_station(name: str, system: str = "") -> dict:
    """Station services, economy, shipyard, and outfitting."""
    key = f"{name}|{system}"
    cached = _cache_get("station", key)
    if cached is not None:
        return cached

    event_data = {"stationName": name}
    if system:
        event_data["starsystemName"] = system
    data = _call_event("getStarStation", event_data)
    if data is None:
        result = {"ok": False, "error": f"No INARA data for station '{name}'."}
    else:
        result = {"ok": True, "station": name, "system": system, "data": data}

    _cache_set("station", key, result)
    return result


def search_engineer(name: str) -> dict:
    """
    Engineer location, blueprints, and unlock requirements. INARA's public
    API has no documented engineer-lookup event, so this always goes
    through the scraping fallback against the public engineer page.
    """
    cached = _cache_get("engineer", name)
    if cached is not None:
        return cached

    url = f"https://inara.cz/elite/engineers/?search={_urlquote(name)}"
    soup = _scrape(url)
    if soup is None:
        result = {"ok": False, "error": f"Could not look up engineer '{name}' — scraping unavailable."}
    else:
        # INARA's engineer pages render as a table of maindata rows; grab
        # any table cell text as a rough-and-ready summary rather than
        # binding to a specific CSS class that INARA can change at any time.
        table = soup.find("table")
        text = table.get_text(" ", strip=True) if table else soup.get_text(" ", strip=True)[:1000]
        result = {"ok": True, "engineer": name, "summary": text[:1500], "url": url}

    _cache_set("engineer", name, result)
    return result


def search_ship(name: str) -> dict:
    """Ship stats and purchase locations."""
    cached = _cache_get("ship", name)
    if cached is not None:
        return cached

    data = _call_event("getShip", {"shipName": name})
    if data is None:
        result = {"ok": False, "error": f"No INARA data for ship '{name}'."}
    else:
        result = {"ok": True, "ship": name, "data": data}

    _cache_set("ship", name, result)
    return result


def search_material(name: str) -> dict:
    """Material collection sources and engineering uses."""
    cached = _cache_get("material", name)
    if cached is not None:
        return cached

    url = f"https://inara.cz/elite/materials/?search={_urlquote(name)}"
    soup = _scrape(url)
    if soup is None:
        result = {"ok": False, "error": f"Could not look up material '{name}' — scraping unavailable."}
    else:
        table = soup.find("table")
        text = table.get_text(" ", strip=True) if table else soup.get_text(" ", strip=True)[:1000]
        result = {"ok": True, "material": name, "summary": text[:1500], "url": url}

    _cache_set("material", name, result)
    return result
