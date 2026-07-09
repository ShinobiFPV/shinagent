"""
IMQ2 Radio DJ Tools
Q2-facing wrappers over integrations/radio_dj.py. Generates themed DJ sets
(LLM + web research), starts/stops/skips the session, and answers
in-session Q&A about the current track. See integrations/radio_dj.py's
module docstring for what "plays via YouTube" actually means here --
there's no local audio SDK in this codebase, so playback means opening a
resolved YouTube video on the connected display, not a synchronized,
position-aware player.
"""
import json
import logging
import random

log = logging.getLogger(__name__)

_VALID_STYLES = ("back_announce", "pre_announce", "mid_song", "liner_notes")


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def _research_theme(theme: str) -> str:
    """Web-search context for the generation prompt, same
    TAVILY-backed search the web_search tool itself uses -- reused
    directly rather than duplicating the API call here."""
    try:
        from tools.registry import WebSearchTool
        query = f"{theme} music history artists albums production notes facts"
        return WebSearchTool().run(query=query)
    except Exception as e:
        log.warning(f"radio_dj: theme research failed: {e}")
        return ""


def _time_of_day_theme() -> str:
    import datetime
    hour = datetime.datetime.now().hour
    if 6 <= hour < 12:
        return "upbeat morning music -- positive, easing into the day"
    if 12 <= hour < 18:
        return "varied mid-energy music for the afternoon"
    if 18 <= hour < 22:
        return "social, peak-energy music for the evening"
    if 22 <= hour < 26 or hour < 2:
        return "late-night, introspective music"
    return "deep-night, ambient, minimal music (it's the small hours)"


def _build_generation_prompt(theme: str, style: str, track_count: int, mood: str, research: str) -> str:
    return f"""You are Q2, a knowledgeable radio DJ. Generate a DJ set.

Theme: {theme}
Style: {style}
Tracks: {track_count}
Mood arc: {mood}

Research context:
{research or "(no web search results available -- rely on your own knowledge, and hedge with 'reportedly'/'allegedly' on anything you're not fully certain of)"}

Generate a complete DJ set as JSON:
{{
  "theme": "Exact theme title",
  "description": "2-3 sentence intro Q2 will speak",
  "mood_arc": "How energy evolves through the set",
  "tracks": [
    {{
      "title": "Song title",
      "artist": "Artist name",
      "album": "Album name",
      "year": 1997,
      "duration_s": 240,
      "youtube_query": "Artist Song Title official audio",
      "pre_announce": "What Q2 says BEFORE playing (1-3 sentences)",
      "back_announce": "What Q2 says AFTER playing (2-4 sentences)",
      "mid_song_note": "What Q2 says 20s in (cold open, 1-2 sentences)",
      "connection_to_next": "What connects this to the next song",
      "facts": ["interesting fact 1", "interesting fact 2"]
    }}
  ]
}}

Requirements:
- Every track choice must have a strong reason
- back_announce must contain at least one fact the listener likely doesn't know (production secret, history, story)
- connection_to_next must be specific (same producer, same studio, sampled this track, answered this song, feuded, collaborated, etc.)
- youtube_query should find the song easily on YouTube
- Facts must be accurate -- only include verifiable claims, hedge with "reportedly"/"allegedly" on anything uncertain, never invent chart positions, Grammy wins, or dates
- The set should have narrative coherence -- it tells a story
- mood_arc should be felt: don't just list "energetic tracks"

Output ONLY valid JSON. No other text."""


def generate_dj_set(theme: str = "free choice", style: str = None,
                     track_count: int = None, mood: str = None) -> str:
    """
    Research the theme, generate a complete DJ set with the active LLM
    backend, and start playing it. Returns a spoken-word intro.
    style/track_count/mood fall back to config.yaml's radio_dj.default_*
    when not given explicitly (None), rather than a hardcoded default,
    so those config values are actually load-bearing.
    """
    try:
        from config.loader import config

        if not config.get("radio_dj.enabled", True):
            return "[generate_dj_set] Radio DJ mode is disabled in config.yaml (radio_dj.enabled)."

        from core.llm import get_llm_backend
        from integrations.radio_dj import DJSession, DJTrack, get_controller, save_session

        if style is None:
            style = config.get("radio_dj.default_style", "back_announce")
        if track_count is None:
            track_count = config.get("radio_dj.default_track_count", 5)
        if mood is None:
            mood = config.get("radio_dj.default_mood", "journey")

        style = style if style in _VALID_STYLES else "back_announce"
        track_count = max(3, min(10, int(track_count or 5)))

        chosen_reason = ""
        if not theme or theme.strip().lower() in ("free choice", "you choose", "your choice", ""):
            theme = _time_of_day_theme()
            chosen_reason = " (picked for the time of day)"

        research = _research_theme(theme)
        prompt = _build_generation_prompt(theme, style, track_count, mood, research)

        llm = get_llm_backend()
        response = llm.complete(
            messages=[{"role": "user", "content": "Generate the DJ set now."}],
            system=prompt,
            max_tokens=4000,
        )

        try:
            parsed = _parse_llm_json(response.text)
        except Exception as e:
            log.error(f"radio_dj: failed to parse LLM response as JSON: {e}\nRaw: {response.text[:500]}")
            return "[generate_dj_set] The set generator returned something that wasn't valid JSON -- try again."

        tracks = [DJTrack.from_dict(t) for t in parsed.get("tracks", [])]
        if not tracks:
            return "[generate_dj_set] Generation produced no tracks -- try a different theme."

        session = DJSession(
            theme=parsed.get("theme", theme),
            description=parsed.get("description", ""),
            tracks=tracks,
            dj_style=style,
            mood_arc=parsed.get("mood_arc", ""),
        )
        save_session(session, name="last_session")

        get_controller().start_session(session)

        return (f"Starting a {len(tracks)}-track set{chosen_reason}: {session.theme}. "
                f"{session.description}").strip()

    except Exception as e:
        log.error(f"generate_dj_set error: {e}", exc_info=True)
        return f"[generate_dj_set] Error: {e}"


