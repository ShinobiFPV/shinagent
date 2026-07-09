"""
IMQ2 Watchalong -- Pop-Up Video mode
MTV Pop Up Video style: before viewing, research a film/TV title via web
search and generate timestamped fact bubbles with the active LLM backend;
during viewing, deliver the bubble for whatever timestamp the user calls
out, and push it to the companion panel (webapp/popup_companion.html) for
visual display.
"""
import json
import logging
import os

import requests

from config.loader import config
from integrations.popup_video import PopUpLibrary, PopUpSession, slugify, POPUP_TYPES

log = logging.getLogger(__name__)

library = PopUpLibrary()


def _web_search(query: str, max_results: int = 3) -> list:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        log.warning(f"popup_video web search failed for '{query}': {e}")
        return []


def _research_title(title: str, year, web_search_fn=_web_search) -> str:
    y = f" {year}" if year else ""
    queries = [
        f"{title}{y} production trivia facts IMDB",
        f"{title}{y} behind the scenes filming locations",
        f"{title}{y} cast facts actors",
        f"{title}{y} soundtrack music",
        f"{title}{y} mistakes errors goofs continuity",
        f"{title}{y} historical accuracy real events",
        f"{title}{y} director writer production notes",
    ]
    findings = []
    for q in queries:
        for res in web_search_fn(q):
            snippet = (res.get("content") or "")[:400]
            if snippet:
                findings.append(f"- {res.get('title', '')}: {snippet}")
    if not findings:
        return "No web search results found -- generate from your own knowledge of the title."
    return "\n".join(findings[:30])


def _build_popup_prompt(title: str, year, media_type: str, episode: str, research: str) -> str:
    subject = f"{title} ({year})" if year else title
    if media_type == "tv" and episode:
        subject += f", episode {episode}"
    runtime_note = "spread across ~45 minutes" if media_type == "tv" else "spread across the full runtime"
    return f"""You are generating Pop Up Video style fact bubbles for: {subject}

Research gathered:
{research}

Generate 25-40 pop-up facts distributed across the runtime ({runtime_note}).

Each pop-up should be:
- Short: 1-3 sentences maximum
- Specific: name names, cite numbers, give context
- Interesting: production secrets, surprising connections, real events behind
  fictional ones, what happened to cast
- Varied: mix types throughout

Categories to include:
CAST facts -- actor backgrounds, connections to other work, real stories from
  set, who almost played the role
MUSIC facts -- song names, artists, why chosen, artist context
LOCATION -- where scenes were actually filmed, what buildings are
TECH -- how effects were achieved, camera techniques, practical vs CGI
CORRECTION -- what's technically wrong, anachronisms, mistakes
HISTORY -- real events the film references or is based on
FACT -- general trivia that doesn't fit other categories
EASTER_EGG -- hidden references, callbacks, in-jokes

Timestamps should be plausible for the content -- music facts when music
plays, cast facts when that actor appears, etc.

Output ONLY a JSON array. No other text, no markdown fences. Format:
[
  {{
    "timestamp_seconds": 60,
    "timestamp_display": "1:00",
    "type": "CAST",
    "title": "Short headline (max 6 words)",
    "body": "The fact in 1-3 sentences.",
    "source": "imdb_trivia"
  }}
]
"""


def _parse_popup_json(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text.strip())
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of pop-ups")
    return data


def _push_to_companion(popup: dict, session: PopUpSession):
    try:
        port = config.get("webapp.port", 8766)
        requests.post(
            f"http://localhost:{port}/popup/api/deliver",
            json={"popup": popup, "title": session.title, "year": session.year},
            timeout=3,
        )
    except Exception as e:
        log.warning(f"popup_video: could not push to companion panel: {e}")


def _parse_timestamp(timestamp_str: str) -> int:
    """Parse '7:23', '1:02:30', '40 minutes', 'forty minutes in', '7' (minutes)."""
    s = timestamp_str.strip().lower()
    s = s.replace(" in", "").replace("minutes", "").replace("minute", "").strip()

    if ":" in s:
        parts = [p.strip() for p in s.split(":")]
        try:
            parts = [int(p) for p in parts]
        except ValueError:
            parts = None
        if parts:
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]

    number_words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
        "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
        "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }
    try:
        return int(float(s)) * 60
    except ValueError:
        pass

    # Group consecutive number-words into separate numbers, combining a tens
    # word (twenty, thirty, ...) with an immediately following ones word
    # (twenty-three -> 23) but otherwise starting a new number each time --
    # so "seven twenty-three" reads as two numbers (7, 23), i.e. 7:23, the
    # same way "twenty-three" alone reads as one number (23) meaning 23
    # minutes.
    words = s.replace("-", " ").split()
    groups = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in number_words:
            val = number_words[w]
            if val >= 20 and i + 1 < len(words) and words[i + 1] in number_words and number_words[words[i + 1]] < 10:
                val += number_words[words[i + 1]]
                i += 1
            groups.append(val)
        i += 1

    if len(groups) == 1:
        return groups[0] * 60
    if len(groups) >= 2:
        return groups[0] * 60 + groups[1]
    raise ValueError(f"Could not parse timestamp: {timestamp_str!r}")


