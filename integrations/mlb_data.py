"""
MLB Watchalong Integration
==========================
Data via the official MLB Stats API (statsapi.mlb.com). Completely free,
no API key, no auth required -- the same API that powers MLB.com's own
live game pages.

Shared by both Watchalong Live and Watchalong Replay agent modes
(personality/profiles/watchalong_live.yaml and watchalong_replay.yaml)
whenever config.yaml's watchalong.active_sport is "mlb" -- same
architecture as NBA/NHL/NFL.

Live mode: polls every 20s for play/inning updates (see main.py's
RaceEngineerAlertThread._check_mlb, MLB_POLL_INTERVAL_S).
Replay mode: fetch game by date/team for historical data.
"""

import time
from datetime import datetime, date
from typing import Optional
from pathlib import Path

import requests

MLB_BASE = "https://statsapi.mlb.com/api/v1"
MLB_LIVE_BASE = "https://statsapi.mlb.com/api/v1.1"  # live feed is versioned separately
CACHE_DIR = Path(__file__).parent.parent / "cache" / "mlb"

# eventType values verified against a real live game's play-by-play feed.
# "double_play" is also a real value for some double-play variants (e.g.
# a line-drive double play) distinct from "grounded_into_double_play",
# which is the one actually observed live -- both are included.
_HOME_RUN = "home_run"
_TRIPLE_PLAY = "triple_play"
_DOUBLE_PLAY_TYPES = {"double_play", "grounded_into_double_play", "lined_into_double_play"}
_STRIKEOUT = "strikeout"


class MLBClient:
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

    def get_schedule(self, game_date: str = None) -> list:
        """Get games for a date (YYYY-MM-DD). Default today."""
        d = game_date or date.today().isoformat()
        params = {"sportId": 1, "date": d, "hydrate": "team,linescore"}
        data = self._get(f"{MLB_BASE}/schedule", params)
        if not data:
            return []
        dates = data.get("dates", [])
        return dates[0].get("games", []) if dates else []

    def get_live_feed(self, game_pk: int) -> Optional[dict]:
        """Get the complete live game feed."""
        return self._get(f"{MLB_LIVE_BASE}/game/{game_pk}/feed/live")

    def get_standings(self, season: int = None) -> Optional[dict]:
        year = season or datetime.now().year
        params = {"leagueId": "103,104", "season": year, "standingsType": "regularSeason"}
        return self._get(f"{MLB_BASE}/standings", params)

    def find_game(self, team_name: str, game_date: str) -> Optional[dict]:
        """Find a game involving a team on a date."""
        games = self.get_schedule(game_date)
        name_lower = team_name.lower()
        for game in games:
            teams = game.get("teams", {})
            home = teams.get("home", {}).get("team", {})
            away = teams.get("away", {}).get("team", {})
            if (name_lower in home.get("name", "").lower() or name_lower in away.get("name", "").lower()
                    or name_lower in home.get("abbreviation", "").lower() or name_lower in away.get("abbreviation", "").lower()):
                return game
        return None


def _bases_occupied(matchup: dict) -> list:
    """Which bases currently have a runner -- [1B, 2B, 3B]. Verified
    against a live game: the natural-looking `currentPlay.runners[].
    movement.end` array only records runner MOVEMENT during the current
    play (it's empty between pitches even with runners actually on
    base). The correct, always-current signal is postOnFirst/Second/
    Third's presence on matchup -- confirmed via a real live game with
    Ortiz on first and Pratt on second, `runners` was `[]` at the same
    moment postOnFirst/postOnSecond were both populated."""
    return [
        bool(matchup.get("postOnFirst")),
        bool(matchup.get("postOnSecond")),
        bool(matchup.get("postOnThird")),
    ]


