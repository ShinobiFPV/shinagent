"""
IMQ2 Game Companion Tools
Game-agnostic co-op partner tools, wrapping integrations/game_companion.py's
GENRES/KNOWN_GAMES data and GameSession/GameHistory state.
"""
import logging
import time

log = logging.getLogger(__name__)


def start_game_session(
    game_name:      str,
    platform:       str = "",
    character_info: str = "",
    spoiler_level:  str = "ask",
) -> str:
    """Start a new Game Companion session -- call when the user says he's
    playing a game. Archives any previous session for a *different* game
    to history first."""
    try:
        from config.loader import config
        if not config.get("game_companion.enabled", True):
            return "[start_game_session] Game Companion mode is disabled in config.yaml (game_companion.enabled)."

        from integrations.game_companion import (
            GameSession, GENRES, get_session, set_session, get_history, identify_game,
        )

        existing = get_session()
        if existing and existing.game_name.lower() != game_name.lower():
            get_history().record(existing)

        game_data = identify_game(game_name)
        genre = game_data.get("genre", "rpg")
        genre_info = GENRES.get(genre, GENRES["other"])
        is_known = not game_data.get("unknown", False)

        if not spoiler_level.strip().lower() in ("ask", "minimal", "full"):
            spoiler_level = "ask"

        session = GameSession(
            game_name=game_name,
            genre=genre,
            platform=platform,
            character_info=character_info,
            spoiler_level=spoiler_level,
            game_data=game_data,
        )
        set_session(session)

        lines = [f"Game Companion session started: {game_name}"]

        if is_known:
            lines.append(f"Genre: {genre_info['name']}")
            dev = game_data.get("developer", "")
            year = game_data.get("year", "")
            if dev:
                lines.append(f"Developer: {dev} ({year})")
            if game_data.get("description"):
                lines.append(game_data["description"])
            if game_data.get("key_systems"):
                lines.append("Key systems: " + ", ".join(game_data["key_systems"][:4]))
            if game_data.get("beginner_tips") and not character_info:
                lines.append("Beginner tips: " + "; ".join(game_data["beginner_tips"][:3]))
        else:
            lines.append(f"Genre (guessed): {genre_info['name']}")
            lines.append("Not in my known-games database -- I'll search for game-specific info as needed.")

        if spoiler_level == "ask":
            lines.append("Ask how far into the game they are before giving any story/location hints.")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"start_game_session error: {e}", exc_info=True)
        return f"[start_game_session] Error: {e}"


def get_game_session() -> str:
    """Get the current game session context. Call at the start of any
    game-related response to ground it in the active session."""
    try:
        from integrations.game_companion import get_session
        session = get_session()
        if not session:
            return "No active game session. Say 'I'm playing [game]' to start."
        return session.context_summary()
    except Exception as e:
        log.error(f"get_game_session error: {e}", exc_info=True)
        return f"[get_game_session] Error: {e}"


def update_game_session(
    progress_note:  str = "",
    current_area:   str = "",
    character_info: str = "",
    stuck_on:       str = "",
    tried:          str = "",
    spoiler_level:  str = "",
) -> str:
    """Update the current game session with new info the user shares.
    All parameters optional -- only pass what changed."""
    try:
        from integrations.game_companion import get_session
        session = get_session()
        if not session:
            return "[update_game_session] No active game session."

        updated = []

        if progress_note:
            session.progress_notes.append(progress_note)
            updated.append(f"progress: {progress_note}")
        if current_area:
            session.current_area = current_area
            updated.append(f"area: {current_area}")
        if character_info:
            session.character_info = character_info
            updated.append(f"character: {character_info}")
        if stuck_on:
            session.stuck_on = stuck_on
            updated.append(f"stuck on: {stuck_on}")
        if tried:
            session.tried.append(tried)
            updated.append(f"tried: {tried}")
        if spoiler_level in ("minimal", "full", "ask"):
            session.spoiler_level = spoiler_level
            updated.append(f"spoilers: {spoiler_level}")

        if updated:
            return "Updated session: " + ", ".join(updated)
        return "No changes made."

    except Exception as e:
        log.error(f"update_game_session error: {e}", exc_info=True)
        return f"[update_game_session] Error: {e}"


def get_boss_help(boss_name: str) -> str:
    """Help with a specific boss or difficult encounter -- formats
    context for web search and advice. Records stuck_on on the session."""
    try:
        from integrations.game_companion import get_session
        session = get_session()
        game = session.game_name if session else "the game"

        if session:
            session.stuck_on = boss_name

        lines = [
            f"Boss help requested: {boss_name} in {game}",
            f"Search for: '{game} {boss_name} guide strategy {time.strftime('%Y')}'",
            f"Also search: '{game} {boss_name} weaknesses'",
        ]

        if session and session.character_info:
            lines.append(f"User's build: {session.character_info}")
            lines.append(f"Search for: '{game} {boss_name} {session.character_info} build'")

        if session and session.tried:
            lines.append(f"Already tried: {', '.join(session.tried[-3:])}")

        lines.append("Remember to: ask what they've already tried if not known; give a general "
                      "tip before the specific strategy; search for current-patch strategies; "
                      "consider their build/class if known.")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_boss_help error: {e}", exc_info=True)
        return f"[get_boss_help] Error: {e}"