def get_active_session() -> "PopUpSession | None":
    slug = config.get("popup_video.active_title_slug")
    if not slug:
        return None
    return library.get(slug)


def generate_popups(title: str, year: int = None, episode: str = None) -> str:
    """
    Research a film/TV title and generate a full Pop-Up Video style set of
    timestamped fact bubbles with the active LLM backend. Saves the session
    and activates it. Returns a spoken-word confirmation.
    """
    try:
        from core.llm import get_llm_backend

        media_type = "tv" if episode else "movie"
        session = PopUpSession(title=title, year=year, media_type=media_type, episode=episode or "")

        llm = get_llm_backend()
        count = session.generate(llm, _web_search)
        if count == 0:
            return f"[generate_popups] No pop-ups could be generated for '{title}' -- try again or check the title."

        library.save(session)

        config.raw.setdefault("popup_video", {})
        config.raw["popup_video"]["active_title"] = title
        config.raw["popup_video"]["active_title_slug"] = session.slug
        config.raw["popup_video"]["auto_advance"] = config.raw["popup_video"].get("auto_advance", False)
        config.save()

        last_ts = session.popups[-1]["timestamp_display"] if session.popups else "?"
        return (
            f"Pop-up video ready for {title}{f' ({year})' if year else ''}. "
            f"Generated {count} pop-ups across the runtime (last one at {last_ts}). "
            f"Say a timestamp any time -- I'll give you the pop-up for that moment. "
            f"Opening title card when you're ready."
        )
    except Exception as e:
        log.error(f"generate_popups error: {e}", exc_info=True)
        return f"[generate_popups] Error: {e}"


def get_popup(timestamp: str) -> str:
    """
    Get the pop-up fact for the current timestamp during Pop-Up viewing and
    push it to the companion panel.
    """
    session = get_active_session()
    if session is None:
        return "[get_popup] No active pop-up session -- say what you're watching first."
    try:
        ts = _parse_timestamp(timestamp)
    except ValueError as e:
        return f"[get_popup] {e}"

    popup = session.get_popup_at(ts)
    session.current_ts = ts
    library.save(session)

    if popup is None:
        upcoming = session.get_upcoming(ts, count=1)
        if upcoming:
            return f"Nothing queued for that moment -- try {upcoming[0]['timestamp_display']}, that's a good one."
        return "Nothing queued for that moment."

    _push_to_companion(popup, session)
    return f"[{popup['type']}] {popup['title']} -- {popup['body']}"


def get_next_popups(timestamp: str, count: int = 3) -> str:
    """Return the next N upcoming pop-up headlines with timestamps."""
    session = get_active_session()
    if session is None:
        return "[get_next_popups] No active pop-up session -- say what you're watching first."
    try:
        ts = _parse_timestamp(timestamp)
    except ValueError as e:
        return f"[get_next_popups] {e}"

    upcoming = session.get_upcoming(ts, count=count)
    if not upcoming:
        return "No more pop-ups queued for the rest of this one."

    parts = [f"{p['timestamp_display']} {p['type']} -- {p['title']}" for p in upcoming]
    return "Coming up: " + ", ".join(parts)


def list_popup_titles() -> str:
    """List all films and shows with saved pop-up data."""
    titles = library.list_titles()
    if not titles:
        return "No pop-up video sessions saved yet."
    lines = ["Saved pop-up sessions:"]
    for t in titles:
        y = f" ({t['year']})" if t.get("year") else ""
        lines.append(f"- {t['title']}{y} -- {t['popup_count']} pop-ups")
    return "\n".join(lines)


def clear_popup_session() -> str:
    """Clear the active pop-up session (for starting over or switching titles)."""
    config.raw.setdefault("popup_video", {})
    config.raw["popup_video"]["active_title"] = None
    config.raw["popup_video"]["active_title_slug"] = None
    config.save()
    return "Pop-up session cleared."


def set_popup_title(title: str, year: int = None) -> str:
    """Load an existing saved pop-up session without regenerating."""
    slug = slugify(title, year)
    session = library.get(slug)
    if session is None:
        return f"[set_popup_title] No saved pop-ups for '{title}' -- say generate to create them first."

    config.raw.setdefault("popup_video", {})
    config.raw["popup_video"]["active_title"] = session.title
    config.raw["popup_video"]["active_title_slug"] = session.slug
    config.save()
    return f"{session.title} loaded -- {len(session.popups)} pop-ups ready. Call out a timestamp any time."
