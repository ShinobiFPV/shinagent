"""NFL Watchalong tools for Q2."""
import random
from integrations.nfl_data import get_nfl, NFLClient


def get_nfl_status(fields: str = "summary") -> str:
    """Get live NFL game status."""
    nfl = get_nfl()
    status = nfl.get_status()

    if not status.get("active"):
        client = NFLClient()
        games = client.get_scoreboard()
        if not games:
            return "No NFL games today."
        game = games[0]
        comp = game.get("competitions", [{}])[0]
        names = [t.get("team", {}).get("displayName", "?") for t in comp.get("competitors", [])]
        return f"Today: {' vs '.join(names)} -- {game.get('status', {}).get('type', {}).get('detail', 'scheduled')}"

    home, away = status["home_team"], status["away_team"]
    hs, as_ = status["home_score"], status["away_score"]
    period = status["period_str"]
    clock = status.get("clock", "")
    down_dist = status.get("down_distance", "")
    possess = status.get("possession", "")

    clock_str = f" {clock}" if clock else ""
    down_str = f" | {down_dist}" if down_dist else ""
    poss_str = f" | {possess} ball" if possess else ""

    lead, margin = status["leading"], status["margin"]
    score_line = f"Tied {hs}-{as_}" if lead == "Tied" else f"{home} {hs} - {away} {as_} ({lead} leads by {margin})"

    return f"{period}{clock_str}: {score_line}{down_str}{poss_str}"


def get_nfl_replay_quarter(game_date: str, home_team: str, away_team: str, through_quarter: int) -> str:
    """Get NFL game state through a given quarter. Spoiler-protected replay mode."""
    client = NFLClient()
    game = client.find_game_by_teams(home_team, away_team, game_date)
    if not game:
        return f"No NFL game found for {away_team} @ {home_team} on {game_date}."

    game_id = game.get("id")
    nfl = get_nfl()
    nfl.set_game(game_id)

    snap = nfl.get_replay_summary(game_id, through_quarter)
    if not snap:
        return "Could not retrieve game data."

    home, away = snap["home_team"], snap["away_team"]
    hs, as_ = snap["home_score"], snap["away_score"]
    qstr = snap["quarter_str"]

    score_line = f"{home} {hs} - {away} {as_}" if hs != as_ else f"Tied {hs}-{as_}"

    plays = snap.get("scoring_plays", [])
    play_text = ""
    if plays:
        last = plays[-1]
        play_text = f" Last score: {last.get('type', {}).get('text', '')}."

    return f"{qstr}: {score_line}.{play_text} No spoilers beyond this point."


def nfl_game_alert() -> str:
    """Check for NFL scoring events. Call from alert thread."""
    nfl = get_nfl()
    events = nfl.poll()
    if not events:
        return ""
    for event in events:
        msg = _format_nfl_event(event)
        if msg:
            return msg
    return ""


def _format_nfl_event(event: dict) -> str:
    etype = event.get("type")

    if etype == "score":
        team, pts, stype = event.get("team", ""), event.get("points", 0), event.get("score_type", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        score = f"{ht} {hs} - {at} {as_}"

        if "touchdown" in stype:
            return random.choice([f"TOUCHDOWN! {team}! {score}.", f"{team} scores! Touchdown. {score}."])
        elif "field goal" in stype:
            return random.choice([f"Field goal -- {team}. {score}.", f"{team} kicks a field goal. {score}."])
        elif "safety" in stype:
            return f"Safety! {team} scores 2. {score}."
        return f"{team} scores. {score}."

    elif etype == "period_start":
        period, hs, as_ = event.get("period_str", ""), event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        return f"{period} underway. {ht} {hs}, {at} {as_}."

    elif etype == "final":
        winner, hs, as_ = event.get("winner", ""), event.get("home_score", 0), event.get("away_score", 0)
        ht, at, ot = event.get("home_team", ""), event.get("away_team", ""), event.get("ot", False)
        suffix = " in overtime." if ot else "."
        return f"Final: {ht} {hs}, {at} {as_}. {winner} win{suffix}"

    return ""
