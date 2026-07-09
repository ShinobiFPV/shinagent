"""
NFL Watchalong Integration
==========================
Data via ESPN's unofficial API. Free, no API key, no auth required.

Shared by both Watchalong Live and Watchalong Replay agent modes
(personality/profiles/watchalong_live.yaml and watchalong_replay.yaml)
whenever config.yaml's watchalong.active_sport is "nfl" -- same
two-profile, per-sport-config architecture as NBA (integrations/
nba_data.py) and NHL (integrations/nhl_data.py).

Live mode deliberately polls the /scoreboard endpoint, not /summary --
verified live against the real API: /summary?event={id} returns a full
600KB+ payload (boxscore, every player's stats, injuries, odds, news,
video links...) meant for a one-time detail view, not a 25-second poll
loop. /scoreboard is ~10x smaller and already carries score/period/clock/
situation (down, distance, yard line, possession) for every game on a
date, so live polling filters that one response to the tracked game_id
instead of fetching the heavy endpoint every tick. /summary is still
used, just only for Replay mode's scoringPlays lookup (get_replay_summary),
which is a one-shot call per user query, not a repeating poll.

Live mode: polls every 25s for score/period updates (see main.py's
RaceEngineerAlertThread._check_nfl, NFL_POLL_INTERVAL_S).
Replay mode: fetch game by date/teams for historical data -- unlike F1/UFC,
there is no persistent "active replay session" stored in config.yaml;
every get_nfl_replay_quarter() call is self-contained (game_date + teams).
"""

import time
from datetime import date
from typing import Optional
from pathlib import Path

import requests

NFL_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CACHE_DIR = Path(__file__).parent.parent / "cache" / "nfl"

NFL_TEAMS = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
    "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots",
    "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

_DOWN_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
_PERIOD_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


class NFLClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "ShinAgent/1.0", "Accept": "application/json"})
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get(self, url: str, params: dict = None, timeout: float = 6.0) -> Optional[dict]:
        try:
            r = self._session.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 429:
                time.sleep(30)
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def get_scoreboard(self, game_date: str = None) -> list:
        """Get games for today or a specific date (YYYY-MM-DD)."""
        params = {}
        if game_date:
            params["dates"] = game_date.replace("-", "")
        data = self._get(f"{NFL_BASE}/scoreboard", params)
        return data.get("events", []) if data else []

    def get_game_summary(self, game_id: str) -> Optional[dict]:
        """Full game details (boxscore/drives/scoringPlays) -- heavy, only
        used for Replay mode's one-shot per-query lookups, never polled."""
        return self._get(f"{NFL_BASE}/summary", {"event": game_id})

    def get_standings(self) -> Optional[dict]:
        return self._get(f"{NFL_BASE}/standings")

    def find_game_by_teams(self, team1: str, team2: str, game_date: str) -> Optional[dict]:
        """Find a game between two teams on a date."""
        games = self.get_scoreboard(game_date)
        t1, t2 = team1.upper(), team2.upper()
        for game in games:
            comps = game.get("competitions", [{}])[0]
            abbrs = [c.get("team", {}).get("abbreviation", "").upper() for c in comps.get("competitors", [])]
            names = [c.get("team", {}).get("displayName", "").upper() for c in comps.get("competitors", [])]
            if (t1 in abbrs or any(t1 in n for n in names)) and (t2 in abbrs or any(t2 in n for n in names)):
                return game
        return None


def _extract_situation(comp: dict) -> dict:
    situation = comp.get("situation") or {}
    return {
        "down": situation.get("down", 0),
        "distance": situation.get("distance", 0),
        "yard_line": situation.get("yardLine", 0),
        "possession_id": situation.get("possession", ""),
    }


