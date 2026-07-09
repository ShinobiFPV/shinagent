"""
IMQ2 NHL Analyst Tools
Spoken-text wrappers over integrations/nhl_data.py, used by both
watchalong agent modes (personality/profiles/watchalong_live.yaml and
watchalong_replay.yaml) whenever config.yaml's watchalong.active_sport is
"nhl" -- see integrations/nba_data.py's module docstring for the
two-profile, per-sport-config architecture shared with F1/UFC/NBA.
"""
import logging
import random

log = logging.getLogger(__name__)


def get_nhl_status(fields: str = "summary") -> str:
    """Get live NHL game status. fields: 'summary' | 'score'"""
    try:
        from integrations.nhl_data import get_nhl, NHLClient

        nhl = get_nhl()
        if not nhl._game_id:
            game = nhl.detect_live_game()
            if game:
                nhl.set_game(game.get("id"))
        status = nhl.get_status()

        if not status.get("active"):
            client = NHLClient()
            games = client.get_scores()
            if not games:
                return "No NHL games today."
            game = games[0]
            home, away = game.get("homeTeam", {}), game.get("awayTeam", {})
            return f"Today: {away.get('abbrev', '')} @ {home.get('abbrev', '')} -- {game.get('gameState', 'scheduled')}"

        home, away = status["home_team"], status["away_team"]
        hs, as_ = status["home_score"], status["away_score"]
        per, clock, lead, margin = status["period_str"], status.get("clock", ""), status["leading"], status["margin"]

        if fields == "score":
            time_str = f"{per} intermission" if status.get("in_intermission") else (
                f"{per} {clock} remaining" if clock else per)
            return f"{time_str}: {home} {hs} - {away} {as_}"

        time_str = f"{per} intermission" if status.get("in_intermission") else (
            f"{per} {clock} remaining" if clock else per)
        score_line = f"Tied {hs}-{as_}" if lead == "Tied" else f"{home} {hs} - {away} {as_} ({lead} leads)"
        return f"{time_str}: {score_line}"

    except Exception as e:
        log.error(f"get_nhl_status error: {e}", exc_info=True)
        return f"[nhl] Error: {e}"


def get_nhl_replay_period(game_date: str, home_team: str, away_team: str, through_period: int) -> str:
    """Get NHL game data through a given period. Spoiler-protected --
    only shows data up to through_period."""
    try:
        from integrations.nhl_data import NHLClient, get_nhl

        client = NHLClient()
        games = client.get_schedule(game_date)

        game_id = None
        search_h, search_a = home_team.upper()[:3], away_team.upper()[:3]
        for g in games:
            ht = str(g.get("homeTeam", {}).get("abbrev", "")).upper()
            at = str(g.get("awayTeam", {}).get("abbrev", "")).upper()
            if search_h in ht or search_a in at or search_h in at or search_a in ht:
                game_id = g.get("id")
                break

        if not game_id:
            return f"No NHL game found for {away_team} @ {home_team} on {game_date}."

        nhl = get_nhl()
        nhl.set_game(game_id)
        snap = nhl.get_replay_period(game_id, through_period)
        if not snap:
            return "Could not retrieve game data."

        home, away = snap["home_team"], snap["away_team"]
        hs, as_ = snap["home_score"], snap["away_score"]
        goals, shots, period_str = snap["total_goals"], snap.get("shots", {}), snap["period_str"]

        score_line = f"{home} {hs} - {away} {as_}" if hs != as_ else f"Tied {hs}-{as_}"
        shots_str = f" Shots: {home} {shots.get('home', 0)}, {away} {shots.get('away', 0)}." if shots else ""

        return f"{period_str}: {score_line}. {goals} goals scored so far.{shots_str} No spoilers beyond this point."

    except Exception as e:
        log.error(f"get_nhl_replay_period error: {e}", exc_info=True)
        return f"[nhl_replay] Error: {e}"


def nhl_game_alert() -> str:
    """On-demand check for new NHL goals or period changes -- the
    'anything happening?' equivalent of f1_race_alert()."""
    try:
        from integrations.nhl_data import get_nhl

        nhl = get_nhl()
        if not nhl._game_id:
            game = nhl.detect_live_game()
            if not game:
                return "No NHL game today."
            nhl.set_game(game.get("id"))

        events = nhl.poll()
        if not events:
            return "No new events since the last check."

        for event in events:
            msg = _format_nhl_event(event)
            if msg:
                return msg
        return "No new events since the last check."

    except Exception as e:
        log.error(f"nhl_game_alert error: {e}", exc_info=True)
        return f"[nhl] Error: {e}"


def _format_nhl_event(event: dict) -> str:
    etype = event.get("type")

    if etype == "goal":
        team = event.get("team", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        ptype = event.get("period_type", "REG")
        score = f"{ht} {hs} - {at} {as_}"
        ot_str = " OT" if ptype == "OT" else ""
        return random.choice([f"GOAL! {team}. {score}.", f"{team} scores! {score}."]) + ot_str

    elif etype == "period_start":
        period = event.get("period", 0)
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        ptype = event.get("period_type", "REG")
        p_str = {1: "First period", 2: "Second period", 3: "Third period"}.get(
            period, "Overtime" if ptype == "OT" else "Shootout")
        return f"{p_str} underway. {ht} {hs}, {at} {as_}."

    elif etype == "final":
        winner = event.get("winner", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        shootout, overtime = event.get("shootout", False), event.get("overtime", False)
        suffix = " in the shootout." if shootout else " in overtime." if overtime else "."
        return f"Final: {ht} {hs}, {at} {as_}. {winner} win{suffix}"

    return ""