def start_preset_set(preset: str) -> str:
    """Start one of the curated preset sets (personality/dj_presets/*.json)
    immediately, with no LLM generation needed -- used by the settings
    panel's Quick Start buttons and by voice ('play the UK Garage set')."""
    try:
        from config.loader import config
        if not config.get("radio_dj.enabled", True):
            return "[start_preset_set] Radio DJ mode is disabled in config.yaml (radio_dj.enabled)."

        from integrations.radio_dj import load_preset, get_controller, list_preset_names

        session = load_preset(preset)
        if not session:
            available = ", ".join(list_preset_names()) or "none available"
            return f"[start_preset_set] No preset named '{preset}'. Available: {available}."

        get_controller().start_session(session)
        return f"Starting the {session.theme} set. {session.description}".strip()

    except Exception as e:
        log.error(f"start_preset_set error: {e}", exc_info=True)
        return f"[start_preset_set] Error: {e}"


def dj_status() -> str:
    """Current DJ session status -- theme, track, progress."""
    try:
        from integrations.radio_dj import get_controller

        status = get_controller().get_status()
        if not status.get("active"):
            return "No DJ session active."

        return (f"Playing track {status['track_number']} of {status['total_tracks']}: "
                f"{status['current_artist']} -- {status['current_track']}. "
                f"Theme: {status['theme']}. {status['elapsed_min']} minutes in.")
    except Exception as e:
        log.error(f"dj_status error: {e}", exc_info=True)
        return f"[dj_status] Error: {e}"


def dj_skip() -> str:
    """Skip the current track and move to the next in the DJ set."""
    try:
        from integrations.radio_dj import get_controller

        ctrl = get_controller()
        if not ctrl.get_status().get("active"):
            return "No session active."
        ctrl.skip_track()
        return "Skipping to the next track."
    except Exception as e:
        log.error(f"dj_skip error: {e}", exc_info=True)
        return f"[dj_skip] Error: {e}"


def dj_stop() -> str:
    """Stop the current DJ session."""
    try:
        from integrations.radio_dj import get_controller

        ctrl = get_controller()
        if not ctrl.get_status().get("active"):
            return "No session active."
        ctrl.stop()
        return "DJ session ended."
    except Exception as e:
        log.error(f"dj_stop error: {e}", exc_info=True)
        return f"[dj_stop] Error: {e}"


def dj_track_info() -> str:
    """Facts about the currently playing track, for in-session Q&A."""
    try:
        from integrations.radio_dj import get_controller

        ctrl = get_controller()
        status = ctrl.get_status()
        if not status.get("active"):
            return "No track playing."

        sess = ctrl._session
        idx = sess.current_idx
        if idx >= len(sess.tracks):
            return "No track playing."
        track = sess.tracks[idx]

        if track.facts:
            return random.choice(track.facts)
        if track.back_announce:
            return f"{track.title} by {track.artist}, {track.year or '?'}. {track.back_announce}"
        return f"{track.title} by {track.artist}, {track.year or '?'}."
    except Exception as e:
        log.error(f"dj_track_info error: {e}", exc_info=True)
        return f"[dj_track_info] Error: {e}"


_PRESET_DESCRIPTIONS = [
    "UK Garage 1999-2003: The three years that defined a genre",
    "The Quincy Jones files: Artists he shaped but doesn't get credit for",
    "Samples that became bigger than the originals",
    "Three degrees of separation: Six Degrees of Kendrick Lamar",
    "Late night Tokyo: City pop and its global descendants",
    "The Neptunes production timeline: 2001-2004",
    "Songs recorded in a single take",
    "One studio, five decades: Abbey Road across the years",
    "The feud set: Songs written as responses to other songs",
    "Producer spotlight: Rick Rubin's quietest years",
    "The Amen break: Every song that used the same 6-second drum loop",
    "Songs that almost weren't: Tracks that nearly got shelved",
    "The 3am set: Music for the hours that don't count",
    "Madlib deep cuts: Tracks most people missed",
    "When pop stars made jazz: Career pivots nobody expected",
]


def list_dj_presets() -> str:
    """List available DJ set themes/ideas Q2 can generate. Note: these are
    generation IDEAS for generate_dj_set's theme parameter, distinct from
    the ready-to-play preset sets in personality/dj_presets/ (see
    start_preset_set) -- only three of these fifteen ideas currently have
    a pre-built, no-generation-needed version."""
    lines = [f"{i + 1}. {p}" for i, p in enumerate(_PRESET_DESCRIPTIONS)]
    return "DJ set ideas (say a theme, or 'free choice' and I'll pick):\n" + "\n".join(lines)
