"""
IMQ2 UFC Data Integration — ESPN public API only.

ufcstats.com (the originally planned primary/historical source) blocks
every single request behind a JavaScript proof-of-work anti-bot challenge
— confirmed live against the event list, fight-details, and event-details
endpoints, all of which return the same "Checking your browser…" gate with
no robots.txt to even check permissions against. Bypassing that is out of
scope by the user's own call, so this module talks to ESPN's public MMA API
only, and does not attempt to scrape ufcstats.com at all.

ESPN's public API is also narrower than originally assumed (verified live):
  - /scoreboard works great — current/live event, or historical via
    dates=YYYYMMDD (a specific event night) or dates=YYYY (a whole season).
  - /schedule and /athletes do NOT exist for mma (404 on both).
  - /summary?event=... does NOT exist for mma (404).
  - Fight cards include fighters, weight class, venue, winner, and the
    round/clock a fight ended at (competition.status.period /
    .displayClock) — but NOT the finish method (KO/Sub/Decision) and NOT
    any round-by-round strike/grappling stats.

Because there is no free structured source for round-by-round stats,
Watchalong Replay's round-by-round commentary (sport: UFC) comes from Q2's
own trained knowledge of the fight, not verified live numbers — see
tools/ufc_analyst.py and personality/profiles/watchalong_replay.yaml for
how that's framed honestly to the user rather than presented as scraped data.
"""

import json
import logging
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "ufc"

# Short-lived in-memory cache for live/upcoming lookups within one process run.
_mem_cache: dict[str, tuple[float, object]] = {}


def _get(endpoint: str, cache_s: float = 0, **params) -> dict:
    """GET one ESPN MMA endpoint. Returns {} on any error — every caller
    treats 'no data' as a normal outcome (no live event, unknown date, etc)."""
    clean = {k: v for k, v in params.items() if v is not None}
    cache_key = f"{endpoint}?{sorted(clean.items())}"
    if cache_s > 0:
        hit = _mem_cache.get(cache_key)
        if hit and (time.time() - hit[0]) < cache_s:
            return hit[1]
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=clean, timeout=10)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        log.debug(f"ufc_data: {endpoint} fetch failed: {e}")
        return {}
    if cache_s > 0:
        _mem_cache[cache_key] = (time.time(), data)
    return data


# ---------------------------------------------------------------------------
# Disk cache — historical event data never changes, so it's cached
# indefinitely; upcoming/live lookups refresh on a schedule per the user's spec.
# ---------------------------------------------------------------------------