def get_build_advice(playstyle: str = "", constraints: str = "") -> str:
    """Build/loadout advice for the current game -- formats context for
    a current-meta web search."""
    try:
        from integrations.game_companion import get_session
        session = get_session()
        if not session:
            return "[get_build_advice] No active game session."

        game = session.game_name
        genre = session.genre

        lines = [
            f"Build advice for: {game}",
            f"User's playstyle: {playstyle or 'not specified'}",
            f"Constraints: {constraints or 'none'}",
            f"Current build: {session.character_info or 'not specified'}",
            f"Search for: '{game} best build {time.strftime('%Y')}'",
        ]
        if playstyle:
            lines.append(f"Search for: '{game} {playstyle} build guide'")

        if genre in ("action_rpg", "rpg"):
            lines.append("For RPG builds, cover: main stat to invest in, recommended weapons/skills, "
                          "key passive/perk/talent choices, gear priorities.")
        elif genre == "shooter":
            lines.append("For shooter loadouts, cover: primary weapon + attachments, secondary, "
                          "perks/abilities, playstyle tips.")
        elif genre == "strategy":
            lines.append("For strategy, cover: opening build order, early economy targets, "
                          "tech path, unit composition.")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_build_advice error: {e}", exc_info=True)
        return f"[get_build_advice] Error: {e}"


def get_progression_hint(vague_first: bool = True) -> str:
    """Help figure out where to go or what to do next -- respects the
    session's spoiler preference."""
    try:
        from integrations.game_companion import get_session
        session = get_session()
        if not session:
            return "[get_progression_hint] No active game session."

        lines = [
            f"Progression help for: {session.game_name}",
            f"Current area: {session.current_area or 'unknown'}",
            f"Spoiler level: {session.spoiler_level}",
            f"Progress: {'; '.join(session.progress_notes[-3:]) or 'not noted'}",
        ]

        if session.spoiler_level == "ask":
            lines.append("SPOILER CHECK: Ask the user how far they are before giving directional hints.")
        elif session.spoiler_level == "minimal":
            lines.append("SPOILER MODE: Directional hints only, no story spoilers -- "
                          "'head north of where you found X', not 'go trigger the next story beat'.")
        else:
            lines.append("Full spoilers OK.")

        if vague_first:
            lines.append("Start with a vague hint, then offer more detail if they want it.")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_progression_hint error: {e}", exc_info=True)
        return f"[get_progression_hint] Error: {e}"


def search_game_knowledge(query: str) -> str:
    """Format a game-specific knowledge search for Q2 to web search after
    calling this."""
    try:
        from integrations.game_companion import get_session
        session = get_session()
        game = session.game_name if session else ""
        wiki = session.game_data.get("wiki", "") if session else ""

        lines = [f"Game knowledge search: {query}"]
        if game:
            lines.append(f"Context: {game}")
            lines.append(f"Primary search: '{game} {query}'")
            if wiki:
                lines.append(f"Check wiki: {wiki}")
        else:
            lines.append(f"Search: '{query} game guide'")

        lines.append("After searching, synthesize the answer and note if it might be patch-specific.")
        return "\n".join(lines)

    except Exception as e:
        log.error(f"search_game_knowledge error: {e}", exc_info=True)
        return f"[search_game_knowledge] Error: {e}"


def end_game_session() -> str:
    """End the current game session and archive it to history."""
    try:
        from integrations.game_companion import get_session, get_history, clear_session
        session = get_session()
        if not session:
            return "[end_game_session] No active session to end."

        duration_min = (time.time() - session.started_at) / 60
        get_history().record(session)
        clear_session()

        return (f"Game session ended: {session.game_name}. "
                f"Session lasted {duration_min:.0f} minutes. Progress saved to history.")

    except Exception as e:
        log.error(f"end_game_session error: {e}", exc_info=True)
        return f"[end_game_session] Error: {e}"


def list_recent_games() -> str:
    """List recently played games from session history."""
    try:
        from integrations.game_companion import get_history
        entries = get_history().recent(8)
        if not entries:
            return "No game history yet."

        lines = ["Recent games:"]
        for entry in entries:
            name = entry.get("game_name", "Unknown")
            duration = (entry.get("ended_at", 0) - entry.get("started_at", 0)) / 60
            notes = entry.get("progress_notes", [])
            note_str = notes[-1] if notes else ""
            lines.append(f"  {name} ({duration:.0f} min)" + (f" -- {note_str}" if note_str else ""))
        return "\n".join(lines)

    except Exception as e:
        log.error(f"list_recent_games error: {e}", exc_info=True)
        return f"[list_recent_games] Error: {e}"


def get_game_info(game_name: str = "") -> str:
    """Database info about a game -- developer, genre, key systems, wiki
    link. Uses the current session's game if none specified."""
    try:
        from integrations.game_companion import get_session, identify_game
        if not game_name:
            session = get_session()
            if session:
                game_name = session.game_name
            else:
                return "[get_game_info] Specify a game name or start a session."

        data = identify_game(game_name)
        if data.get("unknown"):
            return f"'{game_name}' not in my database. I'll use web search for info about it."

        lines = [f"=== {game_name} ==="]
        if data.get("developer"):
            lines.append(f"Developer: {data['developer']} ({data.get('year', '')})")
        if data.get("description"):
            lines.append(data["description"])
        if data.get("key_systems"):
            lines.append("Key systems: " + ", ".join(data["key_systems"]))
        if data.get("wiki"):
            lines.append(f"Wiki: {data['wiki']}")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"get_game_info error: {e}", exc_info=True)
        return f"[get_game_info] Error: {e}"
