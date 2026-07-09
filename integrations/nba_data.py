"""
NBA Watchalong Integration
==========================
Data via BallDontLie API (balldontlie.io). Free tier -- get a key at
balldontlie.io (no credit card) and set BALLDONTLIE_API_KEY in .env.

Shared by both Watchalong Live and Watchalong Replay agent modes
(personality/profiles/watchalong_live.yaml and watchalong_replay.yaml)
whenever config.yaml's watchalong.active_sport is "nba" -- same
two-profile, per-sport-config architecture as F1 (integrations/
f1_watchalong.py) and UFC (integrations/ufc_data.py).

Live mode: polls every 30s for score/play updates (see main.py's
RaceEngineerAlertThread._check_nba, NBA_POLL_INTERVAL_S).
Replay mode: fetch game by date/team for historical data -- unlike F1/UFC,
there is no persistent "active replay session" stored in config.yaml;
every get_nba_replay_period() call is self-contained (game_date + team),
since a specific NBA game is already fully identified by those two things.
"""

import os
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import requests

NBA_BASE = "https://api.balldontlie.io/v1"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "nba"


class NBAClient:
    def __init__(self):
        self._key = os.environ.get("BALLDONTLIE_API_KEY", "")
        self._session = requests.Session()
        if self._key:
            self._session.headers.update({"Authorization": f"Bearer {self._key}"})
        self._session.headers.update({"Content-Type": "application/json"})
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get(self, path: str, params: dict = None, timeout: float = 5.0) -> Optional[dict]:
        try:
            r = self._session.get(f"{NBA_BASE}{path}", params=params or {}, timeout=timeout)
            if r.status_code == 429:
                time.sleep(30)
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def get_todays_games(self) -> list:
        today = date.today().isoformat()
        data = self._get("/games", {"dates[]": today})
        return data.get("data", []) if data else []

    def get_game(self, game_id: int) -> Optional[dict]:
        data = self._get(f"/games/{game_id}")
        return data.get("data") if data else None

    def get_box_score(self, game_id: int) -> Optional[dict]:
        data = self._get("/box_scores", {"game_ids[]": game_id})
        if not data or not data.get("data"):
            return None
        return data["data"][0]

    def get_play_by_play(self, game_id: int, period: int = None) -> list:
        params = {"game_id": game_id}
        if period:
            params["period"] = period
        data = self._get("/play_by_play", params)
        return data.get("data", []) if data else []

    def get_standings(self, season: int = None) -> list:
        year = season or datetime.now().year
        data = self._get("/standings", {"season": year})
        return data.get("data", []) if data else []

    def search_player(self, name: str) -> list:
        data = self._get("/players", {"search": name, "per_page": 5})
        return data.get("data", []) if data else []

    def get_games_by_team_date(self, team_name: str, search_date: str) -> list:
        """Find games for a specific team on a date."""
        data = self._get("/games", {"dates[]": search_date, "per_page": 100})
        if not data:
            return []
        games = data.get("data", [])
        name_lower = team_name.lower()
        return [
            g for g in games
            if name_lower in g.get("home_team", {}).get("full_name", "").lower()
            or name_lower in g.get("visitor_team", {}).get("full_name", "").lower()
        ]

    def has_key(self) -> bool:
        return bool(self._key)


