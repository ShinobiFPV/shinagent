"""
IMQ2 F1 Watchalong Integration
Thin client over the free OpenF1 API (https://openf1.org) — no API key
required. Shared by both Watchalong Live and Watchalong Replay agent modes
(personality/profiles/watchalong_live.yaml and watchalong_replay.yaml)
whenever watchalong.active_sport is "f1"; the difference between the two
modes is in which functions get called and how, not in the data source.

OpenF1 endpoints used: sessions, meetings, drivers, laps, position,
intervals, pit, stints, race_control, weather.
"""

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.openf1.org/v1"

# Maps casual language to OpenF1's session_name values.
SESSION_TYPE_ALIASES = {
    "race": "Race", "gp": "Race", "grand prix": "Race",
    "quali": "Qualifying", "qualifying": "Qualifying", "qualifier": "Qualifying",
    "sprint": "Sprint", "sprint race": "Sprint",
    "sprint quali": "Sprint Qualifying", "sprint qualifying": "Sprint Qualifying",
    "practice": "Practice 1", "fp1": "Practice 1", "practice 1": "Practice 1",
    "fp2": "Practice 2", "practice 2": "Practice 2",
    "fp3": "Practice 3", "practice 3": "Practice 3",
}

# Simple in-memory response cache — OpenF1 is a free/shared API, so
# short-lived caching keeps both the live 5s poll and repeated historical
# lookups (e.g. re-fetching drivers per lap) from hammering it.
_cache: dict[str, tuple[float, object]] = {}


def _get(endpoint: str, cache_s: float = 0, timeout: float = 10, **params) -> list:
    """
    GET an OpenF1 endpoint. Filters out None params. Returns [] on any error
    rather than raising — every caller in this module treats 'no data' as a
    normal, expected outcome (session not live, race hasn't started, etc).

    Builds the query string by hand rather than passing params= to requests:
    several OpenF1 filters embed a comparison operator directly in the key
    with no further '=' separating it from the value (date>..., date<=...,
    lap_number<=...). requests' params= dict percent-encodes the ENTIRE key
    (including that trailing '=') and then appends its own '=' before the
    value, producing a corrupted double-equals token that OpenF1 silently
    ignores/misparses. Building the string ourselves avoids that.
    """
    from urllib.parse import quote
    clean_params = {k: v for k, v in params.items() if v is not None}
    cache_key = f"{endpoint}?{sorted(clean_params.items())}"

    if cache_s > 0:
        hit = _cache.get(cache_key)
        if hit and (time.time() - hit[0]) < cache_s:
            return hit[1]

    parts = []
    for k, v in clean_params.items():
        value = quote(str(v), safe="")
        if "<" in k or ">" in k:
            parts.append(f"{quote(k, safe='<>=')}{value}")
        else:
            parts.append(f"{quote(k, safe='')}={value}")
    query = "&".join(parts)
    url = f"{BASE_URL}/{endpoint}" + (f"?{query}" if query else "")

    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json() or []
    except Exception as e:
        log.debug(f"f1_watchalong: {endpoint} fetch failed: {e}")
        return []

    if cache_s > 0:
        _cache[cache_key] = (time.time(), data)
    return data


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_session_type(session_type: str) -> str:
    return SESSION_TYPE_ALIASES.get(session_type.strip().lower(), session_type)


# ---------------------------------------------------------------------------
# Live session detection
# ---------------------------------------------------------------------------

def get_meeting(meeting_key) -> dict:
    meetings = _get("meetings", cache_s=3600, meeting_key=meeting_key)
    return meetings[0] if meetings else {}


def _enrich_with_meeting(session: dict) -> dict:
    """
    OpenF1's /sessions rows only carry meeting_key, not the meeting's own
    name/country — those live on /meetings. Every caller wants a readable
    race name, so this join happens once here rather than in each caller.
    """
    if not session:
        return session
    meeting = get_meeting(session.get("meeting_key"))
    session["meeting_name"] = meeting.get("meeting_name") or session.get("circuit_short_name", "Session")
    session["country_name"] = meeting.get("country_name", session.get("country_name", ""))
    return session


def detect_live_session() -> Optional[dict]:
    """
    The most recent/current session (OpenF1's session_key='latest'), tagged
    with is_live: True if 'now' falls within its start/end window (+10min
    grace for race control wrap-up after the chequered flag).
    Returns None if OpenF1 has nothing at all (e.g. off-season with no
    session ever recorded, which shouldn't happen once 2023+ data exists).
    """
    sessions = _get("sessions", cache_s=5, session_key="latest")
    if not sessions:
        return None
    session = _enrich_with_meeting(dict(sessions[0]))
    now = datetime.now(timezone.utc)
    start = _parse_dt(session.get("date_start"))
    end = _parse_dt(session.get("date_end"))
    session["is_live"] = bool(
        start and end and start <= now <= (end + timedelta(minutes=10))
    )
    return session


