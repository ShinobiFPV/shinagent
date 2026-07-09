"""
NHL Watchalong Integration
==========================
Data via the NHL Web API (api-web.nhle.com) -- completely free, no API key
required, the same API the NHL's own site uses.

Shared by both Watchalong Live and Watchalong Replay agent modes
(personality/profiles/watchalong_live.yaml and watchalong_replay.yaml)
whenever config.yaml's watchalong.active_sport is "nhl" -- same
two-profile, per-sport-config architecture as F1/UFC/NBA.

Live mode: polls every 20s (hockey moves fast -- see main.py's
NHL_POLL_INTERVAL_S). Replay mode: fetch by date/teams for historical
data, same "no persistent session" approach as NBA (see integrations/
nba_data.py's module docstring).
"""

import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

NHL_BASE = "https://api-web.nhle.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "nhl"


def _team_name(team: dict) -> str:
    """homeTeam/awayTeam's display name lives under 'commonName' in every
    NHL Web API response actually verified (landing, play-by-play,
    boxscore) -- there is no 'name' key at all."""
    return (team or {}).get("commonName", {}).get("default", "")


class NHLClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "ShinAgent/1.0", "Accept": "application/json"})
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get(self, path: str, timeout: float = 5.0) -> Optional[dict]:
        try:
            r = self._session.get(f"{NHL_BASE}{path}", timeout=timeout)
            if r.status_code == 429:
                time.sleep(30)
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def get_schedule(self, game_date: str = None) -> list:
        """Games for a date (YYYY-MM-DD). Default: today."""
        d = game_date or date.today().isoformat()
        data = self._get(f"/v1/schedule/{d}")
        if not data:
            return []
        weeks = data.get("gameWeek", [])
        for week in weeks:
            if week.get("date") == d:
                return week.get("games", [])
        return weeks[0].get("games", []) if weeks else []

    def get_scores(self, game_date: str = None) -> list:
        """Scores (live or final) for a date."""
        d = game_date or date.today().isoformat()
        data = self._get(f"/v1/score/{d}")
        return data.get("games", []) if data else []

    def get_boxscore(self, game_id: int) -> Optional[dict]:
        return self._get(f"/v1/gamecenter/{game_id}/boxscore")

    def get_landing(self, game_id: int) -> Optional[dict]:
        return self._get(f"/v1/gamecenter/{game_id}/landing")

    def get_play_by_play(self, game_id: int) -> Optional[dict]:
        return self._get(f"/v1/gamecenter/{game_id}/play-by-play")

    def get_standings(self) -> Optional[dict]:
        return self._get("/v1/standings/now")

    def get_player(self, player_id: int) -> Optional[dict]:
        return self._get(f"/v1/player/{player_id}/landing")