class NBAWatchalong:
    """Manages live NBA game state for watchalong mode. Polls the
    BallDontLie API every 30 seconds (see main.py's NBA_POLL_INTERVAL_S)."""

    def __init__(self):
        self._client = NBAClient()
        self._game_id = None
        self._game_info = None
        self._last_score = {}
        self._last_period = 0

    def detect_live_game(self) -> Optional[dict]:
        """Find any live NBA game today."""
        games = self._client.get_todays_games()
        for game in games:
            status = game.get("status", "")
            if any(s in status.lower() for s in ("progress", "quarter", "half", "overtime")):
                return game
        # No live game -- return the most recent game if any exist today.
        return games[-1] if games else None

    def set_game(self, game_id: int):
        self._game_id = game_id
        self._game_info = self._client.get_game(game_id)

    def poll(self) -> list:
        """Poll for updates. Returns list of new events."""
        if not self._game_id:
            return []

        events = []
        box = self._client.get_box_score(self._game_id)
        if not box:
            return []

        game = box.get("game", {})
        home = box.get("home_team", {})
        away = box.get("visitor_team", {})

        home_score = game.get("home_team_score", 0)
        away_score = game.get("visitor_team_score", 0)
        period = game.get("period", 0)
        status = game.get("status", "")

        if period > self._last_period and self._last_period > 0:
            events.append({
                "type": "period_start", "period": period,
                "home_score": home_score, "away_score": away_score,
                "home_team": home.get("full_name", "Home"), "away_team": away.get("full_name", "Away"),
            })

        prev_home = self._last_score.get("home", -1)
        prev_away = self._last_score.get("away", -1)

        if home_score != prev_home or away_score != prev_away:
            if prev_home >= 0:  # not the first poll
                if home_score > prev_home:
                    events.append({
                        "type": "score", "team": home.get("full_name", "Home"),
                        "team_abbr": home.get("abbreviation", "HOM"),
                        "points": home_score - prev_home,
                        "home_score": home_score, "away_score": away_score, "period": period,
                        "home_team": home.get("full_name", "Home"), "away_team": away.get("full_name", "Away"),
                    })
                if away_score > prev_away:
                    events.append({
                        "type": "score", "team": away.get("full_name", "Away"),
                        "team_abbr": away.get("abbreviation", "AWY"),
                        "points": away_score - prev_away,
                        "home_score": home_score, "away_score": away_score, "period": period,
                        "home_team": home.get("full_name", "Home"), "away_team": away.get("full_name", "Away"),
                    })

        if "final" in status.lower() and "final" not in self._last_score.get("status", ""):
            winner = home.get("full_name") if home_score > away_score else away.get("full_name")
            events.append({
                "type": "final", "winner": winner,
                "home_score": home_score, "away_score": away_score,
                "home_team": home.get("full_name", "Home"), "away_team": away.get("full_name", "Away"),
            })

        self._last_score = {"home": home_score, "away": away_score, "status": status}
        self._last_period = period

        return events

    def get_status(self) -> dict:
        """Current game summary for Q2 tools."""
        if not self._game_id:
            return {"active": False}

        box = self._client.get_box_score(self._game_id)
        if not box:
            return {"active": False}

        game = box.get("game", {})
        home = box.get("home_team", {})
        away = box.get("visitor_team", {})

        period = game.get("period", 0)
        period_str = {1: "1st Quarter", 2: "2nd Quarter", 3: "3rd Quarter", 4: "4th Quarter"}.get(
            period, f"OT{period - 4}" if period > 4 else "--")

        home_score = game.get("home_team_score", 0)
        away_score = game.get("visitor_team_score", 0)

        return {
            "active": True,
            "game_id": self._game_id,
            "status": game.get("status", ""),
            "period": period,
            "period_str": period_str,
            "time": game.get("time", ""),
            "home_team": home.get("full_name", ""), "home_abbr": home.get("abbreviation", ""), "home_score": home_score,
            "away_team": away.get("full_name", ""), "away_abbr": away.get("abbreviation", ""), "away_score": away_score,
            "leading": home.get("full_name") if home_score > away_score
                       else away.get("full_name") if away_score > home_score else "Tied",
            "margin": abs(home_score - away_score),
        }

    def get_replay_snapshot(self, game_id: int, through_period: int) -> dict:
        """Historical game state through a given period. Spoiler-safe --
        every sub-fetch filtered to periods <= through_period."""
        box = self._client.get_box_score(game_id)
        if not box:
            return {}

        plays = self._client.get_play_by_play(game_id)
        safe_plays = [p for p in plays if p.get("period", 99) <= through_period]

        home_score = 0
        away_score = 0
        for play in safe_plays:
            hs = play.get("home_team_score")
            as_ = play.get("visitor_team_score")
            if hs is not None:
                home_score = hs
            if as_ is not None:
                away_score = as_

        home = box.get("home_team", {})
        away = box.get("visitor_team", {})

        period_str = {1: "End of 1st", 2: "Halftime", 3: "End of 3rd", 4: "Final"}.get(
            through_period, f"End of OT{through_period - 4}")

        return {
            "period": through_period,
            "period_str": period_str,
            "home_team": home.get("full_name", ""),
            "away_team": away.get("full_name", ""),
            "home_score": home_score,
            "away_score": away_score,
            "scoring_plays": [p for p in safe_plays if p.get("score_value")][-10:],
        }


# Singleton
_nba: Optional[NBAWatchalong] = None


def get_nba() -> NBAWatchalong:
    global _nba
    if _nba is None:
        _nba = NBAWatchalong()
    return _nba
