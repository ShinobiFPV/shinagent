"""
IMQ2 Whiplash Clone Hero Integration
Polls a "now playing" text file on a background interval and detects song
changes, so Fletcher can throw out an artist-specific line the moment a
new song starts -- no audio analysis, just a text-file diff.

NOTE ON FILE FORMAT (not verified against a live Clone Hero install --
flag this if it's wrong and it can be adjusted): Clone Hero itself has no
official built-in "now playing" export. Community streaming-overlay tools
that add one (e.g. exporting for OBS) commonly write a plain text file as
two lines -- song title, then artist. This parses that convention, with a
single-line "Artist - Song" fallback for tools that write it that way
instead. Set whiplash.clone_hero.songfile_path in config.yaml to whatever
file your particular now-playing tool actually writes.
"""
import logging
import random
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# Artist name (lowercase, matched as a substring) -> Fletcher-voiced quips.
# Small, deliberately curated set of Clone-Hero-staple artists rather than
# an exhaustive list -- unknown artists fall through to _GENERIC_QUIPS.
CLONE_HERO_QUIPS = {
    "rush": [
        "Neil Peart is currently disappointed in you, and he's not even alive to see this.",
        "This is a Rush song. Rush doesn't do 'close enough.'",
    ],
    "metallica": [
        "Lars couldn't keep time on the record either, so I suppose you're in good company.",
        "Play it like you mean it, not like you're apologizing for it.",
    ],
    "dream theater": [
        "You picked a Dream Theater song. Bold choice for someone who can't hold a shuffle.",
        "Every single note in this song was rehearsed for a month. You have had thirty seconds.",
    ],
    "led zeppelin": [
        "Bonham played this with a broken foot pedal and still sounded better than that.",
        "That's a John Bonham part. Show some respect to the man's ghost.",
    ],
    "the who": [
        "Keith Moon played like the kit owed him money. You're playing like it's asleep.",
        "More. Everything about that needs to be more.",
    ],
    "guns n' roses": [
        "This is supposed to sound dangerous. That sounded like a soundcheck.",
    ],
    "toto": [
        "Porcaro is rolling in his grave, and politely, because that's what Porcaro would do.",
    ],
    "james brown": [
        "James Brown fired drummers for less than that.",
        "The one. Where was the one? The ENTIRE song is about the one.",
    ],
}

_GENERIC_QUIPS = [
    "Whoever wrote this didn't write it for you to play it like that.",
    "New song. Same standards. Don't relax.",
    "Let's see if you're actually better than the last one or if that was luck.",
    "I don't know this song. I'll know in about four bars whether you do.",
]


def get_quip(artist: str) -> str:
    key = (artist or "").strip().lower()
    for name, quips in CLONE_HERO_QUIPS.items():
        if name in key:
            return random.choice(quips)
    return random.choice(_GENERIC_QUIPS)


def _parse_songfile(path: Path) -> Optional[Tuple[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except (FileNotFoundError, OSError):
        return None
    if not text:
        return None

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None
    if len(lines) >= 2:
        return lines[1], lines[0]  # two-line convention: song title, then artist
    if " - " in lines[0]:
        artist, _, song = lines[0].partition(" - ")
        return artist.strip(), song.strip()
    return "", lines[0]


class CloneHeroWatcher:
    """Call poll() on an interval from a background thread. Returns
    (artist, song) the first time a new song is detected, else None."""

    def __init__(self, songfile_path: str = ""):
        self._path = Path(songfile_path) if songfile_path else None
        self._last_seen: Optional[Tuple[str, str]] = None

    def poll(self) -> Optional[Tuple[str, str]]:
        if not self._path:
            return None
        current = _parse_songfile(self._path)
        if current and current != self._last_seen:
            self._last_seen = current
            return current
        if current is None:
            self._last_seen = None
        return None

    @property
    def current(self) -> Optional[Tuple[str, str]]:
        return self._last_seen


_watcher: Optional[CloneHeroWatcher] = None


def get_watcher(songfile_path: str = "") -> CloneHeroWatcher:
    global _watcher
    if _watcher is None:
        _watcher = CloneHeroWatcher(songfile_path)
    return _watcher