class NHLWatchalong:
    """Manages live NHL game state for watchalong mode. Polls the NHL Web
    API every 20 seconds (see main.py's NHL_POLL_INTERVAL_S -- hockey
    moves fast, so this polls tighter than NBA's 30s)."""

    def __init__(self):
        self._client = NHLClient()
        self._game_id = None
        self._last_score = {}
        self._last_period = 0
        self._seen_goals = set()  # goal eventIds already announced

    def detect_live_game(self) -> Optional[dict]:
        """Find a live NHL game today."""
        games = self._client.get_scores()
        live = [g for g in games if g.get("gameState") in ("LIVE", "CRIT")]
        if live:
            return live[0]
        all_today = [g for g in games if g.get("gameState") != "FUT"]
        return all_today[-1] if all_today else None

    def set_game(self, game_id: int):
        self._game_id = game_id

    def poll(self) -> list:
        """Poll for game updates. Returns list of events."""
        if not self._game_id:
            return []

        events = []
        data = self._client.get_landing(self._game_id)
        if not data:
            return []

        period = data.get("periodDescriptor", {}).get("number", 0)
        p_type = data.get("periodDescriptor", {}).get("periodType", "REG")

        home = data.get("homeTeam", {})
        away = data.get("awayTeam", {})
        home_score = home.get("score", 0)
        away_score = away.get("score", 0)
        home_name = _team_name(home)
        away_name = _team_name(away)

        if period > self._last_period and self._last_period > 0:
            events.append({
                "type": "period_start", "period": period, "period_type": p_type,
                "home_score": home_score, "away_score": away_score,
                "home_team": home_name, "away_team": away_name,
            })

        pbp = self._client.get_play_by_play(self._game_id)
        if pbp:
            plays = pbp.get("plays", [])
            goals = [p for p in plays if p.get("typeDescKey") == "goal"]

            for goal in goals:
                goal_id = goal.get("eventId")
                if goal_id is None or goal_id in self._seen_goals:
                    continue
                self._seen_goals.add(goal_id)

                detail = goal.get("details", {})
                scorer = detail.get("scoringPlayerId")
                assists = [a for a in (detail.get("assist1PlayerId"), detail.get("assist2PlayerId")) if a]

                team_id = detail.get("eventOwnerTeamId")
                scoring_team = home_name if home.get("id") == team_id else away_name

                events.append({
                    "type": "goal",
                    "team": scoring_team or "Unknown",
                    "home_score": home_score, "away_score": away_score,
                    "period": goal.get("periodDescriptor", {}).get("number", period),
                    "period_type": goal.get("periodDescriptor", {}).get("periodType", p_type),
                    "scorer_id": scorer, "assist_ids": assists,
                    "home_team": home_name, "away_team": away_name,
                })

        state = data.get("gameState", "")
        if state in ("FINAL", "OFF") and self._last_score.get("state") not in ("FINAL", "OFF"):
            winner = home_name if home_score > away_score else away_name
            events.append({
                "type": "final", "winner": winner,
                "home_score": home_score, "away_score": away_score,
                "home_team": home_name, "away_team": away_name,
                "shootout": p_type == "SO", "overtime": p_type == "OT",
            })

        self._last_score = {"home": home_score, "away": away_score, "state": state}
        self._last_period = period

        return events

    def get_status(self) -> dict:
        """Current game summary for Q2 tools."""
        if not self._game_id:
            return {"active": False}

        data = self._client.get_landing(self._game_id)
        if not data:
            return {"active": False}

        home = data.get("homeTeam", {})
        away = data.get("awayTeam", {})
        period = data.get("periodDescriptor", {})
        clock = data.get("clock", {})

        home_score = home.get("score", 0)
        away_score = away.get("score", 0)
        period_num = period.get("number", 0)
        p_type = period.get("periodType", "REG")

        period_str = {1: "1st Period", 2: "2nd Period", 3: "3rd Period"}.get(
            period_num, "Overtime" if p_type == "OT" else "Shootout" if p_type == "SO" else f"Period {period_num}")

        home_name = _team_name(home)
        away_name = _team_name(away)

        return {
            "active": True,
            "game_id": self._game_id,
            "game_state": data.get("gameState", ""),
            "period": period_num,
            "period_str": period_str,
            "period_type": p_type,
            "clock": clock.get("timeRemaining", ""),
            "in_intermission": clock.get("inIntermission", False),
            "home_team": home_name, "home_abbr": home.get("abbrev", ""), "home_score": home_score,
            "away_team": away_name, "away_abbr": away.get("abbrev", ""), "away_score": away_score,
            "leading": home_name if home_score > away_score else away_name if away_score > home_score else "Tied",
            "margin": abs(home_score - away_score),
        }

    def get_replay_period(self, game_id: int, through_period: int) -> dict:
        """Historical game state through a given period. Spoiler-safe --
        only shows data up to through_period."""
        pbp = self._client.get_play_by_play(game_id)
        if not pbp:
            return {}

        plays = pbp.get("plays", [])
        safe_plays = [p for p in plays if p.get("periodDescriptor", {}).get("number", 99) <= through_period]
        goals = [p for p in safe_plays if p.get("typeDescKey") == "goal"]

        home_score = 0
        away_score = 0
        if goals:
            last_goal_detail = goals[-1].get("details", {})
            home_score = last_goal_detail.get("homeScore", 0)
            away_score = last_goal_detail.get("awayScore", 0)

        landing = self._client.get_landing(game_id)
        home = landing.get("homeTeam", {}) if landing else {}
        away = landing.get("awayTeam", {}) if landing else {}
        home_name = _team_name(home)
        away_name = _team_name(away)

        period_str = {1: "End of 1st", 2: "End of 2nd", 3: "End of 3rd"}.get(
            through_period, f"End of OT{through_period - 3}")

        return {
            "period": through_period,
            "period_str": period_str,
            "home_team": home_name,
            "away_team": away_name,
            "home_score": home_score,
            "away_score": away_score,
            "goals": [
                {"period": g.get("periodDescriptor", {}).get("number"),
                 "time": g.get("timeInPeriod", ""), "detail": g.get("details", {})}
                for g in goals
            ],
            "total_goals": len(goals),
            "shots": {
                "home": sum(1 for p in safe_plays if p.get("typeDescKey") == "shot-on-goal"
                            and p.get("details", {}).get("eventOwnerTeamId") == home.get("id")),
                "away": sum(1 for p in safe_plays if p.get("typeDescKey") == "shot-on-goal"
                            and p.get("details", {}).get("eventOwnerTeamId") == away.get("id")),
            },
        }


# Singleton
_nhl: Optional[NHLWatchalong] = None


def get_nhl() -> NHLWatchalong:
    global _nhl
    if _nhl is None:
        _nhl = NHLWatchalong()
    return _nhl