class MLBWatchalong:
    """Manages live MLB game state."""

    def __init__(self):
        self._client = MLBClient()
        self._game_pk = None
        self._last_state = {}
        self._seen_plays = set()

    def detect_live_game(self) -> Optional[dict]:
        """Find a live MLB game today."""
        games = self._client.get_schedule()
        live = [g for g in games if g.get("status", {}).get("abstractGameState") == "Live"]
        if live:
            return live[0]
        final = [g for g in games if g.get("status", {}).get("abstractGameState") == "Final"]
        if final:
            return final[-1]
        return games[0] if games else None

    def set_game(self, game_pk: int):
        self._game_pk = game_pk
        self._last_state = {}
        self._seen_plays = set()

    def poll(self) -> list:
        """Poll for live game updates."""
        if not self._game_pk:
            return []

        data = self._client.get_live_feed(self._game_pk)
        if not data:
            return []

        events = []
        live = data.get("liveData", {})
        linescore = live.get("linescore", {})
        plays = live.get("plays", {})
        game_data = data.get("gameData", {})
        status = game_data.get("status", {})

        teams = game_data.get("teams", {})
        home_team = teams.get("home", {}).get("name", "Home")
        away_team = teams.get("away", {}).get("name", "Away")

        home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0)
        away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0)
        inning = linescore.get("currentInning", 0)
        inning_half = linescore.get("inningHalf", "")

        # Score deltas are tracked incrementally per-play within this
        # batch, not just against the last poll's cached total -- if
        # several plays land in one 20s window, each one's "did this
        # specific play score" check must compare against the play
        # immediately before it, not the pre-batch baseline, or an
        # unrelated later play in the same batch would get mislabeled
        # as scoring too.
        # Default to 0 (game start), not the current total -- on the very
        # first poll after set_game(), every historical play is "new," and
        # anchoring to the current end-of-game score would misclassify
        # early plays (whose own recorded score is genuinely lower) as
        # scoring plays relative to a baseline they were never near.
        running_home = self._last_state.get("home", 0)
        running_away = self._last_state.get("away", 0)

        all_plays = plays.get("allPlays", [])
        for play in all_plays:
            play_id = play.get("atBatIndex", -1)
            if play_id in self._seen_plays:
                continue
            result = play.get("result", {})
            rtype = result.get("eventType", "")
            rdesc = result.get("description", "")
            hs = result.get("homeScore", running_home)
            as_ = result.get("awayScore", running_away)
            is_scoring = hs != running_home or as_ != running_away
            running_home, running_away = hs, as_

            notable = rtype == _HOME_RUN or rtype == _TRIPLE_PLAY or rtype in _DOUBLE_PLAY_TYPES \
                or rtype == _STRIKEOUT or is_scoring
            if not notable:
                self._seen_plays.add(play_id)
                continue

            self._seen_plays.add(play_id)
            about = play.get("about", {})
            events.append({
                "type": "play",
                "event_type": rtype,
                "description": rdesc,
                "inning": about.get("inning", 0),
                "half": about.get("halfInning", ""),
                "home_score": hs,
                "away_score": as_,
                "home_team": home_team,
                "away_team": away_team,
                "is_scoring": is_scoring,
            })

        prev_inning = self._last_state.get("inning", 0)
        prev_half = self._last_state.get("half", "")
        if (inning != prev_inning or inning_half != prev_half) and prev_inning > 0:
            events.append({
                "type": "inning_change", "inning": inning, "half": inning_half,
                "home_score": home_score, "away_score": away_score,
                "home_team": home_team, "away_team": away_team,
                "outs": linescore.get("outs", 0),
            })

        abs_state = status.get("abstractGameState", "")
        if abs_state == "Final" and self._last_state.get("state") != "Final":
            winner = home_team if home_score > away_score else away_team
            events.append({
                "type": "final", "winner": winner, "home_score": home_score, "away_score": away_score,
                "home_team": home_team, "away_team": away_team, "innings": inning,
            })

        self._last_state = {"home": home_score, "away": away_score, "inning": inning, "half": inning_half, "state": abs_state}
        return events

    def get_status(self) -> dict:
        """Current game summary for Q2 tools."""
        if not self._game_pk:
            return {"active": False}

        data = self._client.get_live_feed(self._game_pk)
        if not data:
            return {"active": False}

        live = data.get("liveData", {})
        linescore = live.get("linescore", {})
        game_data = data.get("gameData", {})
        teams = game_data.get("teams", {})
        status = game_data.get("status", {})

        home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0)
        away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0)
        home_hits = linescore.get("teams", {}).get("home", {}).get("hits", 0)
        away_hits = linescore.get("teams", {}).get("away", {}).get("hits", 0)
        inning = linescore.get("currentInning", 0)
        inning_half = linescore.get("inningHalf", "")
        balls = linescore.get("balls", 0)
        strikes = linescore.get("strikes", 0)
        outs = linescore.get("outs", 0)

        plays = live.get("plays", {})
        current = plays.get("currentPlay", {})
        matchup = current.get("matchup", {})
        batter = matchup.get("batter", {}).get("fullName", "")
        pitcher = matchup.get("pitcher", {}).get("fullName", "")
        bases = _bases_occupied(matchup)

        inn_str = f"{'Top' if inning_half == 'Top' or inning_half == 'top' else 'Bottom'} {inning}"

        return {
            "active": True,
            "game_pk": self._game_pk,
            "state": status.get("abstractGameState", ""),
            "detailed": status.get("detailedState", ""),
            "inning": inning,
            "inning_half": inning_half,
            "inning_str": inn_str,
            "balls": balls, "strikes": strikes, "outs": outs,
            "count": f"{balls}-{strikes}",
            "home_team": teams.get("home", {}).get("name", ""), "home_abbr": teams.get("home", {}).get("abbreviation", ""),
            "home_score": home_score, "home_hits": home_hits,
            "away_team": teams.get("away", {}).get("name", ""), "away_abbr": teams.get("away", {}).get("abbreviation", ""),
            "away_score": away_score, "away_hits": away_hits,
            "leading": teams.get("home", {}).get("name") if home_score > away_score
                       else teams.get("away", {}).get("name") if away_score > home_score else "Tied",
            "margin": abs(home_score - away_score),
            "batter": batter, "pitcher": pitcher,
            "bases": bases,
        }

    def get_replay_inning(self, game_pk: int, through_inning: int, through_half: str = "bottom") -> dict:
        """Replay mode: game state through a given inning.
        through_half: 'top' or 'bottom'. Spoiler-protected."""
        data = self._client.get_live_feed(game_pk)
        if not data:
            return {}

        live = data.get("liveData", {})
        all_plays = live.get("plays", {}).get("allPlays", [])
        game_data = data.get("gameData", {})
        teams = game_data.get("teams", {})

        half_order = {"top": 0, "bottom": 1}
        target_order = half_order.get(through_half, 1)

        safe_plays = [
            p for p in all_plays
            if (p.get("about", {}).get("inning", 99) < through_inning)
            or (p.get("about", {}).get("inning", 99) == through_inning
                and half_order.get(p.get("about", {}).get("halfInning", ""), 0) <= target_order)
        ]

        home_score = away_score = 0
        if safe_plays:
            res = safe_plays[-1].get("result", {})
            home_score = res.get("homeScore", 0)
            away_score = res.get("awayScore", 0)

        key_plays = [
            p for p in safe_plays
            if p.get("result", {}).get("eventType") in (_HOME_RUN, "triple") or p.get("result", {}).get("eventType") == _TRIPLE_PLAY
        ]

        half_str = "after" if through_half == "bottom" else "after top of"
        inning_ord = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th", 9: "9th"}.get(
            through_inning, f"{through_inning}th")

        return {
            "through_inning": through_inning,
            "through_half": through_half,
            "period_str": f"{half_str} {inning_ord}",
            "home_team": teams.get("home", {}).get("name", ""),
            "away_team": teams.get("away", {}).get("name", ""),
            "home_score": home_score,
            "away_score": away_score,
            "key_plays": [
                {
                    "event": p.get("result", {}).get("event", ""),
                    "desc": p.get("result", {}).get("description", ""),
                    "inning": p.get("about", {}).get("inning", 0),
                    "half": p.get("about", {}).get("halfInning", ""),
                }
                for p in key_plays[-5:]
            ],
        }


_mlb: Optional[MLBWatchalong] = None


def get_mlb() -> MLBWatchalong:
    global _mlb
    if _mlb is None:
        _mlb = MLBWatchalong()
    return _mlb
