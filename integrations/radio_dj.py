"""
Q2 Radio DJ Mode
================
Curates themed music sessions with narrated transitions.

IMPORTANT — what "plays via YouTube Music" actually means here:
integrations/youtube_music.py (the YouTube Data API v3 wrapper the
youtube_music tool uses) can only search for videos and build playlists
via the Google API — it has no local audio-playback capability at all,
there is no Spotify-style SDK or media player in this codebase. The real
mechanism available is the same one tools/registry.py's show_on_display
tool uses: resolve a track to a specific YouTube video via search_tracks()
and open its watch URL on the connected display with xdg-open, which
starts playing in whatever browser is running there. See _play_track()'s
docstring below for what that does and doesn't guarantee.

Because there is no feedback channel from that browser tab, this module
has no real playback-position monitoring either — commentary timing is a
duration_s estimate (see DJSession), same as the original design accepted.
"""

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Runtime session cache (generated sets, resumable state) — ephemeral,
# correctly gitignored under cache/.
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "cache" / "dj_sessions"

# Curated preset templates are content, not runtime state, so they live
# under personality/ (tracked in git) rather than cache/dj_sessions/ —
# cache/ is gitignored, so anything placed there would never actually ship.
PRESETS_DIR = Path(__file__).resolve().parent.parent / "personality" / "dj_presets"


@dataclass
class DJTrack:
    """A single track in a DJ set with its planned commentary."""

    title: str
    artist: str
    album: str = ""
    year: int = 0
    duration_s: int = 0
    youtube_query: str = ""  # search query to find on YouTube

    # Commentary
    pre_announce: str = ""         # say BEFORE the song
    back_announce: str = ""        # say AFTER the song
    mid_song_note: str = ""        # say ~20s into the song (cold open)
    connection_to_next: str = ""   # what connects this to the next

    # Facts for Q&A during playback
    facts: list = field(default_factory=list)

    # Playback state
    played: bool = False
    play_time: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "DJTrack":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class DJSession:
    """A complete DJ set with theme and tracklist."""

    theme: str
    description: str = ""
    tracks: list = field(default_factory=list)  # list[DJTrack]
    total_time: int = 0
    dj_style: str = "back_announce"  # back_announce/pre_announce/mid_song/liner_notes
    mood_arc: str = ""

    # Session state
    current_idx: int = 0
    started_at: float = 0.0
    active: bool = False

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["tracks"] = [t.to_dict() for t in self.tracks]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DJSession":
        d = dict(d)
        tracks = [DJTrack.from_dict(t) for t in d.pop("tracks", [])]
        known = {f for f in cls.__dataclass_fields__}
        sess = cls(**{k: v for k, v in d.items() if k in known and k != "tracks"})
        sess.tracks = tracks
        return sess


def _speak(text: str) -> None:
    """Same not-over-the-user safety check as main.py's
    RaceEngineerAlertThread._speak() -- but waits (rather than dropping
    the line) if Q2 is mid-turn, since DJ commentary about a specific
    track only makes sense once, unlike a race alert another lap will
    repeat if skipped."""
    if not text:
        return
    try:
        from face.server import face_state
        for _ in range(20):  # up to ~10s
            snap = face_state.snapshot()
            if not (snap["speaking"] or snap["listening"] or snap["thinking"]):
                break
            time.sleep(0.5)
    except ImportError:
        pass
    try:
        from voice.pipeline import get_tts, AudioIO
        speech = get_tts().synthesize(text)
        AudioIO().play(speech)
    except Exception as e:
        log.warning(f"[radio_dj] TTS/playback failed: {e}")


