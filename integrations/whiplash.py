"""
IMQ2 Whiplash Integration
Tap-to-sync metronome, funk groove practice library, and MIDI-hit timing
scoring for Whiplash Mode (a Fletcher-voiced drum practice companion --
see personality/profiles/whiplash.yaml).

Tap-to-sync design (see architecture note): the user hits SYNC at the
instant they hear beat 1 of whatever they're playing along to. From that
perf_counter() timestamp forward, every beat time is pure arithmetic
(synced_at + n * beat_interval) -- no audio analysis needed. Every kick/
snare MIDI hit is then scored against how far it lands from the nearest
grid beat.

The funk groove patterns below are stylistically representative, hand-
authored approximations of these iconic breaks (not bar-exact
transcriptions verified against the original recordings -- I have no way
to check MIDI-note-accurate timing against the actual audio from here).
Treat the "pattern" field as good enough for a practice metronome grid
and the "structure" field as the actual teaching description; flag it if
a bar sounds wrong and it can be corrected against a real transcription.
"""
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

from integrations.whiplash_midi import SCORED_PIECES


class Metronome:
    """Tap-to-sync metronome grid. Not a dataclass -- it has behavior
    (sync/start/stop, grid math), not just state."""

    def __init__(self):
        self.bpm: float = 100.0
        self.running: bool = False
        self.beats_per_bar: int = 4
        self.synced_at: Optional[float] = None  # perf_counter() timestamp of beat 1

    def is_synced(self) -> bool:
        return self.synced_at is not None

    def sync(self):
        """Tap-to-sync: this instant becomes beat 1 of the grid."""
        self.synced_at = time.perf_counter()

    def start(self, bpm: Optional[float] = None):
        if bpm:
            self.bpm = bpm
        if self.synced_at is None:
            self.sync()
        self.running = True

    def stop(self):
        self.running = False

    def set_bpm(self, bpm: float):
        self.bpm = bpm

    def beat_interval(self) -> float:
        return 60.0 / self.bpm

    def nearest_beat_time(self, ts: float) -> float:
        """Nearest grid beat to a given perf_counter() timestamp."""
        if self.synced_at is None:
            return ts
        interval = self.beat_interval()
        n = round((ts - self.synced_at) / interval)
        return self.synced_at + n * interval

    def deviation_ms(self, ts: float) -> float:
        """Signed ms deviation from the nearest beat. Negative = early
        (rushing), positive = late (dragging)."""
        return (ts - self.nearest_beat_time(ts)) * 1000.0


_metronome = Metronome()


def get_metronome() -> Metronome:
    return _metronome


def _make_click(freq: float, accent: bool, duration_s: float = 0.03, samplerate: int = 44100):
    """Short decaying sine burst -- sounds like a click, not a tone."""
    import numpy as np
    t = np.linspace(0, duration_s, int(samplerate * duration_s), endpoint=False)
    envelope = np.exp(-t * 60)
    wave = np.sin(2 * math.pi * freq * t) * envelope
    amplitude = 0.5 if accent else 0.3
    return (wave * amplitude).astype(np.float32)


class MetronomeClickThread(threading.Thread):
    """Fires an audible click at each beat of the metronome's synced grid.
    Sleeps until ~2ms before the next beat, then busy-waits the remainder
    -- combined with perf_counter()'s sub-millisecond resolution, this
    keeps click timing well under the ~10ms threshold drummers can
    perceive (see architecture note "Timing precision"). Runs continuously
    once started; checks metronome.running every loop rather than being
    started/stopped per session, matching main.py's other background
    listener threads."""

    def __init__(self, metronome: Metronome):
        super().__init__(daemon=True, name="WhiplashMetronomeClick")
        self._metronome = metronome
        self._running = True
        try:
            self._click_accent = _make_click(1600, accent=True)
            self._click_normal = _make_click(1000, accent=False)
        except ImportError:
            # numpy missing in this environment -- timing sync/scoring has
            # no dependency on it at all, only the audible click does, so
            # degrade to a silent metronome rather than failing to start.
            log.warning("numpy not available -- metronome will run silently (timing scoring still works)")
            self._click_accent = None
            self._click_normal = None

    def run(self):
        try:
            import sounddevice as sd
        except ImportError:
            log.warning("sounddevice not available -- metronome will run silently (timing scoring still works)")
            sd = None

        beat_n = 0
        while self._running:
            m = self._metronome
            if not m.running or m.synced_at is None:
                time.sleep(0.05)
                continue

            interval = m.beat_interval()
            elapsed = time.perf_counter() - m.synced_at
            beat_n = math.floor(elapsed / interval) + 1
            next_beat_t = m.synced_at + beat_n * interval

            wait = next_beat_t - time.perf_counter()
            if wait > 0.002:
                time.sleep(wait - 0.002)
            while time.perf_counter() < next_beat_t:
                pass  # busy-wait the final ~2ms for precision

            if not m.running or not self._running:
                continue
            if sd is not None and self._click_accent is not None:
                is_downbeat = beat_n % m.beats_per_bar == 1
                try:
                    sd.play(self._click_accent if is_downbeat else self._click_normal, samplerate=44100)
                except Exception as e:
                    log.debug(f"metronome click playback failed: {e}")

    def stop(self):
        self._running = False