def next_upcoming_race() -> Optional[dict]:
    """
    Nearest future meeting, for the 'no live session' announcement. Same
    {name, circuit, country, date, meeting_key, year} shape as
    list_recent_races() so callers don't need to know which one they got.
    """
    year = datetime.now(timezone.utc).year
    today = datetime.now(timezone.utc).date()

    def _date_of(m):
        # m["date"] is a plain "YYYY-MM-DD" string (see list_recent_races) —
        # fromisoformat parses that as a NAIVE datetime, so comparisons stay
        # in plain date-space here rather than mixing naive/aware datetimes.
        dt = _parse_dt(m["date"])
        return dt.date() if dt else today

    candidates = list_recent_races(year=year)
    upcoming = [m for m in candidates if _date_of(m) >= today]
    if not upcoming:
        # season rollover — check next year too
        upcoming = list_recent_races(year=year + 1)
    if not upcoming:
        return None
    return min(upcoming, key=_date_of)


# ---------------------------------------------------------------------------
# Live standings / status (Watchalong mode)
# ---------------------------------------------------------------------------

def get_drivers(session_key) -> dict:
    """driver_number -> driver info, for name/team resolution."""
    drivers = _get("drivers", cache_s=60, session_key=session_key)
    return {d["driver_number"]: d for d in drivers if "driver_number" in d}


def get_current_lap_number(session_key) -> int:
    laps = _get("laps", cache_s=5, session_key=session_key)
    return max((l.get("lap_number", 0) for l in laps), default=0)