class NFLWatchalong:
    """Manages live NFL game state, polling the lightweight scoreboard
    endpoint (see module docstring for why, verified against the real
    ESPN API)."""

    def __init__(self):
        self._client = NFLClient()
        self._game_id = None
        self._last_score = {}

    def detect_live_game(self) -> Optional[dict]:
        """Find any live NFL game right now."""
        games = self._client.get_scoreboard()
        for game in games:
            if game.get("status", {}).get("type", {}).get("state") == "in":
                return game
        return games[0] if games else None

    def set_game(self, game_id: str):
        self._game_id = game_id
        self._last_score = {}

    def _find_current_game(self) -> Optional[dict]:
        """Locate the tracked game_id within today's scoreboard."""
        for game in self._client.get_scoreboard():
            if str(game.get("id")) == str(self._game_id):
                return game
        return None

    def poll(self) -> list:
        """Poll for scoring and period updates."""
        if not self._game_id:
            return []

        game = self._find_current_game()
        if not game:
            return []

        events = []
        comp = game.get("competitions", [{}])[0]
        status = game.get("status", {})
        stype = status.get("type", {})

        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})

        home_score = int(home.get("score", 0) or 0)
        away_score = int(away.get("score", 0) or 0)
        home_team = home.get("team", {}).get("displayName", "Home")
        away_team = away.get("team", {}).get("displayName", "Away")
        home_abbr = home.get("team", {}).get("abbreviation", "HOM")
        away_abbr = away.get("team", {}).get("abbreviation", "AWY")

        period = status.get("period", 0)
        clock = status.get("displayClock", "")
        period_s = _PERIOD_ORDINALS.get(period, "OT") + " Qtr" if period <= 4 else "OT"

        prev_home = self._last_score.get("home", -1)
        prev_away = self._last_score.get("away", -1)

        if prev_home >= 0:
            if home_score > prev_home:
                pts = home_score - prev_home
                events.append({
                    "type": "score", "team": home_team, "team_abbr": home_abbr, "points": pts,
                    "score_type": _score_type(pts), "home_score": home_score, "away_score": away_score,
                    "period": period_s, "clock": clock, "home_team": home_team, "away_team": away_team,
                })
            if away_score > prev_away:
                pts = away_score - prev_away
                events.append({
                    "type": "score", "team": away_team, "team_abbr": away_abbr, "points": pts,
                    "score_type": _score_type(pts), "home_score": home_score, "away_score": away_score,
                    "period": period_s, "clock": clock, "home_team": home_team, "away_team": away_team,
                })

        prev_period = self._last_score.get("period", 0)
        if period > prev_period and prev_period > 0:
            events.append({
                "type": "period_start", "period": period, "period_str": period_s,
                "home_score": home_score, "away_score": away_score,
                "home_team": home_team, "away_team": away_team,
            })

        state = stype.get("state", "")
        if state == "post" and self._last_score.get("state") != "post":
            winner = home_team if home_score > away_score else away_team if away_score > home_score else None
            events.append({
                "type": "final", "winner": winner, "home_score": home_score, "away_score": away_score,
                "home_team": home_team, "away_team": away_team, "ot": period > 4,
            })

        self._last_score = {"home": home_score, "away": away_score, "period": period, "state": state}
        return events

    def get_status(self) -> dict:
        """Current game summary for Q2 tools -- from the lightweight
        scoreboard endpoint, not the heavy /summary one."""
        if not self._game_id:
            return {"active": False}

        game = self._find_current_game()
        if not game:
            return {"active": False}

        comp = game.get("competitions", [{}])[0]
        status = game.get("status", {})
        stype = status.get("type", {})

        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})

        home_score = int(home.get("score", 0) or 0)
        away_score = int(away.get("score", 0) or 0)
        home_team = home.get("team", {}).get("displayName", "")
        away_team = away.get("team", {}).get("displayName", "")

        period = status.get("period", 0)
        clock = status.get("displayClock", "")
        period_s = _PERIOD_ORDINALS.get(period, "OT" if period > 4 else "--")

        sit = _extract_situation(comp)
        down_s = ""
        if sit["down"] and sit["distance"]:
            down_s = f"{_DOWN_ORDINALS.get(sit['down'], str(sit['down']))} & {sit['distance']}"

        possession_team = ""
        for c in competitors:
            if c.get("team", {}).get("id") == sit["possession_id"]:
                possession_team = c.get("team", {}).get("abbreviation", "")

        return {
            "active": True,
            "game_id": self._game_id,
            "state": stype.get("state", ""),
            "period": period,
            "period_str": f"{period_s} Quarter",
            "clock": clock,
            "home_team": home_team, "home_abbr": home.get("team", {}).get("abbreviation", ""), "home_score": home_score,
            "away_team": away_team, "away_abbr": away.get("team", {}).get("abbreviation", ""), "away_score": away_score,
            "leading": home_team if home_score > away_score else away_team if away_score > home_score else "Tied",
            "margin": abs(home_score - away_score),
            "down_distance": down_s,
            "possession": possession_team,
            "yard_line": sit["yard_line"],
        }

    def get_replay_summary(self, game_id: str, through_quarter: int) -> dict:
        """Replay mode: game state through a given quarter -- uses the
        heavy /summary endpoint for its scoringPlays array, but only ever
        called once per user query, not on a poll loop."""
        data = self._client.get_game_summary(game_id)
        if not data:
            return {}

        scoring = data.get("scoringPlays", [])
        safe_scoring = [p for p in scoring if p.get("period", {}).get("number", 99) <= through_quarter]

        home_score = away_score = 0
        if safe_scoring:
            last = safe_scoring[-1]
            home_score = last.get("homeScore", 0)
            away_score = last.get("awayScore", 0)

        header = data.get("header", {})
        comp = header.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})

        quarter_s = {1: "End of 1st", 2: "Halftime", 3: "End of 3rd", 4: "Final"}.get(
            through_quarter, f"End of OT{through_quarter - 4}")

        return {
            "quarter": through_quarter,
            "quarter_str": quarter_s,
            "home_team": home.get("team", {}).get("displayName", ""),
            "away_team": away.get("team", {}).get("displayName", ""),
            "home_score": home_score,
            "away_score": away_score,
            "scoring_plays": safe_scoring[-8:],
            "total_scores": len(safe_scoring),
        }


def _score_type(points: int) -> str:
    # ESPN sometimes posts a TD (6) and its PAT (1) as two separate score
    # deltas rather than one combined 7 -- both are covered.
    return {6: "touchdown", 7: "touchdown + PAT", 8: "touchdown + 2pt", 3: "field goal",
            2: "safety", 1: "extra point"}.get(points, f"{points} points")


_nfl: Optional[NFLWatchalong] = None


def get_nfl() -> NFLWatchalong:
    global _nfl
    if _nfl is None:
        _nfl = NFLWatchalong()
    return _nfl
