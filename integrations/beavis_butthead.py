"""
IMQ2 Beavis and Butthead Mode
=============================
Manages video sessions, candidate generation, and cross-session history.
Commentary generation lives in tools/beavis_butthead.py -- this module is
pure state (session/history), matching the integrations/ vs tools/ split
used by every other mode (masterchef, radio_dj, whiplash).
"""

import json
import time
import random
from pathlib import Path
from typing import Optional

SESSION_DIR = Path(__file__).parent.parent / "cache" / "bb_mode"
HISTORY_FILE = SESSION_DIR / "video_history.json"

# ── Video candidate pool ──────────────────────────────────────────────
# The kinds of videos Beavis and Butthead would actually watch. Mix of
# things they'd hate, things they'd love, and things they'd misunderstand
# completely -- that mix is the actual feature, not an LLM-generated list.

VIDEO_CATEGORIES = {
    "metal_they_love": [
        {"title": "Enter Sandman", "artist": "Metallica",
         "query": "Metallica Enter Sandman official music video",
         "bb_notes": "This rocks. Fire. Sandman."},
        {"title": "Walk", "artist": "Pantera",
         "query": "Pantera Walk official music video",
         "bb_notes": "HEAVY. Butthead approves."},
        {"title": "Smells Like Teen Spirit", "artist": "Nirvana",
         "query": "Nirvana Smells Like Teen Spirit official video",
         "bb_notes": "This is cool. Cheerleaders."},
        {"title": "Jeremy", "artist": "Pearl Jam",
         "query": "Pearl Jam Jeremy official music video",
         "bb_notes": "Uh... this guy is weird."},
        {"title": "Bulls on Parade", "artist": "Rage Against the Machine",
         "query": "Rage Against the Machine Bulls on Parade",
         "bb_notes": "YEAH. This is LOUD."},
        {"title": "Down with the Sickness", "artist": "Disturbed",
         "query": "Disturbed Down with the Sickness music video",
         "bb_notes": "He goes OOH WAH AH AH AH."},
        {"title": "Bodies", "artist": "Drowning Pool",
         "query": "Drowning Pool Bodies official video",
         "bb_notes": "LET THE BODIES HIT THE FLOOR."},
        {"title": "Chop Suey", "artist": "System of a Down",
         "query": "System of a Down Chop Suey official video",
         "bb_notes": "These guys have weird hair."},
    ],
    "pop_they_hate": [
        {"title": "MMMBop", "artist": "Hanson",
         "query": "Hanson MMMBop official music video",
         "bb_notes": "This sucks. Change it. These are boys."},
        {"title": "Barbie Girl", "artist": "Aqua",
         "query": "Aqua Barbie Girl official music video",
         "bb_notes": "...this is stupid. Heh heh. Barbie."},
        {"title": "Wannabe", "artist": "Spice Girls",
         "query": "Spice Girls Wannabe official music video",
         "bb_notes": "CHICKS. Wait this sucks. But. Chicks."},
        {"title": "Baby One More Time", "artist": "Britney Spears",
         "query": "Britney Spears Baby One More Time official video",
         "bb_notes": "She's in like a school. Heh heh."},
        {"title": "Macarena", "artist": "Los Del Rio",
         "query": "Los Del Rio Macarena official music video",
         "bb_notes": "Uh... they're doing like... dancing. This sucks."},
    ],
    "confused_by": [
        {"title": "Sabotage", "artist": "Beastie Boys",
         "query": "Beastie Boys Sabotage official music video",
         "bb_notes": "These are like cops. This is cool actually."},
        {"title": "Virtual Insanity", "artist": "Jamiroquai",
         "query": "Jamiroquai Virtual Insanity official video",
         "bb_notes": "Why is the floor moving. This is weird."},
        {"title": "Buddy Holly", "artist": "Weezer",
         "query": "Weezer Buddy Holly music video",
         "bb_notes": "They're like in Happy Days. Heh heh. Richie."},
        {"title": "Weapon of Choice", "artist": "Fatboy Slim",
         "query": "Fatboy Slim Weapon of Choice Christopher Walken",
         "bb_notes": "That bald guy is like... flying? This is weird."},
        {"title": "Take On Me", "artist": "a-ha",
         "query": "a-ha Take On Me official music video",
         "bb_notes": "It's like a cartoon. But not a cartoon."},
        {"title": "Thriller", "artist": "Michael Jackson",
         "query": "Michael Jackson Thriller official short film",
         "bb_notes": "This is like... zombies. Cool."},
    ],
    "country_they_despise": [
        {"title": "Friends in Low Places", "artist": "Garth Brooks",
         "query": "Garth Brooks Friends in Low Places music video",
         "bb_notes": "This sucks. This really sucks."},
        {"title": "Achy Breaky Heart", "artist": "Billy Ray Cyrus",
         "query": "Billy Ray Cyrus Achy Breaky Heart music video",
         "bb_notes": "His hair is like... why is his hair like that."},
    ],
    "classic_rock_moments": [
        {"title": "November Rain", "artist": "Guns N Roses",
         "query": "Guns N Roses November Rain official music video",
         "bb_notes": "This is like LONG. But there are explosions."},
        {"title": "Panama", "artist": "Van Halen",
         "query": "Van Halen Panama official music video",
         "bb_notes": "This rocks. PANAMA."},
        {"title": "Detroit Rock City", "artist": "KISS",
         "query": "KISS Detroit Rock City music video",
         "bb_notes": "These guys have like... face paint."},
        {"title": "Paradise City", "artist": "Guns N Roses",
         "query": "Guns N Roses Paradise City official video",
         "bb_notes": "Yeah. YEAH. TAKE ME DOWN TO PARADISE CITY."},
    ],
    "rap_mixed_reaction": [
        {"title": "Fight the Power", "artist": "Public Enemy",
         "query": "Public Enemy Fight the Power music video",
         "bb_notes": "This is... LOUD. I think they're like... mad."},
        {"title": "Jump", "artist": "Kris Kross",
         "query": "Kris Kross Jump music video",
         "bb_notes": "Their pants are on backwards. Heh heh."},
        {"title": "Gangsta's Paradise", "artist": "Coolio",
         "query": "Coolio Gangsta's Paradise music video",
         "bb_notes": "This is from that movie with Michelle Pfeiffer."},
        {"title": "Ice Ice Baby", "artist": "Vanilla Ice",
         "query": "Vanilla Ice Ice Ice Baby music video",
         "bb_notes": "STOP. COLLABORATE. AND LISTEN. Heh heh."},
    ],
}


