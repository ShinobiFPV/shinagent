"""
IMQ2 Whiplash MIDI Integration
Reads live note-on events from a physical MIDI drum kit (e.g. a CME
H4MIDI trigger interface) via python-rtmidi. Soft dependency -- python-
rtmidi needs a C++ build toolchain on Windows (see requirements.txt), so
this module degrades to "no MIDI input available" rather than crashing
import, matching the chromadb/headroom soft-dependency convention.

Note numbers follow the General MIDI percussion map. Every recognised
note is logged, but only kick/snare currently feed timing-pocket scoring
(tools/whiplash.py's get_timing_stats()) -- toms/cymbals/hi-hat are kept
for the recent-hits feed and future instrument-scoring modes (see the
architecture note "Future: other instruments" -- the listener already
handles all MIDI notes, so a new analyser mode just needs to read this
same hit log differently).
"""
import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

try:
    import rtmidi
    RTMIDI_AVAILABLE = True
except ImportError:
    RTMIDI_AVAILABLE = False
    log.warning("python-rtmidi not installed -- Whiplash MIDI input disabled (see requirements.txt)")

DRUM_NOTE_MAP = {
    35: "kick", 36: "kick",
    38: "snare", 40: "snare",
    42: "hihat_closed", 44: "hihat_pedal", 46: "hihat_open",
    41: "tom_low", 43: "tom_low", 45: "tom_mid", 47: "tom_mid", 48: "tom_high", 50: "tom_high",
    49: "crash", 57: "crash",
    51: "ride", 59: "ride",
}
SCORED_PIECES = {"kick", "snare"}

_MAX_HIT_LOG = 500


class MidiListener:
    """Background rtmidi input listener. Call start() once; recent hits
    are available via get_recent_hits()/snapshot(). Thread-safe (rtmidi's
    callback fires on its own C++ thread, not the Python thread that
    called start())."""

    def __init__(self):
        self._lock = threading.Lock()
        self._midi_in = None
        self._port_name: Optional[str] = None
        self._hits: list = []  # [(perf_counter_ts, note, velocity, piece), ...]
        self._running = False

    def list_ports(self) -> list:
        if not RTMIDI_AVAILABLE:
            return []
        probe = rtmidi.MidiIn()
        try:
            return probe.get_ports()
        finally:
            del probe

    def auto_detect_port(self) -> Optional[int]:
        for i, name in enumerate(self.list_ports()):
            if "cme" in name.lower() or "h4midi" in name.lower():
                return i
        return None

    def start(self, port: str = "auto") -> str:
        if not RTMIDI_AVAILABLE:
            return "python-rtmidi is not installed -- MIDI input unavailable."
        if self._running:
            return f"Already listening on {self._port_name}."

        ports = self.list_ports()
        if not ports:
            return "No MIDI input ports found."

        index = None
        if port == "auto":
            index = self.auto_detect_port()
            if index is None:
                index = 0  # nothing matched CME/H4MIDI by name -- fall back to the first port
        else:
            for i, name in enumerate(ports):
                if port.lower() in name.lower():
                    index = i
                    break
            if index is None:
                return f"No MIDI port matching '{port}' found. Available: {', '.join(ports)}"

        self._midi_in = rtmidi.MidiIn()
        self._midi_in.open_port(index)
        self._midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        self._midi_in.set_callback(self._on_message)
        self._port_name = ports[index]
        self._running = True
        return f"Listening on {self._port_name}."

    def stop(self):
        if self._midi_in is not None:
            self._midi_in.close_port()
            del self._midi_in
            self._midi_in = None
        self._running = False
        self._port_name = None

    def _on_message(self, event, data=None):
        message, _delta = event
        if len(message) < 3:
            return
        status, note, velocity = message[0], message[1], message[2]
        if (status & 0xF0) != 0x90 or velocity == 0:
            return  # only real note-on messages, not note-off or note-on-with-zero-velocity
        ts = time.perf_counter()
        piece = DRUM_NOTE_MAP.get(note, f"note_{note}")
        with self._lock:
            self._hits.append((ts, note, velocity, piece))
            if len(self._hits) > _MAX_HIT_LOG:
                self._hits = self._hits[-_MAX_HIT_LOG:]

    def get_recent_hits(self, since: float = 0.0) -> list:
        with self._lock:
            return [h for h in self._hits if h[0] >= since]

    def clear_hits(self):
        with self._lock:
            self._hits = []

    def snapshot(self) -> dict:
        with self._lock:
            recent = list(self._hits[-10:])
            total = len(self._hits)
        return {
            "available": RTMIDI_AVAILABLE,
            "running": self._running,
            "port": self._port_name,
            "hit_count": total,
            "last_hits": [{"piece": p, "velocity": v} for _, _, v, p in recent],
        }


_listener: Optional[MidiListener] = None


def get_listener() -> MidiListener:
    global _listener
    if _listener is None:
        _listener = MidiListener()
    return _listener