_click_thread: Optional[MetronomeClickThread] = None


def start_click_thread():
    """Idempotent -- safe to call every time whiplash mode is entered."""
    global _click_thread
    if _click_thread is None:
        _click_thread = MetronomeClickThread(_metronome)
        _click_thread.start()


def score_hits(metronome: Metronome, hits: list) -> dict:
    """Score recorded MIDI hits against the metronome's synced grid.
    hits: list of (perf_counter_ts, note, velocity, piece) tuples, as
    returned by whiplash_midi.MidiListener.get_recent_hits(). Only
    kick/snare hits count toward pocket timing."""
    scored = [h for h in hits if h[3] in SCORED_PIECES]
    if not scored:
        return {"count": 0}

    deviations = [metronome.deviation_ms(h[0]) for h in scored]
    abs_devs = [abs(d) for d in deviations]
    pocket = sum(1 for d in abs_devs if d < 10)
    rushing = sum(1 for d in deviations if d <= -10)
    dragging = sum(1 for d in deviations if d >= 10)
    worst_idx = max(range(len(abs_devs)), key=lambda i: abs_devs[i])

    return {
        "count": len(scored),
        "avg_abs_deviation_ms": round(sum(abs_devs) / len(abs_devs), 1),
        "worst_deviation_ms": round(deviations[worst_idx], 1),
        "worst_piece": scored[worst_idx][3],
        "pocket_count": pocket,
        "pocket_pct": round(pocket / len(scored) * 100),
        "rushing_count": rushing,
        "dragging_count": dragging,
    }


# ── Funk grooves ──────────────────────────────────────────────────────
# "pattern" is a 16th-note grid (0-15 per bar) for straight-feel grooves,
# or a triplet grid (0-11 per bar, 3 subdivisions per beat) for shuffle-
# feel grooves -- see "subdivision" per entry.

