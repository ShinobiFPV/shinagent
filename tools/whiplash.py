"""
IMQ2 Whiplash Tools
Fletcher-voiced drum practice tools, wrapping integrations/whiplash.py's
Metronome/FUNK_GROOVES/WhiplashSession and integrations/whiplash_midi.py's
MIDI hit log.
"""
import logging
import random

log = logging.getLogger(__name__)


def _fletcher_intensity() -> int:
    from config.loader import config
    try:
        return int(config.get("whiplash.fletcher_intensity", 8))
    except (TypeError, ValueError):
        return 8


def start_metronome(bpm: int = 100) -> str:
    """Start the metronome at the given BPM. Does not sync the grid by
    itself -- call sync_metronome() the instant beat 1 actually lands, or
    this beat 1 is just whenever start_metronome() happened to be called."""
    try:
        from config.loader import config
        if not config.get("whiplash.enabled", True):
            return "[start_metronome] Whiplash mode is disabled in config.yaml (whiplash.enabled)."

        from integrations.whiplash import get_metronome, start_click_thread
        metronome = get_metronome()
        metronome.start(bpm=bpm)
        start_click_thread()
        return f"Metronome running at {metronome.bpm:.0f} BPM. Say 'sync' the instant you hear beat one."
    except Exception as e:
        log.error(f"start_metronome error: {e}", exc_info=True)
        return f"[start_metronome] Error: {e}"


def stop_metronome() -> str:
    """Stop the metronome."""
    try:
        from integrations.whiplash import get_metronome
        get_metronome().stop()
        return "Metronome stopped."
    except Exception as e:
        log.error(f"stop_metronome error: {e}", exc_info=True)
        return f"[stop_metronome] Error: {e}"


def sync_metronome() -> str:
    """Tap-to-sync: call this the exact instant beat 1 of the song lands.
    Aligns the grid to right now."""
    try:
        from integrations.whiplash import get_metronome
        metronome = get_metronome()
        metronome.sync()
        from integrations.whiplash_midi import get_listener
        get_listener().clear_hits()  # scoring should only count hits after the new sync point
        return "Synced. Beat one is now. Play."
    except Exception as e:
        log.error(f"sync_metronome error: {e}", exc_info=True)
        return f"[sync_metronome] Error: {e}"


def set_tempo(bpm: int) -> str:
    """Change the metronome's tempo without resetting the sync point."""
    try:
        from integrations.whiplash import get_metronome
        metronome = get_metronome()
        metronome.set_bpm(bpm)
        return f"Tempo set to {metronome.bpm:.0f} BPM."
    except Exception as e:
        log.error(f"set_tempo error: {e}", exc_info=True)
        return f"[set_tempo] Error: {e}"


def get_midi_status() -> str:
    """Report whether a MIDI kit is connected and what port it's on."""
    try:
        from integrations.whiplash_midi import get_listener, RTMIDI_AVAILABLE
        if not RTMIDI_AVAILABLE:
            return "[get_midi_status] python-rtmidi isn't installed -- MIDI input isn't available in this environment."
        snap = get_listener().snapshot()
        if not snap["running"]:
            return "[get_midi_status] No MIDI kit connected yet."
        return f"Connected to {snap['port']}. {snap['hit_count']} hits recorded this session."
    except Exception as e:
        log.error(f"get_midi_status error: {e}", exc_info=True)
        return f"[get_midi_status] Error: {e}"


def get_timing_stats() -> str:
    """Fletcher's read on your recent kick/snare timing against the
    synced metronome grid."""
    try:
        from integrations.whiplash import get_metronome, score_hits
        from integrations.whiplash_midi import get_listener

        metronome = get_metronome()
        if not metronome.is_synced():
            return "[get_timing_stats] Not synced yet. Hit sync the instant you hear beat one, then play something."

        hits = get_listener().get_recent_hits(since=metronome.synced_at)
        stats = score_hits(metronome, hits)
        if stats["count"] == 0:
            return "[get_timing_stats] Nothing recorded yet -- no MIDI kit connected, or you haven't played anything."

        intensity = _fletcher_intensity()
        lines = []
        if stats["pocket_pct"] >= 85:
            lines.append("Finally. That's the pocket.")
        elif intensity >= 8:
            lines.append(random.choice([
                "Not quite my tempo.",
                "Were you rushing? Or were you dragging?",
                "That was... painful.",
            ]))

        lines.append(f"{stats['count']} hits scored -- {stats['pocket_pct']}% in the pocket, "
                      f"average {stats['avg_abs_deviation_ms']}ms off the grid.")
        if stats["rushing_count"] > stats["dragging_count"]:
            lines.append(f"You're rushing -- {stats['rushing_count']} hits ahead of the beat. Sit back on it.")
        elif stats["dragging_count"] > stats["rushing_count"]:
            lines.append(f"You're dragging -- {stats['dragging_count']} hits behind. Push it.")
        lines.append(f"Worst offender: {stats['worst_piece']} at {stats['worst_deviation_ms']}ms.")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"get_timing_stats error: {e}", exc_info=True)
        return f"[get_timing_stats] Error: {e}"