def get_current_positions(session_key) -> list[dict]:
    """
    Latest known position + gap for every driver, ordered P1 first.
    Uses only the last few minutes of /position and /intervals data (a live
    session's full history isn't needed for 'what's the standing right now').
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    positions = _get("position", cache_s=5, session_key=session_key, **{"date>": since})
    intervals = _get("intervals", cache_s=5, session_key=session_key, **{"date>": since})

    latest_pos: dict = {}
    for p in positions:
        dn = p.get("driver_number")
        if dn is None:
            continue
        if dn not in latest_pos or p["date"] > latest_pos[dn]["date"]:
            latest_pos[dn] = p

    latest_gap: dict = {}
    for i in intervals:
        dn = i.get("driver_number")
        if dn is None:
            continue
        if dn not in latest_gap or i["date"] > latest_gap[dn]["date"]:
            latest_gap[dn] = i

    drivers = get_drivers(session_key)
    rows = []
    for dn, p in latest_pos.items():
        d = drivers.get(dn, {})
        gap = latest_gap.get(dn, {})
        rows.append({
            "driver_number": dn,
            "name": d.get("name_acronym", str(dn)),
            "full_name": d.get("full_name", str(dn)),
            "team": d.get("team_name", "?"),
            "position": p.get("position", 99),
            "gap_to_leader": gap.get("gap_to_leader"),
            "interval": gap.get("interval"),
        })
    rows.sort(key=lambda r: r["position"])
    return rows


def get_current_tyres(session_key) -> dict:
    """driver_number -> {compound, age} for the current (highest lap_start) stint."""
    stints = _get("stints", cache_s=15, session_key=session_key)
    latest: dict = {}
    for s in stints:
        dn = s.get("driver_number")
        if dn is None:
            continue
        if dn not in latest or (s.get("lap_start") or 0) > (latest[dn].get("lap_start") or 0):
            latest[dn] = s
    out = {}
    current_lap = get_current_lap_number(session_key)
    for dn, s in latest.items():
        age_at_start = s.get("tyre_age_at_start", 0) or 0
        laps_this_stint = max(0, current_lap - (s.get("lap_start") or current_lap))
        out[dn] = {"compound": s.get("compound", "?"), "age": age_at_start + laps_this_stint}
    return out


def get_weather(session_key) -> Optional[dict]:
    weather = _get("weather", cache_s=30, session_key=session_key)
    return weather[-1] if weather else None


def resolve_driver(session_key, identifier: str) -> Optional[dict]:
    """Match a driver by number, acronym (VER), or (partial) name."""
    drivers = get_drivers(session_key)
    identifier = identifier.strip()

    if identifier.isdigit():
        return drivers.get(int(identifier))

    ident_lower = identifier.lower()
    for d in drivers.values():
        if d.get("name_acronym", "").lower() == ident_lower:
            return d
    for d in drivers.values():
        if ident_lower in d.get("full_name", "").lower() or ident_lower in d.get("broadcast_name", "").lower():
            return d
    return None


# ---------------------------------------------------------------------------
# Race control event watching (for proactive Watchalong callouts)
# ---------------------------------------------------------------------------

def _classify_rc_message(message: str) -> str:
    text = (message or "").upper()
    if "SAFETY CAR" in text:
        return "safety_car"
    if "RED FLAG" in text:
        return "red_flag"
    if "CHEQUERED" in text:
        return "chequered_flag"
    if "PENALTY" in text or "INVESTIGAT" in text:
        return "penalty"
    if "DRS" in text:
        return "drs"
    if "YELLOW" in text:
        return "yellow_flag"
    return "other"


class LiveRaceWatcher:
    """
    Tracks what's already been seen for one session so repeated polling
    (main.py's proactive alert thread, or an ad-hoc 'anything happening?'
    tool call) only ever surfaces genuinely NEW events — race control
    messages, leader changes, and new fastest laps.
    """

    def __init__(self):
        self._session_key = None
        self._seen_rc_dates: set = set()
        self._last_leader: Optional[int] = None
        self._best_lap_time: Optional[float] = None
        self._bootstrapped = False

    def _reset_if_new_session(self, session_key):
        if session_key != self._session_key:
            self._session_key = session_key
            self._seen_rc_dates = set()
            self._last_leader = None
            self._best_lap_time = None
            self._bootstrapped = False

    def check_new_events(self, session_key) -> list[tuple[str, str]]:
        """
        Returns [(category, spoken_text), ...] for anything new since the
        last check. The very first call for a session only establishes the
        baseline (seen race control messages, current leader, best lap) and
        returns []  — otherwise switching into Watchalong mid-race would dump
        the entire session's backlog as a burst of "new" events, same as
        _check_msfs()'s first-read guard on flight-phase announcements.
        """
        self._reset_if_new_session(session_key)
        is_bootstrap = not self._bootstrapped
        self._bootstrapped = True
        events = []

        for rc in _get("race_control", cache_s=4, session_key=session_key):
            date = rc.get("date")
            if not date or date in self._seen_rc_dates:
                continue
            self._seen_rc_dates.add(date)
            message = rc.get("message", "")
            if not message:
                continue
            category = _classify_rc_message(message)
            events.append((category, message.capitalize()))

        positions = get_current_positions(session_key)
        if positions:
            leader = positions[0]
            if self._last_leader is not None and leader["driver_number"] != self._last_leader:
                events.append((
                    "leader_change",
                    f"{leader['full_name']} takes the lead.",
                ))
            self._last_leader = leader["driver_number"]

        laps = _get("laps", cache_s=4, session_key=session_key)
        for lap in laps:
            dur = lap.get("lap_duration")
            if not dur:
                continue
            if self._best_lap_time is None or dur < self._best_lap_time:
                self._best_lap_time = dur
                drivers = get_drivers(session_key)
                d = drivers.get(lap.get("driver_number"), {})
                events.append((
                    "fastest_lap",
                    f"Fastest lap -- {d.get('full_name', lap.get('driver_number'))}, {dur:.3f} seconds.",
                ))

        for pit in _get("pit", cache_s=4, session_key=session_key):
            date = pit.get("date")
            key = f"pit:{date}:{pit.get('driver_number')}"
            if not date or key in self._seen_rc_dates:
                continue
            self._seen_rc_dates.add(key)
            drivers = get_drivers(session_key)
            d = drivers.get(pit.get("driver_number"), {})
            dn = pit.get("driver_number")
            top5 = [r["driver_number"] for r in positions[:5]] if positions else []
            is_notable = dn in top5
            events.append((
                "pit_stop_top5" if is_notable else "pit_stop",
                f"{d.get('full_name', dn)} {_format_pit_duration(pit.get('pit_duration'))}.",
            ))

        # The loops above still need to run on bootstrap so seen-dates/
        # leader/best-lap state gets initialized to "current" — only the
        # resulting events themselves are suppressed for this first call.
        return [] if is_bootstrap else events


_watcher = LiveRaceWatcher()


def get_watcher() -> LiveRaceWatcher:
    return _watcher


# ---------------------------------------------------------------------------
# Historical race lookup (Watchalong Replay mode)
# ---------------------------------------------------------------------------

def list_recent_races(year: int = None) -> list[dict]:
    year = year or datetime.now(timezone.utc).year
    meetings = _get("meetings", cache_s=3600, year=year)
    return [
        {
            "name": m.get("meeting_name", "?"),
            "circuit": m.get("circuit_short_name", "?"),
            "country": m.get("country_name", "?"),
            "date": (m.get("date_start") or "")[:10],
            "meeting_key": m.get("meeting_key"),
            "year": m.get("year", year),
        }
        for m in meetings
    ]


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def search_races(query: str) -> list[dict]:
    """
    Fuzzy search across meeting name/circuit/country/year. Handles a bare
    year in the query ('Monaco 2024'), 'last year X' (resolved to
    current_year - 1), and unqualified names ('Spa' -> most recent match).
    """
    query = query.strip()
    query_lower = query.lower()
    now_year = datetime.now(timezone.utc).year

    if "last year" in query_lower:
        target_year = now_year - 1
        query_lower = query_lower.replace("last year", "").strip()
    else:
        year_match = _YEAR_RE.search(query)
        target_year = int(year_match.group()) if year_match else None
        if year_match:
            query_lower = query_lower.replace(year_match.group(), "").strip()

    # Search a small window of seasons rather than one year so "Spa" (no
    # year given) can match across the whole OpenF1-covered range (2023+).
    years_to_search = [target_year] if target_year else list(range(now_year, 2022, -1))

    candidates = []
    for y in years_to_search:
        candidates.extend(list_recent_races(year=y))

    if not query_lower:
        # Just a year (or "last year") with no name — return that year's races
        return candidates

    scored = []
    for c in candidates:
        haystack = f"{c['name']} {c['circuit']} {c['country']}".lower()
        score = max(
            SequenceMatcher(None, query_lower, haystack).ratio(),
            1.0 if query_lower in haystack else 0.0,
        )
        if score > 0.3:
            scored.append((score, c))

    scored.sort(key=lambda x: (-x[0], -x[1]["year"]))
    return [c for _, c in scored[:5]]


def get_race_session(meeting_key_or_name, session_type: str = "Race") -> Optional[dict]:
    """
    Resolve to one specific OpenF1 session (with its session_key), given
    either a meeting_key, or a free-text race name/query resolved via
    search_races().
    """
    session_name = normalize_session_type(session_type)

    if isinstance(meeting_key_or_name, int) or str(meeting_key_or_name).isdigit():
        meeting_key = int(meeting_key_or_name)
    else:
        matches = search_races(str(meeting_key_or_name))
        if not matches:
            return None
        meeting_key = matches[0]["meeting_key"]

    sessions = _get("sessions", cache_s=3600, meeting_key=meeting_key)
    match = None
    for s in sessions:
        if s.get("session_name") == session_name:
            match = s
            break
    if match is None:
        # Fall back to a loose match (e.g. "Sprint Qualifying" vs "Sprint Shootout")
        for s in sessions:
            if session_name.lower() in (s.get("session_name") or "").lower():
                match = s
                break
    if match is None:
        return None

    match = _enrich_with_meeting(dict(match))
    # /sessions has no total_laps field — derive it from the highest lap
    # number seen in /laps (0 for a session that hasn't happened yet).
    laps = _get("laps", cache_s=3600, session_key=match["session_key"])
    match["total_laps"] = max((l.get("lap_number", 0) for l in laps), default=0)
    return match


def get_lap_data(session_key, lap_number: int) -> dict:
    """
    Complete state AT the end of a specific lap. SPOILER PROTECTION: every
    sub-fetch here is filtered to lap_number (this lap's own events) or a
    cutoff timestamp derived from this lap (for "state as of now" data like
    positions/tyres) — nothing later than this lap is ever included.
    """
    laps_this = _get("laps", cache_s=3600, session_key=session_key, lap_number=lap_number)
    drivers = get_drivers(session_key)

    # Cutoff = the latest moment any driver's lap_number<=requested lap
    # ended. Used to bound position/tyre "current state" lookups so they
    # never leak data from later laps. SAFETY: do not widen this to "session
    # end" or spoilers leak into a replay narrative.
    laps_upto = _get("laps", cache_s=3600, session_key=session_key, **{"lap_number<=": lap_number})
    cutoff = None
    for l in laps_upto:
        start = _parse_dt(l.get("date_start"))
        dur = l.get("lap_duration")
        if start and dur:
            end = start + timedelta(seconds=dur)
            if cutoff is None or end > cutoff:
                cutoff = end
    cutoff_iso = cutoff.isoformat() if cutoff else None

    positions_all = _get("position", cache_s=3600, session_key=session_key, **{"date<=": cutoff_iso}) if cutoff_iso else []
    latest_pos = {}
    for p in positions_all:
        dn = p.get("driver_number")
        if dn is None:
            continue
        if dn not in latest_pos or p["date"] > latest_pos[dn]["date"]:
            latest_pos[dn] = p

    stints_upto = _get("stints", cache_s=3600, session_key=session_key, **{"lap_start<=": lap_number})
    latest_stint = {}
    for s in stints_upto:
        dn = s.get("driver_number")
        if dn is None:
            continue
        if dn not in latest_stint or (s.get("lap_start") or 0) > (latest_stint[dn].get("lap_start") or 0):
            latest_stint[dn] = s

    pits_this = _get("pit", cache_s=3600, session_key=session_key, lap_number=lap_number)
    rc_this = _get("race_control", cache_s=3600, session_key=session_key, lap_number=lap_number)

    weather_upto = _get("weather", cache_s=3600, session_key=session_key, **{"date<=": cutoff_iso}) if cutoff_iso else []
    weather = weather_upto[-1] if weather_upto else None

    positions = []
    for dn, p in latest_pos.items():
        d = drivers.get(dn, {})
        stint = latest_stint.get(dn, {})
        age_at_start = stint.get("tyre_age_at_start", 0) or 0
        laps_this_stint = max(0, lap_number - (stint.get("lap_start") or lap_number))
        positions.append({
            "driver_number": dn,
            "name": d.get("full_name", str(dn)),
            "acronym": d.get("name_acronym", str(dn)),
            "team": d.get("team_name", "?"),
            "position": p.get("position", 99),
            "compound": stint.get("compound", "?"),
            "tyre_age": age_at_start + laps_this_stint,
        })
    positions.sort(key=lambda r: r["position"])

    return {
        "lap_number": lap_number,
        "positions": positions,
        "laps_this_lap": [
            {"driver": drivers.get(l.get("driver_number"), {}).get("full_name", l.get("driver_number")),
             "lap_duration": l.get("lap_duration")}
            for l in laps_this if l.get("lap_duration")
        ],
        "pit_stops": [
            {"driver": drivers.get(p.get("driver_number"), {}).get("full_name", p.get("driver_number")),
             "duration": p.get("pit_duration")}
            for p in pits_this
        ],
        "race_control": [rc.get("message", "") for rc in rc_this if rc.get("message")],
        "weather": weather,
    }


def _format_pit_duration(seconds) -> str:
    if seconds is None:
        return "pits"
    if seconds > 90:
        # OpenF1's pit_duration during a red flag/long stoppage reports the
        # ENTIRE time spent in the pits, not just genuine pit-lane time — a
        # "2358 second stop" is real data but reads as nonsense spoken
        # aloud, so call out the stoppage instead of the literal number.
        return "pits for an extended stop (likely a red flag or lengthy stoppage)"
    return f"pits -- a {seconds:.1f} second stop"


def get_lap_narrative(session_key, lap_number: int) -> str:
    """
    A 2-4 sentence spoken summary of a specific lap: position changes,
    fastest lap of the lap, pit stops, incidents, gaps. Never references
    anything beyond lap_number — see get_lap_data()'s spoiler-safety note.
    """
    data = get_lap_data(session_key, lap_number)
    parts = [f"Lap {lap_number}:"]

    if data["pit_stops"]:
        # Cap how many individual stops get named — a mass-pit lap (e.g.
        # everyone pitting under a red flag) would otherwise blow way past
        # the "2-4 sentence" spoken-summary target.
        stops = data["pit_stops"]
        for p in stops[:2]:
            parts.append(f"{p['driver']} {_format_pit_duration(p['duration'])}.")
        if len(stops) > 2:
            parts.append(f"Plus {len(stops) - 2} more cars pitting this lap.")

    if data["positions"]:
        leader = data["positions"][0]
        parts.append(f"{leader['name']} leads on {leader['compound']} tyres ({leader['tyre_age']} laps old).")
        if len(data["positions"]) > 1:
            p2 = data["positions"][1]
            parts.append(f"{p2['name']} is P2 in second, on {p2['compound']}.")

    if data["laps_this_lap"]:
        fastest = min(data["laps_this_lap"], key=lambda l: l["lap_duration"])
        parts.append(f"Fastest lap of the lap: {fastest['driver']}, {fastest['lap_duration']:.3f}s.")

    for msg in data["race_control"][:2]:
        parts.append(msg.capitalize().rstrip(".") + ".")

    return " ".join(parts)
