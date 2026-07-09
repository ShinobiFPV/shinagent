"""MLB Watchalong tools for Q2."""
import random
from integrations.mlb_data import get_mlb, MLBClient


def get_mlb_status(fields: str = "summary") -> str:
    """Get live MLB game status."""
    mlb = get_mlb()
    status = mlb.get_status()

    if not status.get("active"):
        client = MLBClient()
        games = client.get_schedule()
        if not games:
            return "No MLB games today."
        game = games[0]
        home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
        away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
        state = game.get("status", {}).get("detailedState", "Scheduled")
        return f"Today: {away} @ {home} -- {state}"

    home, away = status["home_team"], status["away_team"]
    hs, as_ = status["home_score"], status["away_score"]
    inn, count, outs = status["inning_str"], status["count"], status["outs"]
    batter, lead, margin = status.get("batter", ""), status["leading"], status["margin"]

    out_s = f"{outs} out{'s' if outs != 1 else ''}"
    score_line = f"Tied {hs}-{as_}" if lead == "Tied" else f"{home} {hs} - {away} {as_} ({lead} leads by {margin})"
    batter_s = f" | {batter} at bat" if batter else ""

    return f"{inn} | {score_line} | {count} count | {out_s}{batter_s}"


def get_mlb_replay_inning(game_date: str, team: str, through_inning: int, through_half: str = "bottom") -> str:
    """Get MLB game state through a specific inning. Spoiler-protected replay mode."""
    client = MLBClient()
    game = client.find_game(team, game_date)

    if not game:
        return f"No MLB game found for {team} on {game_date}. Try full team name (e.g. 'Blue Jays') or abbreviation."

    game_pk = game.get("gamePk")
    mlb = get_mlb()
    mlb.set_game(game_pk)

    snap = mlb.get_replay_inning(game_pk, through_inning, through_half)
    if not snap:
        return "Could not retrieve game data."

    home, away = snap["home_team"], snap["away_team"]
    hs, as_ = snap["home_score"], snap["away_score"]
    p_str = snap["period_str"]

    score_line = f"{home} {hs} - {away} {as_}" if hs != as_ else f"Tied {hs}-{as_}"

    key_plays = snap.get("key_plays", [])
    highlight = ""
    if key_plays:
        last = key_plays[-1]
        highlight = f" Notable: {last.get('event', '')} -- {last.get('desc', '')[:60]}."

    return f"{p_str}: {score_line}.{highlight} No spoilers beyond this point."


def mlb_game_alert() -> str:
    """Check for MLB events. Called from alert thread."""
    mlb = get_mlb()
    events = mlb.poll()
    if not events:
        return ""
    for event in events:
        msg = _format_mlb_event(event)
        if msg:
            return msg
    return ""


def _format_mlb_event(event: dict) -> str:
    etype = event.get("type")

    if etype == "play":
        ev_type, desc = event.get("event_type", ""), event.get("description", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        score = f"{ht} {hs} - {at} {as_}"

        if ev_type == "home_run":
            return random.choice([f"HOME RUN! {score}.", f"Gone! Home run. {score}.", f"That ball is out of here. {score}."])
        elif ev_type == "triple":
            return random.choice([f"Triple! {score}.", f"Three-bagger. {score}."])
        elif ev_type == "double":
            return f"RBI double. {score}." if event.get("is_scoring") else "Double. Runners advancing."
        elif ev_type == "stolen_base_home":
            return f"Steals home! {score}."
        elif ev_type == "triple_play":
            return "TRIPLE PLAY! Three out on one play."
        elif ev_type in ("double_play", "grounded_into_double_play", "lined_into_double_play"):
            return "Double play. Two away."
        elif ev_type == "strikeout":
            if random.random() < 0.25:
                return "Strikeout."
        elif event.get("is_scoring"):
            return f"Scores. {score}."

        return ""

    elif etype == "inning_change":
        inning, half = event.get("inning", 0), event.get("half", "")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")
        ords = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th", 9: "9th"}
        inn_s = f"{'Bottom' if half.lower() == 'bottom' else 'Top'} {ords.get(inning, str(inning))}"
        return f"{inn_s}. {ht} {hs}, {at} {as_}."

    elif etype == "final":
        winner, hs, as_ = event.get("winner", ""), event.get("home_score", 0), event.get("away_score", 0)
        ht, at, innings = event.get("home_team", ""), event.get("away_team", ""), event.get("innings", 9)
        extra = f" in {innings} innings" if innings > 9 else ""
        return f"Final{extra}: {ht} {hs}, {at} {as_}. {winner} win."

    return ""