def list_grooves() -> str:
    """List the available funk grooves to practice."""
    try:
        from integrations.whiplash import FUNK_GROOVES
        lines = ["Grooves on offer:"]
        for groove in FUNK_GROOVES.values():
            bpm_lo, bpm_hi = groove["bpm_range"]
            lines.append(f"- {groove['name']} ({groove['artist_credit']}) -- {bpm_lo}-{bpm_hi} BPM")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"list_grooves error: {e}", exc_info=True)
        return f"[list_grooves] Error: {e}"


def get_groove_info(groove: str) -> str:
    """Full teaching description of a named groove, without starting
    practice mode."""
    try:
        from integrations.whiplash import FUNK_GROOVES, find_groove_key
        key = find_groove_key(groove)
        if not key:
            return f"[get_groove_info] Don't recognise '{groove}'. Use list_grooves to see what's available."
        g = FUNK_GROOVES[key]
        bpm_lo, bpm_hi = g["bpm_range"]
        return (f"{g['name']} -- {g['artist_credit']} ({bpm_lo}-{bpm_hi} BPM)\n\n"
                f"{g['structure']}")
    except Exception as e:
        log.error(f"get_groove_info error: {e}", exc_info=True)
        return f"[get_groove_info] Error: {e}"


def start_groove_practice(groove: str) -> str:
    """Begin practicing a named funk groove -- sets tempo to the middle
    of its BPM range, syncs the grid to right now, and clears the hit log
    so scoring starts fresh."""
    try:
        from config.loader import config
        if not config.get("whiplash.enabled", True):
            return "[start_groove_practice] Whiplash mode is disabled in config.yaml (whiplash.enabled)."

        from integrations.whiplash import FUNK_GROOVES, find_groove_key, get_metronome, get_session, start_click_thread
        from integrations.whiplash_midi import get_listener

        key = find_groove_key(groove)
        if not key:
            return f"[start_groove_practice] Don't recognise '{groove}'. Use list_grooves to see what's available."
        g = FUNK_GROOVES[key]

        bpm_lo, bpm_hi = g["bpm_range"]
        metronome = get_metronome()
        metronome.start(bpm=(bpm_lo + bpm_hi) / 2)
        metronome.sync()
        start_click_thread()
        get_listener().clear_hits()

        session = get_session()
        session.active = True
        session.current_groove = key

        import time as _time
        session.groove_started_at = _time.time()

        return f"{g['fletcher_intro']}\n\nMetronome synced at {metronome.bpm:.0f} BPM. Go."
    except Exception as e:
        log.error(f"start_groove_practice error: {e}", exc_info=True)
        return f"[start_groove_practice] Error: {e}"


def fletcher_critique(problem: str) -> str:
    """User describes something going wrong while practicing; Fletcher
    diagnoses it in character, grounded in whatever groove is active."""
    try:
        from core.llm import get_llm_backend
        from integrations.whiplash import FUNK_GROOVES, get_session

        context = ""
        session = get_session()
        if session.active and session.current_groove in FUNK_GROOVES:
            g = FUNK_GROOVES[session.current_groove]
            context = f"They are practicing {g['name']} ({g['artist_credit']}). {g['structure']}"

        system = (
            "You are Fletcher from Whiplash -- a brutal, exacting drum instructor who "
            "secretly wants the student to succeed. The user describes a problem while "
            "practicing drums. Give a SPECIFIC technical diagnosis and a fix -- not generic "
            "reassurance. Stay in character: intense, precise, no patience for excuses, but "
            "never abandon them. 2-4 sentences, spoken-word style, no markdown.\n"
            + (context or "No specific groove context -- diagnose from the description alone.")
        )

        llm = get_llm_backend()
        response = llm.complete(
            messages=[{"role": "user", "content": problem}],
            system=system,
            max_tokens=300,
        )
        return response.text.strip()
    except Exception as e:
        log.error(f"fletcher_critique error: {e}", exc_info=True)
        return f"[fletcher_critique] Error: {e}"