def _cache_read(name: str, max_age_s: Optional[float]) -> Optional[dict]:
    path = CACHE_DIR / name
    if not path.exists():
        return None
    if max_age_s is not None and (time.time() - path.stat().st_mtime) > max_age_s:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_write(name: str, data) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / name).write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        log.debug(f"ufc_data: cache write failed for {name}: {e}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _competitor_record(c: dict) -> str:
    for rec in c.get("records", []) or []:
        if rec.get("name") == "overall" or rec.get("type") == "total":
            return rec.get("summary", "?")
    return "?"


def _format_competitor(c: dict) -> dict:
    athlete = c.get("athlete", {})
    return {
        "name": athlete.get("fullName", "?"),
        "espn_id": athlete.get("id") or c.get("id"),
        "record": _competitor_record(c),
        "winner": bool(c.get("winner", False)),
    }


def _format_fight(comp: dict, card_position: int, card_size: int) -> dict:
    """
    card_position counts DOWN from the main event (0 = main event, 1 =
    co-main, ...) — ESPN's competitions array is ordered early-prelims
    first, main event last (verified against UFC 300's real card order).
    """
    fighters = [_format_competitor(c) for c in comp.get("competitors", []) or []]
    status = comp.get("status", {}) or {}
    status_type = status.get("type", {}) or {}
    result = None
    if status_type.get("completed"):
        winner = next((f for f in fighters if f["winner"]), None)
        loser = next((f for f in fighters if not f["winner"]), None)
        result = {
            "winner": winner["name"] if winner else None,
            "winner_record": winner["record"] if winner else None,
            "loser": loser["name"] if loser else None,
            # ESPN's public payload has no finish-method field (KO/Sub/
            # Decision) — only round + clock the fight ended at. Q2 fills
            # in the actual method from its own knowledge when asked.
            "ended_round": status.get("period"),
            "ended_clock": status.get("displayClock"),
        }
    return {
        "fighters": fighters,
        "weight_class": (comp.get("type", {}) or {}).get("abbreviation", "?"),
        "scheduled_rounds": (comp.get("format", {}) or {}).get("regulation", {}).get("periods"),
        "is_main_event": card_position == 0,
        "is_co_main": card_position == 1,
        "card_position_from_top": card_position,
        "completed": bool(status_type.get("completed")),
        "live": status_type.get("state") == "in",
        "result": result,
    }


def format_event(event: dict) -> dict:
    comps = event.get("competitions", []) or []
    n = len(comps)
    # Reversed: last array entry (index n-1) is the main event -> position 0.
    fights = [_format_fight(comp, n - 1 - i, n) for i, comp in enumerate(comps)]
    fights.sort(key=lambda f: f["card_position_from_top"])
    venue = (comps[0].get("venue", {}) if comps else {}) or {}
    address = venue.get("address", {}) or {}
    status_type = (event.get("status", {}) or {}).get("type", {}) or {}
    return {
        "event_id": event.get("id"),
        "name": event.get("name", "?"),
        "date": event.get("date", ""),
        "venue": venue.get("fullName", "?"),
        "city": address.get("city", ""),
        "state": address.get("state", ""),
        "country": address.get("country", ""),
        "completed": bool(status_type.get("completed")),
        "live": status_type.get("state") == "in",
        "fights": fights,
    }


# ---------------------------------------------------------------------------
# Live / upcoming (Watchalong)
# ---------------------------------------------------------------------------

def get_live_event() -> Optional[dict]:
    """Current ESPN scoreboard event if it's actually in progress right now."""
    data = _get("scoreboard", cache_s=15)
    events = data.get("events", []) or []
    if not events:
        return None
    formatted = format_event(events[0])
    return formatted if formatted["live"] else None


def get_upcoming_events(days_ahead: int = 30) -> list[dict]:
    """
    Next UFC event(s) per ESPN's default scoreboard (which already returns
    the next upcoming card when nothing is live), cached to upcoming.json
    on a 6-hour refresh per the user's spec.
    """
    cached = _cache_read("upcoming.json", max_age_s=6 * 3600)
    if cached is not None:
        return cached

    data = _get("scoreboard", cache_s=0)
    events = data.get("events", []) or []
    now = datetime.now(timezone.utc)
    result = []
    for e in events:
        formatted = format_event(e)
        result.append(formatted)
    _cache_write("upcoming.json", result)
    return result


def get_tonight_event() -> Optional[dict]:
    """
    Today's event if ESPN's scoreboard shows one scheduled/live for today's
    date (used by Watchalong's 'card' status and activation announcement).
    """
    data = _get("scoreboard", cache_s=15)
    events = data.get("events", []) or []
    if not events:
        return None
    formatted = format_event(events[0])
    event_date = (formatted["date"] or "")[:10]
    today = datetime.now(timezone.utc).date().isoformat()
    return formatted if (formatted["live"] or event_date == today) else None


# ---------------------------------------------------------------------------
# Historical lookup (Watchalong Replay's "populate stats" flow)
# ---------------------------------------------------------------------------

def get_events_for_year(year: int) -> list[dict]:
    """
    A full season of events via ESPN's dates=YYYY (verified: returns the
    same event list as dates=YYYYMMDD-per-event, just aggregated for the
    year). Historical years are cached indefinitely — past events don't change.
    """
    cache_name = f"events_{year}.json"
    is_current_year = year >= datetime.now(timezone.utc).year
    cached = _cache_read(cache_name, max_age_s=(7 * 86400 if is_current_year else None))
    if cached is not None:
        return cached

    data = _get("scoreboard", cache_s=0, dates=str(year))
    events = [format_event(e) for e in (data.get("events", []) or [])]
    _cache_write(cache_name, events)
    return events


def list_ufc_events_by_year(year: int = None) -> list[dict]:
    year = year or datetime.now(timezone.utc).year
    return get_events_for_year(year)


def search_ufc_event(query: str) -> list[dict]:
    """
    Fuzzy search across event name and fighter names for a given year (or
    the last few years if no year is named), same approach as F1's
    search_races(). Handles 'UFC 300', 'McGregor Poirier', a bare year, and
    'last year X'.
    """
    import re
    query = query.strip()
    query_lower = query.lower()
    now_year = datetime.now(timezone.utc).year

    if "last year" in query_lower:
        target_year = now_year - 1
        query_lower = query_lower.replace("last year", "").strip()
    else:
        year_match = re.search(r"\b(19|20)\d{2}\b", query)
        target_year = int(year_match.group()) if year_match else None
        if year_match:
            query_lower = query_lower.replace(year_match.group(), "").strip()

    # 20-year fallback window (not just "recent") — well-known historical
    # fights (e.g. a retirement bout) are exactly the kind of thing this
    # gets asked about, not just last season's cards. Historical years are
    # cached indefinitely, so this is cheap after the first search.
    years_to_search = [target_year] if target_year else list(range(now_year, now_year - 20, -1))

    candidates = []
    for y in years_to_search:
        candidates.extend(get_events_for_year(y))

    if not query_lower:
        return candidates

    scored = []
    for c in candidates:
        fighter_names = " ".join(
            f["name"] for fight in c["fights"] for f in fight["fighters"]
        )
        haystack = f"{c['name']} {fighter_names}".lower()
        score = max(
            SequenceMatcher(None, query_lower, haystack).ratio(),
            1.0 if query_lower in haystack else 0.0,
        )
        # Boost matches where every query token appears somewhere (handles
        # 'McGregor Poirier' matching a card with both names in different fights).
        tokens = query_lower.split()
        if tokens and all(t in haystack for t in tokens):
            score = max(score, 0.9)
        if score > 0.3:
            scored.append((score, c))

    # Descending score, then MOST RECENT first as tiebreak — a query like
    # 'Khabib' legitimately matches every card he ever fought on (his UFC
    # debut through his retirement bout all score 1.0 on a plain substring
    # match), and his early undercard prelim appearances are a much less
    # useful top result than his title fights/retirement fight, which skew
    # later in his career.
    scored.sort(key=lambda x: (x[0], x[1]["date"]), reverse=True)
    return [c for _, c in scored[:5]]


def find_fighter_in_event(event: dict, name: str) -> Optional[dict]:
    """Fuzzy-match a fighter name against one event's card, returning that fight."""
    name_lower = name.strip().lower()
    best = None
    best_score = 0.0
    for fight in event.get("fights", []):
        for fighter in fight["fighters"]:
            score = max(
                SequenceMatcher(None, name_lower, fighter["name"].lower()).ratio(),
                1.0 if name_lower in fighter["name"].lower() else 0.0,
            )
            if score > best_score:
                best_score = score
                best = fight
    return best if best_score > 0.4 else None


def find_fighter_last_record(name: str, search_years: int = 15) -> Optional[dict]:
    """
    Best-effort current record lookup: scans recent cached/fetched event
    cards for this fighter's most recent listed record. ESPN has no
    dedicated fighter-search endpoint (verified: /athletes 404s), so this
    is the only way to get a record without asking Q2's own knowledge.
    Checks most-recent years first, so this is just as fast as a short
    window for active fighters — the wider default only matters for
    retired/inactive names, which is exactly when it's needed most.
    """
    now_year = datetime.now(timezone.utc).year
    for y in range(now_year, now_year - search_years, -1):
        for event in reversed(get_events_for_year(y)):
            fight = find_fighter_in_event(event, name)
            if fight:
                fighter = next(
                    (f for f in fight["fighters"]
                     if SequenceMatcher(None, name.lower(), f["name"].lower()).ratio() > 0.4
                     or name.lower() in f["name"].lower()),
                    None,
                )
                if fighter:
                    return {"name": fighter["name"], "record": fighter["record"], "event": event["name"], "date": event["date"]}
    return None