class BBSession:
    """Active Beavis and Butthead viewing session."""

    def __init__(self):
        self.session_id = str(int(time.time()))
        self.candidates: list = []
        self.selected: list = []
        self.current_idx: int = 0
        self.nice_guy: bool = False
        self.q2_is: str = "butthead"  # or "beavis"
        self.active: bool = False
        self.commentary_history: list = []

        SESSION_DIR.mkdir(parents=True, exist_ok=True)

    def generate_candidates(self, count: int = 20) -> list:
        """Shuffle the curated pool and pick N -- mixing categories is
        the point (things they'd love, hate, and be confused by)."""
        all_videos = []
        for category, videos in VIDEO_CATEGORIES.items():
            for v in videos:
                all_videos.append({**v, "category": category})

        random.shuffle(all_videos)
        self.candidates = all_videos[:count]
        return self.candidates

    def select_videos(self, indices: list) -> list:
        """User selects videos by index from candidates."""
        self.selected = [
            self.candidates[i] for i in indices
            if 0 <= i < len(self.candidates)
        ]
        self.current_idx = 0
        self.active = True
        return self.selected

    @property
    def current_video(self) -> Optional[dict]:
        if self.selected and self.current_idx < len(self.selected):
            return self.selected[self.current_idx]
        return None

    def next_video(self) -> Optional[dict]:
        self.current_idx += 1
        return self.current_video

    def add_commentary(self, speaker: str, text: str):
        self.commentary_history.append({
            "speaker": speaker,
            "text": text,
            "time": time.time(),
            "video": self.current_video.get("title", "") if self.current_video else "",
        })

    def mark_replay(self, replay: bool = True):
        """Mark current video as replay-OK or not."""
        if self.current_video:
            self.current_video["replay_ok"] = replay

    def get_status(self) -> dict:
        return {
            "active": self.active,
            "session_id": self.session_id,
            "nice_guy": self.nice_guy,
            "q2_is": self.q2_is,
            "current_video": self.current_video,
            "current_idx": self.current_idx,
            "total": len(self.selected),
            "selected": self.selected,
        }


