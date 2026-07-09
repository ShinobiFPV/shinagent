"""
IMQ2 NBA Analyst Tools
Spoken-text wrappers over integrations/nba_data.py, used by both
watchalong agent modes (personality/profiles/watchalong_live.yaml and
watchalong_replay.yaml) whenever config.yaml's watchalong.active_sport is
"nba" -- see integrations/nba_data.py's module docstring for the
two-profile, per-sport-config architecture shared with F1/UFC/NHL.

Unlike F1/UFC, NBA replay has no persistent "active session" stored in
config.yaml -- get_nba_replay_period() is self-contained (game_date +
team identify the game on every call), since there's no equivalent to a
lap-by-lap callout flow that benefits from remembering which game is active.
"""
import logging
import random

log = logging.getLogger(__name__)


def get_nba_status(fields: str = "summary") -> str:
    """Get live NBA game status. fields: 'summary' | 'score'"""
    try:
        from integrations.nba_data import get_nba, NBAClient

        nba = get_nba()
        if not nba._game_id:
            game = nba.detect_live_game()
            if game:
                nba.set_game(game.get("id"))
        status = nba.get_status()

        if not status.get("active"):
            client = NBAClient()
            games = client.get_todays_games()
            if not games:
                return "No NBA games today."
            game = games[0]
            home = game.get("home_team", {})
            away = game.get("visitor_team", {})
            return (f"Today: {away.get('full_name')} @ {home.get('full_name')} -- "
                    f"{game.get('status', 'scheduled')}")

        home, away = status["home_team"], status["away_team"]
        hs, as_ = status["home_score"], status["away_score"]
        per, lead, margin = status["period_str"], status["leading"], status["margin"]

        if fields == "score":
            return f"{home} {hs} -- {away} {as_} | {per} | {status.get('time', '')}"

        score_str = f"Tied {hs}-{as_}" if lead == "Tied" else f"{home} {hs} - {away} {as_} ({lead} by {margin})"
        return f"{per}: {score_str}"

    except Exception as e:
        log.error(f"get_nba_status error: {e}", exc_info=True)
        return f"[nba] Error: {e}"


def get_nba_replay_period(game_date: str, team: str, through_period: int) -> str:
    """
    Get NBA game state through a given quarter for Replay mode.
    game_date: YYYY-MM-DD. team: team name or abbreviation.
    through_period: 1-4 (or 5+ for OT).
    """
    try:
        from integrations.nba_data import NBAClient, get_nba

        client = NBAClient()
        games = client.get_games_by_team_date(team, game_date)
        if not games:
            return f"No NBA game found for {team} on {game_date}. Try a different date or team name."

        game_id = games[0].get("id")
        nba = get_nba()
        nba.set_game(game_id)

        snap = nba.get_replay_snapshot(game_id, through_period)
        if not snap:
            return "Could not retrieve game data."

        period_str = {1: "the end of the 1st quarter", 2: "halftime", 3: "the end of the 3rd quarter",
                      4: "final"}.get(through_period, f"end of OT{through_period - 4}")

        home, away = snap["home_team"], snap["away_team"]
        hs, as_ = snap["home_score"], snap["away_score"]
        margin = abs(hs - as_)
        leader = home if hs > as_ else away if as_ > hs else None
        score_line = f"{home} {hs}, {away} {as_} -- {leader} leads by {margin}" if leader else f"Tied {hs}-{as_}"

        plays = snap.get("scoring_plays", [])
        play_summary = f" Last score: {plays[-1].get('text', '')}." if plays else ""

        return f"At {period_str}: {score_line}.{play_summary} No spoilers beyond this point."

    except Exception as e:
        log.error(f"get_nba_replay_period error: {e}", exc_info=True)
        return f"[nba_replay] Error: {e}"


def nba_game_alert() -> str:
    """On-demand check for new NBA scoring events or quarter changes --
    the 'anything happening?' equivalent of f1_race_alert()."""
    try:
        from integrations.nba_data import get_nba

        nba = get_nba()
        if not nba._game_id:
            game = nba.detect_live_game()
            if not game:
                return "No NBA game today."
            nba.set_game(game.get("id"))

        events = nba.poll()
        if not events:
            return "No new events since the last check."

        for event in events:
            msg = _format_nba_event(event)
            if msg:
                return msg
        return "No new events since the last check."

    except Exception as e:
        log.error(f"nba_game_alert error: {e}", exc_info=True)
        return f"[nba] Error: {e}"


def _format_nba_event(event: dict) -> str:
    etype = event.get("type")

    if etype == "score":
        pts = event.get("points", 2)
        team = event.get("team", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        score = f"{ht} {hs} - {at} {as_}"

        if pts == 3:
            return random.choice([f"Three! {team}. {score}.", f"{team} from downtown! {score}."])
        elif pts == 2:
            return f"{team} scores. {score}."
        else:
            return f"{team} at the line. {score}."

    elif etype == "period_start":
        period = event.get("period", 0)
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        p_str = {2: "Second quarter", 3: "Third quarter", 4: "Fourth quarter"}.get(period, f"Quarter {period}")
        return f"{p_str} underway. {ht} {hs}, {at} {as_}."

    elif etype == "final":
        winner = event.get("winner", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        return f"Final: {ht} {hs}, {at} {as_}. {winner} win."

    return ""