FUNK_GROOVES = {
    "the_pocket": {
        "name": "The Pocket",
        "artist_credit": "No one in particular -- the foundation every groove below is built on",
        "bpm_range": (85, 100),
        "subdivision": "16th",
        "structure": (
            "Kick on 1 and 3, snare backbeat on 2 and 4, steady 8th-note "
            "hi-hat throughout. No ghost notes, no syncopation -- just lock "
            "in with the click."
        ),
        "pattern": (
            [{"step": s, "piece": "kick", "velocity": 100} for s in (0, 8)]
            + [{"step": s, "piece": "snare", "velocity": 110} for s in (4, 12)]
            + [{"step": s, "piece": "hihat_closed", "velocity": 70} for s in range(0, 16, 2)]
        ),
        "fletcher_intro": (
            "Before you touch anything with syncopation in it, you're "
            "playing this until it's boring. Boring is the point. Kick on "
            "1 and 3, snare on 2 and 4, hi-hat never wavers. If this isn't "
            "rock solid, nothing else matters."
        ),
        "fletcher_criticism": [
            "Your hi-hat sped up on beat 3. I heard it. You think I didn't hear it?",
            "That's not a backbeat, that's a suggestion. Hit the snare like you mean it.",
            "The kick on beat 3 was late. Late is late. There's no 'basically on time.'",
        ],
        "fletcher_rare_praise": [
            "Fine. That was actually in the pocket. Don't get comfortable.",
        ],
    },
    "funky_drummer": {
        "name": "Funky Drummer",
        "artist_credit": "Clyde Stubblefield -- James Brown, 1970",
        "bpm_range": (95, 102),
        "subdivision": "16th",
        "structure": (
            "16th-note hi-hat throughout. Kick lands on 1, the 'and' of 2, "
            "and the 'e' of 4 -- syncopated, not on the downbeats you'd "
            "expect. Snare backbeat on 2 and 4 with ghost notes filling the "
            "gaps at low velocity. This is the most sampled drum break in "
            "history for a reason: it never stops moving but never rushes."
        ),
        "pattern": (
            [{"step": s, "piece": "kick", "velocity": 105} for s in (0, 6, 14)]
            + [{"step": s, "piece": "snare", "velocity": 110} for s in (4, 12)]
            + [{"step": s, "piece": "snare", "velocity": 40} for s in (2, 7, 9, 15)]  # ghost notes
            + [{"step": s, "piece": "hihat_closed", "velocity": 65} for s in range(16)]
        ),
        "fletcher_intro": (
            "Clyde Stubblefield. Nineteen seventy. This break has been "
            "sampled more than anything else in the history of recorded "
            "music and he probably never saw a dime for most of it. The "
            "ghost notes are not optional -- if your snare hand can't go "
            "from a whisper to a crack in the same bar, you don't have the "
            "control for this yet."
        ),
        "fletcher_criticism": [
            "Your ghost notes are as loud as your backbeat. That's not a ghost, that's a second snare hit.",
            "The kick on the 'e' of 4 didn't land -- you skipped straight to beat 1. Again.",
            "This is supposed to breathe. You're playing it like a robot with a grudge.",
        ],
        "fletcher_rare_praise": [
            "That ghost-to-backbeat dynamic just now -- that's the whole lesson. Do that again.",
        ],
    },
    "purdie_shuffle": {
        "name": "Purdie Shuffle",
        "artist_credit": "Bernard \"Pretty\" Purdie",
        "bpm_range": (78, 92),
        "subdivision": "8th_triplet",
        "structure": (
            "Half-time shuffle feel over a triplet subdivision. Snare "
            "ghost notes fill the triplet grid between backbeats on 2 and "
            "4 (half-time, so those land on beats 3 and... no, on the "
            "downbeats every other bar in true half-time counting -- for "
            "practice purposes, snare backbeat every other beat, ghost "
            "notes on the remaining triplet partials). Kick is sparse: "
            "beat 1 and a syncopated hit around beat 3's last triplet."
        ),
        "pattern": (
            [{"step": s, "piece": "kick", "velocity": 100} for s in (0, 8)]
            + [{"step": s, "piece": "snare", "velocity": 110} for s in (6,)]
            + [{"step": s, "piece": "snare", "velocity": 35} for s in (1, 2, 4, 5, 7, 9, 10)]
            + [{"step": s, "piece": "hihat_closed", "velocity": 60} for s in range(12)]
        ),
        "fletcher_intro": (
            "Bernard Purdie. If you don't know his name you've still heard "
            "his hands -- half the records you own have this shuffle "
            "buried in them. This is a triplet feel, not straight 16ths. "
            "If you play this straight it's not a shuffle, it's just wrong."
        ),
        "fletcher_criticism": [
            "You flattened the triplet into straight time. This is a SHUFFLE. Feel the third partial.",
            "Your ghost notes rushed into the backbeat instead of sitting under it.",
            "That kick placement was a guess, not a groove. Where's beat 1?",
        ],
        "fletcher_rare_praise": [
            "The triplet lift was actually there that time. That's a genuinely hard feel to nail.",
        ],
    },
    "cold_sweat": {
        "name": "Cold Sweat",
        "artist_credit": "Clyde Stubblefield -- James Brown, 1967",
        "bpm_range": (110, 120),
        "subdivision": "16th",
        "structure": (
            "One of the first true 'one' grooves -- everything resolves "
            "hard back to beat 1. Kick is sparse and syncopated (1, and "
            "a hit just before beat 3), snare is a tight, dry backbeat on "
            "2 and 4 with almost no ghost notes -- this is about space, "
            "not density."
        ),
        "pattern": (
            [{"step": s, "piece": "kick", "velocity": 105} for s in (0, 7)]
            + [{"step": s, "piece": "snare", "velocity": 115} for s in (4, 12)]
            + [{"step": s, "piece": "hihat_closed", "velocity": 55} for s in range(0, 16, 2)]
        ),
        "fletcher_intro": (
            "Cold Sweat. This is where 'the one' became a religion -- "
            "everything in this groove exists to make beat 1 land like a "
            "hammer. The space between hits matters as much as the hits. "
            "Do not fill the space. That's the whole lesson."
        ),
        "fletcher_criticism": [
            "You added notes that aren't there. This groove is about restraint. Stop decorating it.",
            "Beat 1 didn't hit hard enough to justify everything leading up to it.",
            "You rushed into beat 1 like you were relieved to get there. Sit in the space first.",
        ],
        "fletcher_rare_praise": [
            "That beat 1 actually landed like it meant something. Good.",
        ],
    },
    "rosanna_shuffle": {
        "name": "Rosanna Shuffle",
        "artist_credit": "Jeff Porcaro -- Toto, 1982",
        "bpm_range": (84, 90),
        "subdivision": "8th_triplet",
        "structure": (
            "Half-time shuffle blending a Purdie-style triplet feel with a "
            "Bonham-style half-time backbeat on 3. Kick doubles up under "
            "the shuffle, snare ghost notes thread the triplet gaps, "
            "backbeat lands once per bar. Famously hard to play convincingly."
        ),
        "pattern": (
            [{"step": s, "piece": "kick", "velocity": 100} for s in (0, 3, 8)]
            + [{"step": s, "piece": "snare", "velocity": 115} for s in (6,)]
            + [{"step": s, "piece": "snare", "velocity": 30} for s in (1, 4, 7, 9, 10)]
            + [{"step": s, "piece": "hihat_closed", "velocity": 60} for s in range(12)]
        ),
        "fletcher_intro": (
            "Jeff Porcaro. Session drummers spend years chasing this one "
            "and most of them still don't get the ghost notes right. It's "
            "two feels stacked on top of each other -- if it sounds like "
            "you're doing one thing, you're not doing it right."
        ),
        "fletcher_criticism": [
            "That was a Purdie shuffle with extra steps, not a Rosanna. The kick doubling is missing.",
            "Your ghost notes are too even -- Porcaro's breathe, they don't tick like a clock.",
            "You landed the backbeat early. In half-time, that's the one moment everything hangs on. Don't rush it.",
        ],
        "fletcher_rare_praise": [
            "That's the closest I've heard to the real thing out of you. Don't let it go to your head.",
        ],
    },
}