class BBHistory:
    """Persistent history of videos played across sessions -- play
    counts and replay-list membership, stored as JSON in the gitignored
    cache/ directory (runtime-generated state, not a shipped asset --
    same convention as e.g. purchasing/db/ledger.db)."""

    def __init__(self):
        self._history = self._load()

    def _load(self) -> dict:
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(self._history, indent=2), encoding="utf-8")

    def record_play(self, video: dict, rating: str = ""):
        key = f"{video['artist']}|{video['title']}"
        entry = self._history.get(key, {
            "title": video["title"],
            "artist": video["artist"],
            "query": video["query"],
            "play_count": 0,
            "replay_ok": False,
            "ratings": [],
            "last_played": 0,
        })
        entry["play_count"] += 1
        entry["last_played"] = time.time()
        if rating:
            entry["ratings"].append(rating)

        self._history[key] = entry
        self._save()

    def set_replay(self, video: dict, ok: bool):
        # Create the history entry if this is called before record_play()
        # -- e.g. the user says "add this to replay" mid-video, before it
        # finishes and gets recorded -- rather than silently no-opping.
        key = f"{video['artist']}|{video['title']}"
        entry = self._history.get(key, {
            "title": video["title"],
            "artist": video["artist"],
            "query": video["query"],
            "play_count": 0,
            "replay_ok": False,
            "ratings": [],
            "last_played": 0,
        })
        entry["replay_ok"] = ok
        self._history[key] = entry
        self._save()

    def get_replay_allowed(self) -> list:
        return [v for v in self._history.values() if v.get("replay_ok")]

    def get_history(self, limit: int = 50) -> list:
        items = sorted(self._history.values(), key=lambda x: x.get("last_played", 0), reverse=True)
        return items[:limit]

    def has_been_played(self, video: dict) -> bool:
        key = f"{video['artist']}|{video['title']}"
        return key in self._history


def resolve_video_id(query: str) -> Optional[str]:
    """Look up a real YouTube video ID for a candidate's search query via
    the YouTube Data API (integrations/youtube_music.py's search_tracks(),
    already filtered to the Music category -- a good fit for music
    videos). The originally proposed approach embedded a raw
    "?listType=search&list=..." search-results URL directly in an
    iframe; YouTube deprecated public search-result embeds years ago, so
    that URL no longer reliably returns results. A real video ID embeds
    via the standard, fully-supported /embed/{id} URL instead. Returns
    None (not an exception) if no OAuth token is set up or the lookup
    otherwise fails -- callers should fall back to a plain search-results
    link the user opens themselves, same as MasterChef's
    get_technique_video()/Radio DJ's playback mechanism."""
    try:
        from integrations.youtube_music import search_tracks
        results = search_tracks(query, max_results=1)
        return results[0]["id"] if results else None
    except Exception:
        return None


_session: Optional[BBSession] = None
_history: Optional[BBHistory] = None


def get_session() -> BBSession:
    global _session
    if _session is None:
        _session = BBSession()
    return _session


def new_session() -> BBSession:
    global _session
    _session = BBSession()
    return _session


def get_history() -> BBHistory:
    global _history
    if _history is None:
        _history = BBHistory()
    return _history