def _play_track(track: DJTrack) -> tuple:
    """
    Resolve the track to a real YouTube video (integrations.youtube_music's
    search_tracks(), the same YouTube Data API call the youtube_music tool
    uses) and open its watch URL on the connected display via xdg-open --
    the real mechanism tools/registry.py's show_on_display tool uses, not
    a fabricated always-succeeds play call. Whether audio actually starts
    depends on the browser's autoplay policy, which this process has no
    visibility into or control over.

    Returns (found: bool, resolved_title: str).
    """
    query = track.youtube_query or f"{track.artist} {track.title}"
    try:
        from integrations.youtube_music import search_tracks
        results = search_tracks(query, max_results=3)
        if not results:
            log.warning(f"[radio_dj] No YouTube match for '{query}'")
            return False, ""
        video = results[0]
        subprocess.Popen(["xdg-open", video["url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, video.get("title", track.title)
    except FileNotFoundError:
        log.warning("[radio_dj] xdg-open not found -- is a desktop environment running?")
        return False, ""
    except Exception as e:
        log.warning(f"[radio_dj] Playback resolve/open failed for '{query}': {e}")
        return False, ""


class RadioDJController:
    """
    Runs one DJ session's playback/commentary loop on a background thread.
    Talks to voice.pipeline and integrations.youtube_music directly via
    the module-level _speak()/_play_track() above rather than injected
    callables -- there is exactly one real way to do either in this
    codebase, so constructor-injected function params would be pure
    indirection with nothing else to ever swap in.
    """

    def __init__(self):
        self._session: Optional[DJSession] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._skip = False
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def start_session(self, session: DJSession):
        if self._running:
            self.stop()
        self._session = session
        session.active = True
        session.started_at = time.time()
        self._running = True
        self._skip = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="RadioDJ")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._session:
            self._session.active = False

    def skip_track(self):
        self._skip = True

    def get_status(self) -> dict:
        if not self._session:
            return {"active": False}
        sess = self._session
        idx = sess.current_idx
        track = sess.tracks[idx] if idx < len(sess.tracks) else None
        return {
            "active": sess.active,
            "theme": sess.theme,
            "current_track": track.title if track else "--",
            "current_artist": track.artist if track else "--",
            "track_number": idx + 1,
            "total_tracks": len(sess.tracks),
            "elapsed_min": round((time.time() - sess.started_at) / 60) if sess.started_at else 0,
        }

    def _run(self):
        sess = self._session
        from config.loader import config
        announce_delay = config.get("radio_dj.announce_delay_s", 1.5)
        pre_delay = config.get("radio_dj.pre_announce_delay_s", 2.0)
        mid_delay = config.get("radio_dj.mid_song_delay_s", 20)

        if sess.description:
            _speak(sess.description)
            time.sleep(pre_delay)

        for i, track in enumerate(sess.tracks):
            if not self._running:
                break
            sess.current_idx = i
            self._skip = False

            if track.pre_announce and sess.dj_style in ("pre_announce", "liner_notes"):
                _speak(track.pre_announce)
                time.sleep(pre_delay)

            found, _resolved_title = _play_track(track)
            if not found:
                _speak(f"Couldn't find {track.title} by {track.artist} on YouTube -- skipping ahead.")
                continue
            track.played = True
            track.play_time = time.time()

            if track.mid_song_note and sess.dj_style == "mid_song":
                waited = 0
                while waited < mid_delay and self._running and not self._skip:
                    time.sleep(1)
                    waited += 1
                if not self._skip and self._running:
                    _speak(track.mid_song_note)

            wait_s = track.duration_s or 210  # default 3:30 if unknown
            elapsed = 0
            while elapsed < wait_s and self._running and not self._skip:
                time.sleep(5)
                elapsed += 5

            if not self._running:
                break

            if track.back_announce and sess.dj_style in ("back_announce", "liner_notes"):
                time.sleep(announce_delay)
                _speak(track.back_announce)

            if track.connection_to_next and i < len(sess.tracks) - 1:
                time.sleep(1.0)
                _speak(track.connection_to_next)

            time.sleep(2.0)  # gap between tracks

        if self._running:
            _speak("That's the set. Hope you enjoyed the ride.")

        sess.active = False
        self._running = False


# Singleton -- matches integrations.nba_data.get_nba()/nhl_data.get_nhl()'s
# lazy-init pattern rather than the given spec's separate get/set pair.
_controller: Optional[RadioDJController] = None


def get_controller() -> RadioDJController:
    global _controller
    if _controller is None:
        _controller = RadioDJController()
    return _controller


# ---------------------------------------------------------------------------
# Session persistence (generated sets) + preset loading (curated templates)
# ---------------------------------------------------------------------------

def save_session(session: DJSession, name: str = "last_session") -> None:
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        (SESSIONS_DIR / f"{name}.json").write_text(
            json.dumps(session.to_dict(), indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"[radio_dj] Could not save session '{name}': {e}")


def list_preset_names() -> list:
    if not PRESETS_DIR.exists():
        return []
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def load_preset(name: str) -> Optional[DJSession]:
    path = PRESETS_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        return DJSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"[radio_dj] Could not load preset '{name}': {e}")
        return None