@dataclass
class WhiplashSession:
    active: bool = False              # a groove practice session is underway
    current_groove: str = ""
    groove_started_at: float = 0.0
    clone_hero_artist: str = ""
    clone_hero_song: str = ""


_session: Optional[WhiplashSession] = None


def get_session() -> WhiplashSession:
    global _session
    if _session is None:
        _session = WhiplashSession()
    return _session


def clear_session():
    global _session
    _session = WhiplashSession()


_GROOVE_STOPWORDS = {"the", "a", "an", "groove", "shuffle", "beat", "practice"}


def find_groove_key(name: str) -> Optional[str]:
    """Fuzzy-ish match against FUNK_GROOVES, same staged approach as
    integrations/masterchef.py's find_recipe_key: exact key, exact
    display name, substring, then token-overlap ignoring filler words."""
    if not name:
        return None
    key_guess = name.strip().lower().replace(" ", "_").replace("-", "_")
    if key_guess in FUNK_GROOVES:
        return key_guess

    name_lower = name.strip().lower()
    for key, groove in FUNK_GROOVES.items():
        if groove["name"].lower() == name_lower:
            return key
    for key, groove in FUNK_GROOVES.items():
        if name_lower in groove["name"].lower() or name_lower in key:
            return key

    tokens = {t for t in name_lower.replace("-", " ").split() if t not in _GROOVE_STOPWORDS and len(t) > 2}
    if not tokens:
        return None
    best_key, best_overlap = None, 0
    for key, groove in FUNK_GROOVES.items():
        haystack = {t for t in (groove["name"].lower() + " " + key.replace("_", " ")).split() if t not in _GROOVE_STOPWORDS}
        overlap = len(tokens & haystack)
        if overlap > best_overlap:
            best_overlap, best_key = overlap, key
    return best_key
