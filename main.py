#!/usr/bin/env python3
"""
IMQ2 — Main Entry Point
"I am Q too"

Usage:
  python main.py             # voice mode (default)
  python main.py --text      # text/CLI mode (no mic/speaker required)
  python main.py --profile q2_guest   # load a non-default personality profile
"""

import argparse
import logging
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Ensure project root is on the path regardless of invocation directory
sys.path.insert(0, str(Path(__file__).parent))

from config.loader import config
from core.agent import IMQ2Agent

log = logging.getLogger(__name__)


def setup_logging():
    level = getattr(logging, config.get("logging.level", "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [logging.StreamHandler()]
    if config.get("logging.file"):
        log_path = Path(__file__).parent / config.get("logging.file")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(log_path)))
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def check_env(text_mode: bool = False):
    """Warn about missing API keys based on active backends.
    In text mode, voice (STT/TTS) keys aren't required since audio is never touched."""
    required = []
    backend = config.get("llm.backend", "claude")
    if backend == "claude":
        required.append("ANTHROPIC_API_KEY")
    elif backend == "openai":
        required.append("OPENAI_API_KEY")
    elif backend == "grok":
        required.append("XAI_API_KEY")
    elif backend == "gemini":
        required.append("GEMINI_API_KEY")
    # Note: glm's ZAI_API_KEY is deliberately not required here — GLMBackend
    # falls back to a local Ollama endpoint when it's unset, per core/llm.py.

    if not text_mode:
        stt = config.get("voice.stt_backend", "deepgram")
        if stt == "deepgram":
            required.append("DEEPGRAM_API_KEY")
        elif stt == "whisper_cloud":
            required.append("OPENAI_API_KEY")

        tts = config.get("voice.tts_backend", "deepgram_tts")
        if tts == "elevenlabs":
            required.append("ELEVENLABS_API_KEY")
        elif tts == "openai_tts":
            required.append("OPENAI_API_KEY")
        elif tts == "deepgram_tts":
            required.append("DEEPGRAM_API_KEY")

    # de-dupe while preserving order
    required = list(dict.fromkeys(required))

    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"⚠  Missing environment variables: {', '.join(missing)}")
        print("   Set them in your shell or in a .env file and source it.")
        return False
    return True


def run_text_mode(agent: IMQ2Agent):
    """Simple REPL for development / testing without audio hardware."""
    name = config.profile.get("name", "Q2")
    print(f"\n{'='*50}")
    print(f"  IMQ2 — Text Mode  |  Active: {name}")
    print(f"  Commands: /quit  /reset  /profile <name>  /tools  /facts [subject]  /wakeword")
    print(f"{'='*50}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down.")
            break

        if not user_input:
            # Check restart flag in text mode too
            from pathlib import Path as _Path
            _flag = _Path(__file__).parent / ".restart_requested"
            if _flag.exists():
                _flag.unlink()
                print("\n[Webapp: restart requested — restarting Q2...]")
                import os as _os
                _os.execv(sys.executable, [sys.executable] + sys.argv)
            continue

        # Meta-commands
        if user_input.startswith("/"):
            parts = user_input[1:].split(maxsplit=1)
            cmd = parts[0].lower()

            if cmd == "quit":
                print("Goodbye.")
                break
            elif cmd == "reset":
                agent.reset_short_term()
                print("[Context cleared]")
            elif cmd == "profile" and len(parts) > 1:
                try:
                    agent.switch_profile(parts[1])
                    print(f"[Profile switched to: {parts[1]}]")
                except FileNotFoundError as e:
                    print(f"[Error: {e}]")
            elif cmd == "tools":
                for t in agent.tools.list_tools():
                    status = "✓ granted" if t["granted"] else "✗ locked"
                    print(f"  {t['name']:20s} {status}  — {t['description']}")
            elif cmd == "profiles":
                print("Available profiles:", ", ".join(config.list_profiles()))
            elif cmd == "wakeword":
                current = config.get("voice.wake_word.enabled", False)
                new_val = not current
                config.raw.setdefault("voice", {}).setdefault("wake_word", {})["enabled"] = new_val
                state = "enabled" if new_val else "disabled"
                print(f"[Wake word {state} — takes effect on next voice-mode launch]")
            elif cmd == "facts":
                # /facts            -> list all current facts
                # /facts <subject>  -> show change history for one subject
                if len(parts) > 1:
                    subject = parts[1].strip()
                    history = agent.memory.get_fact_history(subject)
                    if not history:
                        print(f"  No history found for subject '{subject}'.")
                    else:
                        print(f"  History for '{subject}':")
                        for h in history:
                            old = h["old_content"] or "(new)"
                            print(f"    {h['changed_at']}  {old}  ->  {h['new_content']}")
                else:
                    facts = agent.memory.get_facts_detailed()
                    if not facts:
                        print("  No facts stored yet.")
                    else:
                        for f in facts:
                            print(f"  [{f['subject']:20s}] {f['content']}  ({f['category']}, updated {f['updated_at'][:10]})")
                        print(f"\n  Use /facts <subject> to see change history for one fact.")
            else:
                print(f"[Unknown command: /{cmd}]")
            continue

        reply = agent.chat(user_input)
        print(f"\n{name}: {reply}\n")


def run_voice_mode(agent: IMQ2Agent):
    """
    Voice loop. Push-to-talk via either a dedicated talk button (8BitDo Zero 2
    or Flipper Zero in keyboard mode, or any keypress) or the Enter key as a
    fallback. STT transcribes, Q2 responds, TTS speaks the reply.
    Wake-word detection is deferred to a later pass — see voice.wake_word
    in config.yaml; Porcupine has no built-in "Q2" keyword, so that needs
    a custom-trained model via Picovoice Console before it's worth wiring in.
    """
    from voice.pipeline import get_stt, get_tts, AudioIO

    name = config.profile.get("name", "Q2")

    stt = get_stt()
    audio = AudioIO()
    # TTS is deliberately NOT cached here — get_tts() is a stateless factory
    # that reads voice.deepgram_tts.model fresh on every call (see
    # voice/pipeline.py), so calling it again at each synthesize() below is
    # what lets a voice change made mid-session via Settings take effect on
    # the very next reply, with no restart and no separate hot-reload plumbing.

    talk_button_listener = None
    talk_button_state = None
    wake_word_listener = None

    if config.get("voice.talk_button.enabled", False):
        from voice.talk_button import start_controller_bridge, talk_button_state as tb_state
        talk_button_listener = start_controller_bridge(audio)
        if talk_button_listener:
            talk_button_state = tb_state
            print(f"\n{name} voice mode active — controller ready (8BitDo Zero 2, gamepad mode).")
        else:
            print(f"\n{name} voice mode active — controller unavailable, using Enter key push-to-talk.")
    else:
        print(f"\n{name} voice mode active — push-to-talk (Enter key).")

    # Start wake word detector if enabled
    if config.get("voice.wake_word.enabled", False):
        try:
            from voice.pipeline import WakeWordDetector
            wake_word_listener = WakeWordDetector()
            wake_word_listener.start()
            print(f"{name} wake word active — say 'Hey Dude' to activate.")
        except Exception as e:
            print(f"[Warning] Wake word init failed: {e} — continuing without it.")
            wake_word_listener = None

    print("Ready. Ctrl+C to exit.\n")

    try:
        while True:
            # Check for settings-panel restart request
            # Check for webapp-requested restart (LLM switch etc.)
            from pathlib import Path as _Path
            _flag = _Path(__file__).parent / ".restart_requested"
            if _flag.exists():
                _flag.unlink()
                print("\n[Webapp: restart requested — restarting Q2...]")
                import os as _os
                _os.execv(sys.executable, [sys.executable] + sys.argv)

            if config.get("face.port"):
                try:
                    from face.server import settings_state
                    if settings_state.consume_restart_request():
                        print("\n[Settings: restarting in text mode]")
                        run_text_mode(agent)
                        return
                except ImportError:
                    pass

            if wake_word_listener is not None and talk_button_state is not None:
                # Wake word + PTT combo — "Hey Dude" activates and recording
                # starts immediately (no button press needed to start); it
                # auto-stops on VAD silence detection, or the talk button
                # can still stop it early — whichever comes first.
                print("Listening for wake word...")
                wake_word_listener.wait_for_wake_word()
                audio.play_tone(frequency=880, duration_s=0.12)
                audio._signal_face_listening(True)
                print("🎙  Recording... auto-stops on silence, or press talk button to stop early.")
                audio_bytes = audio.record_utterance_vad(early_stop_check=talk_button_state.consume_toggle)
                audio._signal_face_listening(False)
                wake_word_listener.rearm()
            elif wake_word_listener is not None:
                # Wake word only — fully hands-free: recording starts the
                # instant the wake word fires and auto-stops on VAD silence
                # detection, no Enter press needed.
                print("Listening for wake word...")
                wake_word_listener.wait_for_wake_word()
                audio.play_tone(frequency=880, duration_s=0.12)
                audio._signal_face_listening(True)
                print("🎙  Recording... auto-stops on silence.")
                audio_bytes = audio.record_utterance_vad()
                audio._signal_face_listening(False)
                wake_word_listener.rearm()
            elif talk_button_state is not None:
                audio_bytes = audio.record_utterance_button(talk_button_state)
            else:
                audio_bytes = audio.record_utterance_ptt()

            if not audio_bytes:
                continue

            try:
                print("Transcribing...")
                transcript = stt.transcribe(audio_bytes)
                if not transcript.strip():
                    print("(Heard nothing — try again.)")
                    continue
                print(f"You: {transcript}")

                # Signal thinking state — face turns white while Q2 processes
                try:
                    from face.server import face_state
                    face_state.set_thinking(True)
                except ImportError:
                    pass

                reply = agent.chat(transcript)

                # Note: set_thinking(False) is NOT called here — it's called inside
                # AudioIO.play() right before sound actually starts, so the white
                # thinking state persists through TTS synthesis and up to the moment
                # Q2's voice begins playing rather than flashing back to blue first.

                print(f"{name}: {reply}")

                speech = get_tts().synthesize(reply)
                audio.play(speech)
                print()
            except Exception as e:
                # A single bad turn (STT hiccup, TTS/network error, playback
                # glitch) must never take down the whole voice session — log
                # it and go back to listening for the next utterance instead.
                log.error(f"Voice turn failed: {e}", exc_info=True)
                print(f"[Error this turn: {e} — still listening.]")

    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        if talk_button_listener:
            talk_button_listener.stop()
        if wake_word_listener:
            wake_word_listener.delete()


class RaceEngineerAlertThread(threading.Thread):
    """
    Proactive race engineer / first officer callouts — speaks up without
    being asked while a race/session or flight is live, instead of only
    responding to explicit telemetry questions. Polls whichever sim
    listener is active (see tools/telemetry_source.py for Forza/AC;
    integrations/msfs_telemetry.py for MSFS), checks per-alert-type
    thresholds, and calls TTS + AudioIO directly in-process (the same way
    run_voice_mode() does) rather than round-tripping through the webapp's
    /tts endpoint, since that endpoint only returns WAV bytes to its HTTP
    caller and never touches the host's speakers itself.

    Gated on two things, both re-read from config every poll cycle so the
    settings panel and profile switches take effect live without a restart:
    - race_engineer.frequency ("off" disables the thread's alerts entirely,
      for both racing and flight callouts)
    - the active personality profile: RaceEngineer (personality/profiles/
      race_engineer.yaml) for Forza/AC, or First Officer (personality/profiles/
      first_officer.yaml) for MSFS — alerts don't fire while chatting in a
      normal profile even if a sim happens to be running in the background.
    """

    POLL_INTERVAL_S = 3.0

    # Per-alert-type cooldowns (seconds) by frequency level. Six racing
    # buckets match the six independently-timed callout categories; tyre
    # wear shares the "tyres" bucket with tyre temperature since both are
    # corner-health calls that shouldn't interrupt each other back-to-back.
    COOLDOWNS = {
        "sparse": {"fuel": 120, "tyres": 180, "damage": 120, "flag": 30, "pit": 60, "gap": 90},
        "normal": {"fuel": 60,  "tyres": 90,  "damage": 60,  "flag": 20, "pit": 30, "gap": 45},
        "chatty": {"fuel": 30,  "tyres": 45,  "damage": 30,  "flag": 10, "pit": 20, "gap": 20},
    }

    # Forza open-world "excited passenger" commentary, shares
    # race_engineer.frequency with the racing alerts above (same profile,
    # same dial) but its own cooldown floor per event category -- these
    # events are already self-limiting (a drift/landing/milestone only
    # fires once when it genuinely happens), so this mainly stops two
    # events queued in the same poll tick from talking over each other.
    OPENWORLD_COOLDOWNS = {
        "sparse": {"drift": 8, "air": 8, "speed": 15, "near_spin": 10, "location": 5,
                   "race_transition": 5, "race_position": 3, "race_status": 0},
        "normal": {"drift": 4, "air": 4, "speed": 8,  "near_spin": 5,  "location": 3,
                   "race_transition": 3, "race_position": 2, "race_status": 0},
        "chatty": {"drift": 2, "air": 2, "speed": 4,  "near_spin": 2,  "location": 1,
                   "race_transition": 2, "race_position": 1, "race_status": 0},
    }

    # Same idea for MSFS First Officer alerts, gated independently by
    # first_officer.frequency rather than race_engineer.frequency (the two
    # modes never run at once, but they're separate settings-panel knobs).
    # altitude/gear/fuel/waypoint/phase values are the ones product spec'd
    # explicitly; bank/autopilot/engine/approach/weather extend the same
    # per-frequency scaling by category urgency (bank/autopilot/engine are
    # safety-critical so short like gear; approach checklist is a single
    # per-approach reminder so roughly double waypoint's cadence; weather
    # is the least urgent so as long as altitude/fuel). phase has no
    # cooldown ("always") — it's gated by _last_flight_phase actually
    # changing, not by time; see _check_msfs().
    FO_COOLDOWNS = {
        "sparse": {"altitude": 300, "gear": 120, "fuel": 180, "waypoint": 60, "phase": 0,
                   "bank": 60, "autopilot": 60, "engine": 60, "approach": 120, "weather": 300},
        "normal": {"altitude": 120, "gear": 60,  "fuel": 90,  "waypoint": 30, "phase": 0,
                   "bank": 30, "autopilot": 30, "engine": 30, "approach": 60,  "weather": 120},
        "chatty": {"altitude": 60,  "gear": 30,  "fuel": 45,  "waypoint": 15, "phase": 0,
                   "bank": 15, "autopilot": 15, "engine": 15, "approach": 30,  "weather": 60},
    }

    _CORNER_NAMES = ("FL", "FR", "RL", "RR")
    _DAMAGE_AREA_NAMES = ("front", "rear", "left", "right", "centre")

    # F1 Watchalong event categories, gated by watchalong.live.frequency
    # (shared across sports — see UFC_FREQUENCY_TIERS below for the same
    # idea applied to UFC's own event categories). Tiers match the original
    # F1 Watchalong spec exactly (silent/sparse/normal/chatty each add one
    # more bucket on top of the last).
    F1_FREQUENCY_TIERS = {
        "silent": set(),
        "sparse": {"safety_car", "red_flag", "chequered_flag"},
        "normal": {"safety_car", "red_flag", "chequered_flag", "fastest_lap", "leader_change", "penalty"},
        "chatty": {"safety_car", "red_flag", "chequered_flag", "fastest_lap", "leader_change", "penalty",
                   "drs", "yellow_flag", "pit_stop_top5"},
    }
    # Short floor cooldowns per category — genuine dedup already happens in
    # LiveRaceWatcher (only NEW events are ever returned), this just stops
    # two same-category events landing in one poll tick from talking over
    # each other.
    F1_COOLDOWNS = {
        "sparse": {k: 20 for k in ("safety_car", "red_flag", "chequered_flag", "fastest_lap",
                                    "leader_change", "penalty", "drs", "yellow_flag", "pit_stop_top5", "other")},
        "normal": {k: 10 for k in ("safety_car", "red_flag", "chequered_flag", "fastest_lap",
                                    "leader_change", "penalty", "drs", "yellow_flag", "pit_stop_top5", "other")},
        "chatty": {k: 5 for k in ("safety_car", "red_flag", "chequered_flag", "fastest_lap",
                                   "leader_change", "penalty", "drs", "yellow_flag", "pit_stop_top5", "other")},
    }
    F1_POLL_INTERVAL_S = 5.0  # OpenF1 is a shared remote API — poll it less often than local sim telemetry

    # Elite Dangerous Ship Computer alerts, gated purely on the active profile
    # (COVAS — personality/profiles/ship_computer.yaml) rather than a
    # frequency dial: there's no settings-panel knob for this yet, so it's
    # simply on whenever that profile is active and telemetry is live.
    # No per-category cooldown table either — ed_alert() itself only ever
    # returns text for genuinely-changed conditions, so nothing here needs
    # to deduplicate repeats the way COOLDOWNS/FO_COOLDOWNS do.
    ED_POLL_INTERVAL_S = 5.0

    # UFC Watchalong event categories, gated by watchalong.live.frequency
    # (same shared setting as F1 — see F1_FREQUENCY_TIERS above).
    # NOTE: ESPN's public API has no reliable title-fight flag, so "title
    # fights"/"title change" from the original spec can't be detected —
    # main_event_result/main_event_starting are the closest honest
    # substitute (see integrations/ufc_data.py's docstring for the full
    # rundown of what ESPN's public API does/doesn't expose). "finish" is
    # a real, derived signal though: a fight ending before its scheduled
    # round count is a genuine stoppage, not a guess.
    UFC_FREQUENCY_TIERS = {
        "silent": set(),
        "sparse": {"finish", "main_event_result"},
        "normal": {"finish", "main_event_result", "fight_result", "main_event_starting"},
        "chatty": {"finish", "main_event_result", "fight_result", "main_event_starting", "round_transition"},
    }
    UFC_COOLDOWNS = {
        "sparse": {k: 30 for k in ("finish", "main_event_result", "fight_result", "main_event_starting", "round_transition")},
        "normal": {k: 20 for k in ("finish", "main_event_result", "fight_result", "main_event_starting", "round_transition")},
        "chatty": {k: 10 for k in ("finish", "main_event_result", "fight_result", "main_event_starting", "round_transition")},
    }
    UFC_POLL_INTERVAL_S = 30.0  # matches spec — ESPN scoreboard, not worth polling faster

    # NBA Watchalong event categories, gated by watchalong.live.frequency
    # (same shared setting as F1/UFC). integrations/nba_data.py's poll()
    # only emits period_start/score/final — it has no scoring-run or
    # true lead-change detector, so "lead_change" and "three_pointer" are
    # derived here from each raw "score" event's before/after margin
    # rather than coming pre-classified. Plain 2/1-point scores that are
    # neither a 3 nor a lead change never get their own category — BallDontLie
    # updates fire on every made basket, and announcing all of them would be
    # unbearable (an NBA game has 150-200+ scoring plays).
    NBA_FREQUENCY_TIERS = {
        "silent": set(),
        "sparse": {"final", "lead_change"},
        "normal": {"final", "lead_change", "period_start"},
        "chatty": {"final", "lead_change", "period_start", "three_pointer"},
    }
    NBA_COOLDOWNS = {
        "sparse": {k: 20 for k in ("final", "lead_change", "period_start", "three_pointer")},
        "normal": {k: 15 for k in ("final", "lead_change", "period_start", "three_pointer")},
        "chatty": {k: 8 for k in ("final", "lead_change", "period_start", "three_pointer")},
    }
    NBA_POLL_INTERVAL_S = 30.0  # per spec — NBA scoring is frequent, don't hammer BallDontLie

    # NHL Watchalong event categories. Goals are rare enough in hockey that
    # they're always in every non-silent tier per spec ("always announce
    # them regardless of frequency setting") — sparse adds nothing else,
    # normal adds period changes. integrations/nhl_data.py's poll() has no
    # shot/penalty event (the API's situationCode would need real parsing
    # to derive a genuine power-play signal, which isn't implemented), so
    # chatty doesn't add a category beyond normal.
    NHL_FREQUENCY_TIERS = {
        "silent": set(),
        "sparse": {"goal", "final"},
        "normal": {"goal", "final", "period_start"},
        "chatty": {"goal", "final", "period_start"},
    }
    NHL_COOLDOWNS = {
        "sparse": {k: 20 for k in ("goal", "final", "period_start")},
        "normal": {k: 12 for k in ("goal", "final", "period_start")},
        "chatty": {k: 6 for k in ("goal", "final", "period_start")},
    }
    NHL_POLL_INTERVAL_S = 20.0  # per spec — hockey moves fast, goals are rare but sudden

    # NFL Watchalong event categories, gated by watchalong.live.frequency
    # (same shared setting as NBA/NHL). integrations/nfl_data.py's poll()
    # classifies scoring by point value (_classify_nfl_score below) since
    # touchdowns/field goals/safeties are meaningfully different-weight
    # events, unlike NBA's flat basket-vs-three-pointer split.
    NFL_FREQUENCY_TIERS = {
        "silent": set(),
        "sparse": {"final", "touchdown"},
        "normal": {"final", "touchdown", "field_goal", "period_start"},
        "chatty": {"final", "touchdown", "field_goal", "safety", "period_start"},
    }
    NFL_COOLDOWNS = {
        "sparse": {k: 20 for k in ("final", "touchdown", "field_goal", "safety", "period_start")},
        "normal": {k: 15 for k in ("final", "touchdown", "field_goal", "safety", "period_start")},
        "chatty": {k: 8 for k in ("final", "touchdown", "field_goal", "safety", "period_start")},
    }
    NFL_POLL_INTERVAL_S = 25.0  # per spec — scoring happens in bursts, not worth polling faster

    # MLB Watchalong event categories. Home runs and triple plays are rare
    # enough to always announce (sparse); strikeouts are deliberately only
    # ever in the chatty tier (and even then only ~25% of them get spoken,
    # see tools/mlb_analyst.py's _format_mlb_event) since they happen every
    # few minutes and would otherwise spam every frequency setting equally.
    MLB_FREQUENCY_TIERS = {
        "silent": set(),
        "sparse": {"final", "home_run", "triple_play"},
        "normal": {"final", "home_run", "triple_play", "scoring_play", "inning_change"},
        "chatty": {"final", "home_run", "triple_play", "scoring_play", "inning_change", "double_play", "strikeout"},
    }
    MLB_COOLDOWNS = {
        "sparse": {k: 20 for k in ("final", "home_run", "triple_play", "scoring_play", "inning_change", "double_play", "strikeout")},
        "normal": {k: 15 for k in ("final", "home_run", "triple_play", "scoring_play", "inning_change", "double_play", "strikeout")},
        "chatty": {k: 8 for k in ("final", "home_run", "triple_play", "scoring_play", "inning_change", "double_play", "strikeout")},
    }
    MLB_POLL_INTERVAL_S = 20.0  # per spec — pitches are quick but scoring is infrequent

    def __init__(self):
        super().__init__(daemon=True, name="RaceEngineerAlerts")
        self._frequency = "off"
        self._fo_frequency = "off"
        self._wa_frequency = "off"  # shared by both F1 and UFC watchalong live checks
        self._running = False
        self._last_alert_at: dict[str, float] = {}
        self._last_flight_phase = None
        self._last_f1_poll = 0.0
        self._last_ufc_poll = 0.0
        self._last_ed_poll = 0.0
        self._last_ed_system = None
        self._ufc_event_id = None
        self._ufc_seen_results: set = set()
        self._ufc_last_round: dict[int, int] = {}
        self._ufc_main_event_live = False
        self._last_race_update = 0.0  # last Forza race_status periodic callout
        self._last_nba_poll = 0.0
        self._last_nhl_poll = 0.0
        self._last_nfl_poll = 0.0
        self._last_mlb_poll = 0.0

    def set_frequency(self, level: str):
        if level not in ("off", "sparse", "normal", "chatty"):
            log.warning(f"[race_engineer_alerts] Unknown frequency '{level}', ignoring")
            return
        self._frequency = level

    def run(self):
        self._running = True
        log.info("[race_engineer_alerts] Alert thread started")
        while self._running:
            try:
                self._tick()
            except Exception as e:
                # A single bad telemetry read should never kill the thread.
                log.debug(f"[race_engineer_alerts] tick error: {e}")
            time.sleep(self.POLL_INTERVAL_S)

    def stop(self):
        self._running = False

    def _tick(self):
        # Re-read live so settings-panel changes and profile switches apply
        # on the next poll without needing to restart the thread. Each
        # domain's "off" is checked only once we know which one applies —
        # race_engineer.frequency and first_officer.frequency are
        # independent knobs now, so one being off must not silently gate
        # the other.
        self._frequency = config.get("race_engineer.frequency", "off")
        self._fo_frequency = config.get("first_officer.frequency", "normal")
        self._wa_frequency = config.get("watchalong.live.frequency", "normal")

        profile_name = config.profile.get("name")
        if profile_name == "First Officer":
            if self._fo_frequency == "off":
                return
            self._check_msfs()
            return
        if profile_name == "Watchalong Live":
            # No proactive callouts at all in Watchalong Replay — only this
            # exact profile name triggers live polling (see the "NO
            # proactive callouts" note in watchalong_replay.yaml's persona).
            # Which sport's checker runs is config.yaml's
            # watchalong.active_sport, a runtime setting shared by both
            # watchalong profiles rather than a separate profile per sport.
            if self._wa_frequency in ("off", "silent"):
                return
            sport = config.get("watchalong.active_sport", "f1")
            if sport == "f1":
                self._check_f1()
            elif sport == "ufc":
                self._check_ufc()
            elif sport == "nba":
                self._check_nba()
            elif sport == "nhl":
                self._check_nhl()
            elif sport == "nfl":
                self._check_nfl()
            elif sport == "mlb":
                self._check_mlb()
            return
        if profile_name == "Watchalong Replay":
            return  # no proactive callouts in replay mode, for any sport
        if profile_name == "COVAS":
            if (time.time() - self._last_ed_poll) < self.ED_POLL_INTERVAL_S:
                return
            self._last_ed_poll = time.time()
            self._check_ed()
            return
        if profile_name != "RaceEngineer":
            return
        if self._frequency == "off":
            return

        from tools.telemetry_source import active_source
        source = active_source()
        if source is None:
            return

        alerts_cfg = config.get("race_engineer.alerts", {}) or {}
        if source == "forza":
            self._check_forza(alerts_cfg)
        else:
            self._check_ac(alerts_cfg)

    def _cooldown_ok(self, key: str, table: dict, frequency: str) -> bool:
        cooldowns = table.get(frequency, table.get("normal", {}))
        cooldown_s = cooldowns.get(key, 60)
        return (time.time() - self._last_alert_at.get(key, 0)) >= cooldown_s

    def _speak(self, cooldown_key: str, text: str, table: dict = None, frequency: str = None):
        table = table if table is not None else self.COOLDOWNS
        frequency = frequency if frequency is not None else self._frequency
        if not self._cooldown_ok(cooldown_key, table, frequency):
            return

        # Don't talk over Q2 mid-conversation — check the same face_state
        # AudioIO.play() itself updates, so a callout never collides with a
        # normal turn's speech, listening window, or thinking pause.
        try:
            from face.server import face_state
            snap = face_state.snapshot()
            if snap["speaking"] or snap["listening"] or snap["thinking"]:
                return
        except ImportError:
            pass

        self._last_alert_at[cooldown_key] = time.time()
        try:
            from voice.pipeline import get_tts, AudioIO
            speech = get_tts().synthesize(text)
            AudioIO().play(speech)
            log.info(f"[race_engineer_alerts] ({cooldown_key}) {text}")
        except Exception as e:
            log.warning(f"[race_engineer_alerts] TTS/playback failed: {e}")

    @classmethod
    def _corners(cls, flags) -> str:
        return ", ".join(name for name, on in zip(cls._CORNER_NAMES, flags) if on)

    @classmethod
    def _damage_area(cls, damage) -> str:
        return cls._DAMAGE_AREA_NAMES[damage.index(max(damage))]

    def _check_forza(self, alerts_cfg: dict):
        from integrations.forza_telemetry import get_snapshot
        d = get_snapshot()
        if not d or not d.get("is_race_on"):
            return

        from integrations.forza_telemetry import get_listener
        events = get_listener().pop_events()

        # Mode transitions (race just started/ended) take priority over
        # whatever the current mode's regular handler would otherwise say.
        race_cfg = config.get("integrations.forza_telemetry.race", {}) or {}
        for event in events:
            if event.get("type") == "race_started":
                if race_cfg.get("announce_start", True):
                    msg = self._format_race_start(event.get("position", 0))
                    if msg:
                        self._speak("race_transition", msg, table=self.OPENWORLD_COOLDOWNS, frequency=self._frequency)
                return
            if event.get("type") == "race_ended":
                self._speak("race_transition", "Race over. Good run.",
                            table=self.OPENWORLD_COOLDOWNS, frequency=self._frequency)
                return

        # Automatic mode routing -- no manual switch needed. "menu"
        # (paused/in a menu, IsRaceOn=0) does nothing.
        mode = d.get("driving_mode", "freeroam")
        if mode == "race":
            self._check_forza_race_live(d, events, alerts_cfg)
        elif mode == "freeroam":
            self._check_forza_openworld(d, events)

    def _format_race_start(self, position: int) -> str:
        responses = {
            1: "You're on pole. Keep them behind you.",
            2: "Starting P2. The lead is right there.",
            3: "P3 on the grid. Podium fight.",
        }
        if position in responses:
            return responses[position]
        elif position > 0:
            return f"Starting P{position}. Let's go."
        return "Race starting."

    def _check_forza_race_live(self, snap: dict, events: list, alerts_cfg: dict):
        """FH6 race-mode callouts: position and position changes are the
        story here (Forza's wire format has no gap-to-leader data -- see
        RaceStateTracker's docstring), gear/fuel/tyres take a back seat
        except when genuinely critical."""
        race_cfg = config.get("integrations.forza_telemetry.race", {}) or {}
        summary = snap.get("race_summary")
        if not summary:
            return

        for event in events:
            etype = event.get("type")
            if etype == "overtake" and not race_cfg.get("announce_overtakes", True):
                continue
            if etype == "position_lost" and not race_cfg.get("announce_position_lost", True):
                continue
            msg = self._format_race_event(event)
            if msg:
                self._speak("race_position", msg, table=self.OPENWORLD_COOLDOWNS, frequency=self._frequency)
                return

        interval = race_cfg.get("position_update_interval", 30)
        now = time.time()
        if now - self._last_race_update > interval:
            self._last_race_update = now
            msg = self._build_race_status(summary)
            if msg:
                self._speak("race_status", msg, table=self.OPENWORLD_COOLDOWNS, frequency=self._frequency)

        fuel_threshold = race_cfg.get("fuel_warning_threshold", 0.1)
        if snap.get("fuel", 1.0) < fuel_threshold:
            self._speak("fuel", "Fuel critical.")

        if alerts_cfg.get("tyre_temp", True) and not race_cfg.get("suppress_tyre_alerts", False):
            tt = (snap["tire_temp_fl"], snap["tire_temp_fr"], snap["tire_temp_rl"], snap["tire_temp_rr"])
            cold = [t < 60 for t in tt]
            hot = [t > 110 for t in tt]
            if any(cold):
                self._speak("tyres", f"Tyres cold -- {self._corners(cold)}.")
            elif any(hot):
                self._speak("tyres", f"Tyres overheating -- {self._corners(hot)}.")

        # Forza's wire format has no per-corner wear, flags, damage, pit-window,
        # or gap-ahead data — tyre_wear/flags/pit_window/gap/damage alerts
        # simply never fire while Forza is the active source.

    def _format_race_event(self, event: dict) -> Optional[str]:
        etype = event.get("type")

        if etype == "overtake":
            to_pos = event.get("to_pos", 0)
            if event.get("is_lead"):
                return random.choice(["You're in the lead!", "P1! You've got the lead.", "Leading the race."])
            return random.choice([f"Up to P{to_pos}.", f"P{to_pos}. Good move.", f"Moved up to P{to_pos}."])

        if etype == "position_lost":
            to_pos = event.get("to_pos", 0)
            return random.choice([f"Down to P{to_pos}.", f"P{to_pos} now.", f"Lost a position -- P{to_pos}."])

        return None

    def _build_race_status(self, summary: dict) -> Optional[str]:
        """Concise periodic status, called every race.position_update_interval
        seconds. No lap total/remaining claim -- see RaceStateTracker's
        docstring for why FH6 can't honestly support one."""
        pos = summary.get("position", 0)
        lap = summary.get("lap", 0)
        last_lap = summary.get("last_lap_time", "--:--.---")
        gained = summary.get("positions_gained", 0)
        lost = summary.get("positions_lost", 0)

        parts = []
        if pos == 1:
            parts.append("Leading")
        elif pos > 0:
            parts.append(f"P{pos}")

        if lap > 0:
            parts.append(f"lap {lap}")

        if last_lap != "--:--.---":
            parts.append(f"last lap {last_lap}")

        if gained > 0:
            parts.append(f"up {gained} from start")
        elif lost > 0:
            parts.append(f"down {lost} from start")

        if not parts:
            return None
        return ". ".join(parts) + "."

    def _check_forza_openworld(self, d: dict, events: list):
        """Excited-passenger commentary for Forza free roam: drifts, jumps,
        speed milestones, near-spins, and known-landmark arrivals. Gated by
        integrations.forza_telemetry.openworld config, independent of the
        race_engineer.alerts toggles (those are race-only)."""
        ow_cfg = config.get("integrations.forza_telemetry.openworld", {}) or {}
        if not ow_cfg.get("enabled", True):
            return

        speed_kmh = d.get("speed", 0) * 3.6

        if ow_cfg.get("location_announcements", True):
            from integrations.forza_location import get_location_system
            loc = get_location_system()
            location_msg = loc.check_location(d.get("pos_x", 0), d.get("pos_z", 0), speed_kmh)
            if location_msg:
                self._speak("location", location_msg, table=self.OPENWORLD_COOLDOWNS, frequency=self._frequency)
                return

        min_score = ow_cfg.get("min_drift_score", "nice")
        score_rank = {"mild": 0, "nice": 1, "clean": 2, "insane": 3}

        for event in events:
            etype = event.get("type")
            if etype == "drift_end" and not ow_cfg.get("drift_commentary", True):
                continue
            if etype == "drift_end" and score_rank.get(event.get("score", "mild"), 0) < score_rank.get(min_score, 1):
                continue
            if etype == "speed_milestone" and not ow_cfg.get("speed_milestones", True):
                continue

            msg = self._format_openworld_event(event)
            if not msg:
                continue
            cooldown_key = {"drift_end": "drift", "landing": "air", "speed_milestone": "speed",
                             "near_spin": "near_spin"}.get(etype, "drift")
            self._speak(cooldown_key, msg, table=self.OPENWORLD_COOLDOWNS, frequency=self._frequency)
            break  # one event per poll tick

    def _format_openworld_event(self, event: dict) -> Optional[str]:
        etype = event.get("type")

        if etype == "drift_end":
            score = event.get("score", "mild")
            duration = event.get("duration", 0)
            angle = event.get("angle", 0)
            responses = {
                "mild": [
                    "Nice little slide.", "Clean.", f"That was smooth -- {duration:.1f} seconds.",
                ],
                "nice": [
                    f"Good drift, {angle:.0f} degrees.", f"Yeah! {duration:.1f} seconds sideways.",
                    "That's what I'm talking about.",
                ],
                "clean": [
                    f"Clean drift -- {duration:.1f} seconds, {angle:.0f} degrees.", "Absolutely clean.",
                    f"That's a proper drift -- {duration:.1f} seconds.",
                ],
                "insane": [
                    f"WHAT. {duration:.1f} seconds sideways at {angle:.0f} degrees.",
                    "That was absolutely mental.",
                    f"Bro. {angle:.0f} degrees. {duration:.1f} seconds. Insane.",
                ],
            }
            return random.choice(responses.get(score, responses["nice"]))

        if etype == "landing":
            airtime = event.get("airtime", 0)
            speed_kmh = event.get("speed_kmh", 0)
            if airtime > 1.0:
                return f"{airtime:.1f} seconds of air at {speed_kmh:.0f} km/h."
            elif airtime > 0.5:
                return "Good jump."
            return None

        if etype == "speed_milestone":
            speed = event.get("speed_kmh", 0)
            responses = {
                200: ["200 km/h.", "Two hundred.", "We're moving."],
                250: ["250 km/h. Proper speed now.", "Two fifty.", "That's quick."],
                300: ["300 km/h. Okay.", "Three hundred kilometres per hour.", "This thing is fast."],
                350: ["Three fifty. WHAT.", "350 km/h. This is insane.", "We should not be going this fast."],
            }
            return random.choice(responses.get(speed, [f"{speed} km/h."]))

        if etype == "near_spin":
            return random.choice(["Ooh. Close.", "That was almost a spin.", "Nice save.", "Held it together."])

        return None

    def _check_ac(self, alerts_cfg: dict):
        from integrations.ac_telemetry import get_snapshot, AC_FLAG
        d = get_snapshot()
        if not d or d.get("status") != 2:  # 2 == LIVE; skip OFF/REPLAY/PAUSE
            return

        if alerts_cfg.get("fuel", True):
            laps_left = d.get("fuel_estimated_laps", 0)
            if 0 < laps_left < 2:
                self._speak("fuel", f"Fuel critical -- {laps_left:.1f} laps left. Box.")
            elif 0 < laps_left < 4:
                self._speak("fuel", f"Fuel -- {laps_left:.1f} laps remaining.")

        if alerts_cfg.get("tyre_temp", True):
            tt = (d["tyre_temp_fl"], d["tyre_temp_fr"], d["tyre_temp_rl"], d["tyre_temp_rr"])
            cold = [t < 60 for t in tt]
            hot = [t > 110 for t in tt]
            if any(cold):
                self._speak("tyres", f"Tyres cold -- {self._corners(cold)}.")
            elif any(hot):
                self._speak("tyres", f"Tyres overheating -- {self._corners(hot)}.")

        if alerts_cfg.get("tyre_wear", True):
            # Wire format's tyre_wear is percent-tread-remaining (100=new,
            # 0=gone — see windows/ac_bridge.py's build_packet()), so
            # "critical" is the low end.
            tw = (d["tyre_wear_fl"], d["tyre_wear_fr"], d["tyre_wear_rl"], d["tyre_wear_rr"])
            critical = [w < 15 for w in tw]
            if any(critical):
                self._speak("tyres", f"Tyre wear critical -- {self._corners(critical)}.")

        if alerts_cfg.get("flags", True):
            flag = AC_FLAG.get(d.get("flag", 0), "None")
            if flag == "Blue":
                self._speak("flag", "Blue flag. Let him past.")
            elif flag == "Black":
                self._speak("flag", "Black flag. You're disqualified.")

        if alerts_cfg.get("damage", True):
            damage = (d["damage_front"], d["damage_rear"], d["damage_left"], d["damage_right"], d["damage_centre"])
            if max(damage) > 0:
                self._speak("damage", f"Damage warning -- {self._damage_area(damage)}.")

        if alerts_cfg.get("pit_window", True):
            missing = d.get("missing_mandatory_pits", 0)
            mandatory_done = d.get("mandatory_pit_done", 0)
            pit_end_min = d.get("pit_window_end", 0)
            clock_s = d.get("session_clock", 0)
            if missing > 0 and not mandatory_done and pit_end_min > 0:
                remaining_s = (pit_end_min * 60) - clock_s
                if 0 < remaining_s < 120:
                    self._speak("pit", "Box this lap -- pit window closing.")

        # Gap-to-car-ahead isn't available anywhere in AC/ACC's shared memory
        # (only via ACC's separate Broadcasting SDK) — the "gap" toggle exists
        # in settings for forward-compat but has no data source yet, so it
        # never fires.

    def _check_msfs(self):
        """
        Inline per-category MSFS checks, mirroring _check_forza/_check_ac's
        shape rather than calling tools/first_officer.py's
        first_officer_status() (which returns one combined string under a
        single cooldown — too coarse now that each category has its own
        first_officer.alerts.<key> toggle and FO_COOLDOWNS entry).
        first_officer_status() stays the holistic, on-demand version for
        when the user actually asks "how's the flight going".
        """
        from integrations.msfs_telemetry import get_snapshot, is_active
        if not is_active():
            return
        d = get_snapshot()
        if not d:
            return

        fo = lambda key, text: self._speak(key, text, table=self.FO_COOLDOWNS, frequency=self._fo_frequency)

        announce_phase = config.get("first_officer.announce_phase", True)
        alerts_cfg = config.get("first_officer.alerts", {}) or {}
        min_alt_agl = config.get("first_officer.min_alt_agl", 50)
        aircraft_type = config.get("first_officer.aircraft_type", "auto")

        # Flight-phase transitions announce immediately (no cooldown — the
        # phase itself only changes a handful of times per flight, and
        # _last_flight_phase already prevents repeats). Skipped on the very
        # first read so startup mid-flight doesn't narrate the current phase
        # as if it just changed. _last_flight_phase still updates even with
        # announce_phase off, so re-enabling it mid-flight doesn't fire a
        # stale transition.
        phase = d.get("flight_phase")
        if phase and phase != self._last_flight_phase:
            if announce_phase and self._last_flight_phase is not None:
                fo("phase", f"{phase}.")
            self._last_flight_phase = phase

        agl = d.get("alt_agl_ft", 0.0)
        ias = d.get("airspeed_ind_kt", 0.0)
        bank = abs(d.get("bank_deg", 0.0))

        # Altitude alerting — approaching AP altitude target
        if alerts_cfg.get("altitude", True) and d.get("ap_master") and d.get("ap_alt_lock"):
            target = d.get("ap_alt_var_ft", 0.0)
            current = d.get("altitude_ft", 0.0)
            remaining = abs(target - current)
            if 0 < remaining <= 1000:
                fo("altitude", f"{remaining:.0f} to level off.")

        # Gear check on approach
        if alerts_cfg.get("gear", True) and ias < 150 and agl < 3000 and not d.get("gear_down") and not d.get("on_ground"):
            fo("gear", "Gear check.")

        # Fuel — critical/low two-tier, same shape as _check_ac's fuel alert
        if alerts_cfg.get("fuel", True):
            fuel_gal = d.get("fuel_qty_gal", 0.0)
            cap_gal = d.get("fuel_capacity_gal", 0.0)
            if cap_gal > 0 and fuel_gal / cap_gal < 0.10:
                fo("fuel", f"Fuel critical -- {fuel_gal:.0f} gallons.")
            elif cap_gal > 0 and fuel_gal / cap_gal < 0.25:
                flow = d.get("eng1_fuel_flow_gph", 0.0) + d.get("eng2_fuel_flow_gph", 0.0)
                hrs = (fuel_gal / flow) if flow > 0.1 else 0.0
                fo("fuel", f"Fuel low -- {fuel_gal:.0f} gallons, {hrs:.1f} hours.")

        # Bank angle
        if alerts_cfg.get("bank", True) and bank > 30:
            fo("bank", "Bank angle.")

        # Autopilot disconnect (only worth calling out once airborne and configured to fly itself)
        if alerts_cfg.get("autopilot", True) and phase not in ("PARKED", "TAXI") and not d.get("ap_master"):
            fo("autopilot", "Autopilot disconnected.")

        # Engine failure — N1% for jets/turboprops, RPM for piston GA;
        # "auto" picks whichever reading is actually nonzero. Engine 2 is
        # only checked with some N1 reading on it so single-engine
        # aircraft's unused engine-2 fields don't false-alarm.
        if alerts_cfg.get("engine", True) and phase not in ("PARKED", "TAXI"):
            uses_n1 = aircraft_type in ("airliner", "turboprop") or (
                aircraft_type == "auto" and d.get("eng1_n1_pct", 0.0) > 0
            )
            if not d.get("eng1_combustion"):
                metric = f"N1 {d.get('eng1_n1_pct', 0.0):.0f}%" if uses_n1 else f"RPM {d.get('eng1_rpm', 0.0):.0f}"
                fo("engine", f"Engine 1 not running -- {metric}.")
            elif d.get("eng2_n1_pct", 0.0) > 0 and not d.get("eng2_combustion"):
                fo("engine", "Engine 2 not running.")

        # Approaching waypoint
        wp_dist = d.get("gps_wp_distance_nm", 0.0)
        wp_name = d.get("gps_wp_ident")
        if alerts_cfg.get("waypoint", True) and wp_name and 0 < wp_dist < 2.0:
            fo("waypoint", f"Waypoint {wp_name} in {wp_dist:.1f}nm.")

        # Approach checklist reminder — suppressed below min_alt_agl so it
        # doesn't nag during the ground roll/taxi after landing.
        if alerts_cfg.get("approach", True) and phase == "APPROACH" and agl > min_alt_agl:
            gear_state = "down" if d.get("gear_down") else "up"
            flaps_pct = d.get("flaps_pct", 0.0) * 100
            wp = wp_name or "field"
            fo("approach", f"Approaching {wp}, gear {gear_state}, flaps {flaps_pct:.0f}%.")

        # Weather advisories. MSFS's wire format only carries wind and
        # visibility, not a turbulence simvar, so that's what this covers.
        if alerts_cfg.get("weather", True):
            wind_kt = d.get("wind_speed_kt", 0.0)
            vis_m = d.get("visibility_m", 9999.0)
            if wind_kt > 25:
                fo("weather", f"Strong wind -- {wind_kt:.0f}kts from {d.get('wind_dir_deg', 0.0):.0f}.")
            elif 0 < vis_m < 4800:  # ~3 statute miles
                fo("weather", f"Visibility {vis_m / 1609:.1f} miles.")

    def _speak_immediate(self, text: str):
        """
        Like _speak(), but with no cooldown-table bookkeeping — used for ED
        Ship Computer alerts, which are already deduplicated at the source
        (ed_alert() only returns text for genuinely-changed conditions, and
        arrival announcements are gated on _last_ed_system actually changing).
        Still respects face_state so it never talks over an in-progress turn.
        """
        try:
            from face.server import face_state
            snap = face_state.snapshot()
            if snap["speaking"] or snap["listening"] or snap["thinking"]:
                return
        except ImportError:
            pass
        try:
            from voice.pipeline import get_tts, AudioIO
            speech = get_tts().synthesize(text)
            AudioIO().play(speech)
            log.info(f"[ed_alerts] {text}")
        except Exception as e:
            log.warning(f"[ed_alerts] TTS/playback failed: {e}")

    def _check_ed(self):
        """
        Elite Dangerous Ship Computer proactive callouts — fuel/hull/
        interdiction/shield alerts via tools.ship_computer.ed_alert(), plus a
        one-line arrival announcement whenever the current system changes.
        """
        from integrations.ed_telemetry import is_active, get_snapshot
        if not is_active():
            return

        from tools.ship_computer import ed_alert
        text = ed_alert()
        if text:
            self._speak_immediate(text)

        state = get_snapshot()
        system = state["location"].get("system")
        if system and system != self._last_ed_system:
            if self._last_ed_system is not None:
                note = f"Arrived in {system}."
                try:
                    from integrations.ed_edsm import get_system
                    info = get_system(system)
                    if info.get("ok"):
                        sys_info = info["data"].get("information", {}) or {}
                        if sys_info.get("economy"):
                            note += f" {sys_info['economy']} economy."
                        if sys_info.get("security"):
                            note += f" {sys_info['security']} security."
                except Exception:
                    pass
                self._speak_immediate(note)
            self._last_ed_system = system

    def _check_f1(self):
        """
        Proactive F1 Watchalong callouts — polls integrations.f1_watchalong's
        LiveRaceWatcher for new events (race control messages, leader changes,
        fastest laps, notable pit stops) and speaks whichever ones the current
        watchalong.live.frequency tier allows (see F1_FREQUENCY_TIERS).
        Throttled to F1_POLL_INTERVAL_S independently of the thread's 3s
        tick, since OpenF1 is a shared remote API rather than local UDP
        telemetry. Only called from _tick() when profile_name == "Watchalong
        Live" and watchalong.active_sport == "f1" — never runs in Watchalong
        Replay, matching that mode's "no proactive callouts" requirement.
        """
        now = time.time()
        if (now - self._last_f1_poll) < self.F1_POLL_INTERVAL_S:
            return
        self._last_f1_poll = now

        from integrations.f1_watchalong import detect_live_session, get_watcher
        session = detect_live_session()
        if not session or not session.get("is_live"):
            return

        tier_categories = self.F1_FREQUENCY_TIERS.get(self._wa_frequency, set())
        if not tier_categories:
            return

        events = get_watcher().check_new_events(session["session_key"])
        for category, text in events:
            if category not in tier_categories:
                continue
            self._speak(f"f1_{category}", text, table=self.F1_COOLDOWNS, frequency=self._wa_frequency)

    def _check_ufc(self):
        """
        Proactive UFC Watchalong callouts — polls ESPN's scoreboard every
        UFC_POLL_INTERVAL_S (30s per spec) for tonight's event and speaks
        whichever categories the current watchalong.live.frequency tier
        allows (see UFC_FREQUENCY_TIERS). No live strike-by-strike data
        exists (see integrations/ufc_data.py's docstring), so this only
        ever announces things ESPN's public scoreboard actually reports:
        fight results (winner + round/time, never method), the main event
        starting, and — chatty only — a lightweight "Round N" transition
        signal (not a round-winner call, since there's no data to judge
        that from). Only called from _tick() when profile_name ==
        "Watchalong Live" and watchalong.active_sport == "ufc".
        """
        now = time.time()
        if (now - self._last_ufc_poll) < self.UFC_POLL_INTERVAL_S:
            return
        self._last_ufc_poll = now

        from integrations.ufc_data import get_tonight_event
        event = get_tonight_event()
        if not event:
            return

        if event["event_id"] != self._ufc_event_id:
            self._ufc_event_id = event["event_id"]
            self._ufc_seen_results = set()
            self._ufc_last_round = {}
            self._ufc_main_event_live = False

        tier_categories = self.UFC_FREQUENCY_TIERS.get(self._wa_frequency, set())
        if not tier_categories:
            return

        ufc = lambda key, text: self._speak(key, text, table=self.UFC_COOLDOWNS, frequency=self._wa_frequency)

        for fight in event["fights"]:
            pos = fight["card_position_from_top"]

            if fight["is_main_event"] and fight["live"] and not self._ufc_main_event_live:
                self._ufc_main_event_live = True
                if "main_event_starting" in tier_categories:
                    names = " vs ".join(f["name"] for f in fight["fighters"])
                    ufc("ufc_main_event_starting", f"Main event time -- {names}.")

            if fight["completed"] and pos not in self._ufc_seen_results:
                self._ufc_seen_results.add(pos)
                result = fight.get("result")
                if not result or not result.get("winner"):
                    continue
                is_finish = bool(
                    fight.get("scheduled_rounds") and result.get("ended_round")
                    and result["ended_round"] < fight["scheduled_rounds"]
                )
                categories_hit = {"fight_result"}
                if fight["is_main_event"]:
                    categories_hit.add("main_event_result")
                if is_finish:
                    categories_hit.add("finish")
                if categories_hit & tier_categories:
                    names_note = f" in round {result['ended_round']}" if result.get("ended_round") else ""
                    text = f"{result['winner']} defeats {result['loser']}{names_note}. {result['winner']} moves to {result['winner_record']}."
                    ufc(f"ufc_result_{pos}", text)
                continue

            if fight["live"] and "round_transition" in tier_categories:
                result = fight.get("result") or {}
                current_round = result.get("ended_round")  # same ESPN field carries the in-progress round while live
                if current_round and self._ufc_last_round.get(pos) != current_round:
                    self._ufc_last_round[pos] = current_round
                    names = " vs ".join(f["name"] for f in fight["fighters"])
                    ufc(f"ufc_round_{pos}", f"Round {current_round} -- {names}.")

    def _check_nba(self):
        """
        Proactive NBA Watchalong callouts — polls integrations.nba_data's
        NBAWatchalong every NBA_POLL_INTERVAL_S for score/period/final
        events and speaks whichever categories the current
        watchalong.live.frequency tier allows (see NBA_FREQUENCY_TIERS).
        Only called from _tick() when profile_name == "Watchalong Live"
        and watchalong.active_sport == "nba".
        """
        now = time.time()
        if (now - self._last_nba_poll) < self.NBA_POLL_INTERVAL_S:
            return
        self._last_nba_poll = now

        from integrations.nba_data import get_nba
        nba = get_nba()
        if not nba._game_id:
            game = nba.detect_live_game()
            if not game:
                return
            nba.set_game(game.get("id"))

        tier_categories = self.NBA_FREQUENCY_TIERS.get(self._wa_frequency, set())
        if not tier_categories:
            return

        for event in nba.poll():
            etype = event.get("type")
            if etype == "final":
                category, text = "final", self._format_nba_event(event)
            elif etype == "period_start":
                category, text = "period_start", self._format_nba_event(event)
            elif etype == "score":
                category, text = self._classify_nba_score(event), self._format_nba_event(event)
            else:
                continue

            if category not in tier_categories or not text:
                continue
            self._speak(f"nba_{category}", text, table=self.NBA_COOLDOWNS, frequency=self._wa_frequency)

    @staticmethod
    def _classify_nba_score(event: dict) -> str:
        """A 'score' event only becomes worth its own announcement if it's
        a 3-pointer or it just changed who's leading — otherwise it's a
        plain basket, which is never in any tier's category set (see
        NBA_FREQUENCY_TIERS' comment). Lead-before is reconstructed from
        the event's own home/away score minus the points just scored,
        since integrations/nba_data.py's poll() doesn't track this itself.
        """
        pts = event.get("points", 0)
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        team_is_home = event.get("team") == event.get("home_team")
        prior_home = hs - pts if team_is_home else hs
        prior_away = as_ - pts if not team_is_home else as_
        leader_before = "home" if prior_home > prior_away else "away" if prior_away > prior_home else "tied"
        leader_after = "home" if hs > as_ else "away" if as_ > hs else "tied"
        if leader_after != leader_before:
            return "lead_change"
        if pts == 3:
            return "three_pointer"
        return "score"

    @staticmethod
    def _format_nba_event(event: dict) -> str:
        """Mirrors tools/nba_analyst.py's _format_nba_event -- kept
        separate rather than imported, same layering as F1/UFC above
        (this proactive-alert path only ever imports integrations.*, never
        tools.*_analyst, which are the LLM-facing on-demand wrappers)."""
        etype = event.get("type")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")

        if etype == "score":
            pts, team = event.get("points", 2), event.get("team", "")
            score = f"{ht} {hs} - {at} {as_}"
            if pts == 3:
                return random.choice([f"Three! {team}. {score}.", f"{team} from downtown! {score}."])
            return f"{team} scores. {score}."

        if etype == "period_start":
            period = event.get("period", 0)
            p_str = {2: "Second quarter", 3: "Third quarter", 4: "Fourth quarter"}.get(period, f"Quarter {period}")
            return f"{p_str} underway. {ht} {hs}, {at} {as_}."

        if etype == "final":
            winner = event.get("winner", "")
            return f"Final: {ht} {hs}, {at} {as_}. {winner} win."

        return ""

    def _check_nhl(self):
        """
        Proactive NHL Watchalong callouts — polls integrations.nhl_data's
        NHLWatchalong every NHL_POLL_INTERVAL_S for goal/period/final
        events. Goals are rare in hockey so they're in every non-silent
        tier (see NHL_FREQUENCY_TIERS). Only called from _tick() when
        profile_name == "Watchalong Live" and watchalong.active_sport == "nhl".
        """
        now = time.time()
        if (now - self._last_nhl_poll) < self.NHL_POLL_INTERVAL_S:
            return
        self._last_nhl_poll = now

        from integrations.nhl_data import get_nhl
        nhl = get_nhl()
        if not nhl._game_id:
            game = nhl.detect_live_game()
            if not game:
                return
            nhl.set_game(game.get("id"))

        tier_categories = self.NHL_FREQUENCY_TIERS.get(self._wa_frequency, set())
        if not tier_categories:
            return

        for event in nhl.poll():
            category = event.get("type")
            if category not in tier_categories:
                continue
            text = self._format_nhl_event(event)
            if not text:
                continue
            self._speak(f"nhl_{category}", text, table=self.NHL_COOLDOWNS, frequency=self._wa_frequency)

    @staticmethod
    def _format_nhl_event(event: dict) -> str:
        """Mirrors tools/nhl_analyst.py's _format_nhl_event -- see
        _format_nba_event's docstring above for why this is duplicated
        rather than imported."""
        etype = event.get("type")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")

        if etype == "goal":
            team = event.get("team", "")
            ot_str = " OT" if event.get("period_type") == "OT" else ""
            return random.choice([f"GOAL! {team}. {ht} {hs} - {at} {as_}.",
                                   f"{team} scores! {ht} {hs} - {at} {as_}."]) + ot_str

        if etype == "period_start":
            period, ptype = event.get("period", 0), event.get("period_type", "REG")
            p_str = {1: "First period", 2: "Second period", 3: "Third period"}.get(
                period, "Overtime" if ptype == "OT" else "Shootout")
            return f"{p_str} underway. {ht} {hs}, {at} {as_}."

        if etype == "final":
            winner = event.get("winner", "")
            suffix = " in the shootout." if event.get("shootout") else " in overtime." if event.get("overtime") else "."
            return f"Final: {ht} {hs}, {at} {as_}. {winner} win{suffix}"

        return ""

    def _check_nfl(self):
        """
        Proactive NFL Watchalong callouts — polls integrations.nfl_data's
        NFLWatchalong every NFL_POLL_INTERVAL_S for score/period/final
        events and speaks whichever categories the current
        watchalong.live.frequency tier allows (see NFL_FREQUENCY_TIERS).
        Only called from _tick() when profile_name == "Watchalong Live"
        and watchalong.active_sport == "nfl".
        """
        now = time.time()
        if (now - self._last_nfl_poll) < self.NFL_POLL_INTERVAL_S:
            return
        self._last_nfl_poll = now

        from integrations.nfl_data import get_nfl
        nfl = get_nfl()
        if not nfl._game_id:
            game = nfl.detect_live_game()
            if not game:
                return
            nfl.set_game(game.get("id"))

        tier_categories = self.NFL_FREQUENCY_TIERS.get(self._wa_frequency, set())
        if not tier_categories:
            return

        for event in nfl.poll():
            etype = event.get("type")
            if etype == "final":
                category, text = "final", self._format_nfl_event(event)
            elif etype == "period_start":
                category, text = "period_start", self._format_nfl_event(event)
            elif etype == "score":
                category, text = self._classify_nfl_score(event), self._format_nfl_event(event)
            else:
                continue

            if category not in tier_categories or not text:
                continue
            self._speak(f"nfl_{category}", text, table=self.NFL_COOLDOWNS, frequency=self._wa_frequency)

    @staticmethod
    def _classify_nfl_score(event: dict) -> str:
        """touchdown (6/7/8 pts) / field_goal (3) / safety (2) — the
        1-point extra-point-only delta (when ESPN posts a TD and its PAT
        as two separate score bumps) folds into 'touchdown' too, since
        it's the tail end of the same scoring drive, not its own event."""
        pts = event.get("points", 0)
        if pts in (6, 7, 8, 1):
            return "touchdown"
        if pts == 3:
            return "field_goal"
        if pts == 2:
            return "safety"
        return "score"

    @staticmethod
    def _format_nfl_event(event: dict) -> str:
        """Mirrors tools/nfl_analyst.py's _format_nfl_event -- see
        _format_nba_event's docstring above for why this is duplicated
        rather than imported."""
        etype = event.get("type")
        hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
        ht, at = event.get("home_team", ""), event.get("away_team", "")

        if etype == "score":
            team, stype = event.get("team", ""), event.get("score_type", "")
            score = f"{ht} {hs} - {at} {as_}"
            if "touchdown" in stype:
                return random.choice([f"TOUCHDOWN! {team}! {score}.", f"{team} scores! Touchdown. {score}."])
            if "field goal" in stype:
                return random.choice([f"Field goal -- {team}. {score}.", f"{team} kicks a field goal. {score}."])
            if "safety" in stype:
                return f"Safety! {team} scores 2. {score}."
            return f"{team} scores. {score}."

        if etype == "period_start":
            period = event.get("period", 0)
            p_str = {2: "Second quarter", 3: "Third quarter", 4: "Fourth quarter"}.get(period, f"Quarter {period}")
            return f"{p_str} underway. {ht} {hs}, {at} {as_}."

        if etype == "final":
            winner, ot = event.get("winner", ""), event.get("ot", False)
            suffix = " in overtime." if ot else "."
            return f"Final: {ht} {hs}, {at} {as_}. {winner} win{suffix}"

        return ""

    def _check_mlb(self):
        """
        Proactive MLB Watchalong callouts — polls integrations.mlb_data's
        MLBWatchalong every MLB_POLL_INTERVAL_S for play/inning/final
        events and speaks whichever categories the current
        watchalong.live.frequency tier allows (see MLB_FREQUENCY_TIERS).
        Only called from _tick() when profile_name == "Watchalong Live"
        and watchalong.active_sport == "mlb".
        """
        now = time.time()
        if (now - self._last_mlb_poll) < self.MLB_POLL_INTERVAL_S:
            return
        self._last_mlb_poll = now

        from integrations.mlb_data import get_mlb
        mlb = get_mlb()
        if not mlb._game_pk:
            game = mlb.detect_live_game()
            if not game:
                return
            mlb.set_game(game.get("gamePk"))

        tier_categories = self.MLB_FREQUENCY_TIERS.get(self._wa_frequency, set())
        if not tier_categories:
            return

        for event in mlb.poll():
            etype = event.get("type")
            if etype == "final":
                category = "final"
            elif etype == "inning_change":
                category = "inning_change"
            elif etype == "play":
                category = self._classify_mlb_play(event)
                if not category:
                    continue
            else:
                continue

            if category not in tier_categories:
                continue
            # Even within the chatty tier, only announce a slice of
            # strikeouts -- they happen every few minutes and would
            # otherwise dominate the commentary (see tools/mlb_analyst.py's
            # docstring note on the same 25% throttle for the on-demand
            # mlb_game_alert tool).
            if category == "strikeout" and random.random() >= 0.25:
                continue
            text = self._format_mlb_event(event)
            if not text:
                continue
            self._speak(f"mlb_{category}", text, table=self.MLB_COOLDOWNS, frequency=self._wa_frequency)

    @staticmethod
    def _classify_mlb_play(event: dict) -> str:
        etype = event.get("event_type", "")
        if etype == "home_run":
            return "home_run"
        if etype == "triple_play":
            return "triple_play"
        if etype in ("double_play", "grounded_into_double_play", "lined_into_double_play"):
            return "double_play"
        if etype == "strikeout":
            return "strikeout"
        if event.get("is_scoring"):
            return "scoring_play"
        return ""

    @staticmethod
    def _format_mlb_event(event: dict) -> str:
        """Mirrors tools/mlb_analyst.py's _format_mlb_event -- see
        _format_nba_event's docstring above for why this is duplicated
        rather than imported."""
        etype = event.get("type")

        if etype == "play":
            ev_type = event.get("event_type", "")
            hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
            ht, at = event.get("home_team", ""), event.get("away_team", "")
            score = f"{ht} {hs} - {at} {as_}"

            if ev_type == "home_run":
                return random.choice([f"HOME RUN! {score}.", f"Gone! Home run. {score}.", f"That ball is out of here. {score}."])
            if ev_type == "triple_play":
                return "TRIPLE PLAY! Three out on one play."
            if ev_type in ("double_play", "grounded_into_double_play", "lined_into_double_play"):
                return "Double play. Two away."
            if ev_type == "strikeout":
                return "Strikeout."
            if event.get("is_scoring"):
                return f"Scores. {score}."
            return ""

        if etype == "inning_change":
            inning, half = event.get("inning", 0), event.get("half", "")
            hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
            ht, at = event.get("home_team", ""), event.get("away_team", "")
            ords = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th", 9: "9th"}
            inn_s = f"{'Bottom' if half.lower() == 'bottom' else 'Top'} {ords.get(inning, str(inning))}"
            return f"{inn_s}. {ht} {hs}, {at} {as_}."

        if etype == "final":
            winner = event.get("winner", "")
            hs, as_ = event.get("home_score", 0), event.get("away_score", 0)
            ht, at, innings = event.get("home_team", ""), event.get("away_team", ""), event.get("innings", 9)
            extra = f" in {innings} innings" if innings > 9 else ""
            return f"Final{extra}: {ht} {hs}, {at} {as_}. {winner} win."

        return ""


class WhiplashCloneHeroThread(threading.Thread):
    """
    Polls a Clone Hero now-playing text file on a fixed interval (see
    integrations/clone_hero.py) and, on every detected song change,
    speaks a Fletcher-voiced quip via TTS directly in-process -- same
    face_state busy-check + get_tts()/AudioIO() idiom as
    RaceEngineerAlertThread._speak(), reimplemented here rather than
    shared since this thread has no cooldown table or frequency dial,
    just "new song -> one quip."
    """

    def __init__(self, songfile_path: str, poll_interval_s: float = 0.5):
        super().__init__(daemon=True, name="WhiplashCloneHero")
        from integrations.clone_hero import get_watcher
        self._watcher = get_watcher(songfile_path)
        self._poll_interval_s = poll_interval_s
        self._running = False

    def run(self):
        self._running = True
        log.info("[whiplash_clone_hero] Clone Hero song watcher started")
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.debug(f"[whiplash_clone_hero] tick error: {e}")
            time.sleep(self._poll_interval_s)

    def stop(self):
        self._running = False

    def _tick(self):
        new_song = self._watcher.poll()
        if not new_song:
            return
        artist, song = new_song

        from integrations.whiplash import get_session
        session = get_session()
        session.clone_hero_artist = artist
        session.clone_hero_song = song

        from integrations.clone_hero import get_quip
        quip = get_quip(artist)
        self._speak(quip)

    def _speak(self, text: str):
        try:
            from face.server import face_state
            snap = face_state.snapshot()
            if snap["speaking"] or snap["listening"] or snap["thinking"]:
                return
        except ImportError:
            pass

        try:
            from voice.pipeline import get_tts, AudioIO
            speech = get_tts().synthesize(text)
            AudioIO().play(speech)
            log.info(f"[whiplash_clone_hero] {text}")
        except Exception as e:
            log.warning(f"[whiplash_clone_hero] TTS/playback failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="IMQ2 — I am Q too")
    parser.add_argument("--text", action="store_true", help="Run in text/CLI mode")
    parser.add_argument("--profile", type=str, help="Personality profile to load")
    parser.add_argument("--llm", type=str, help="LLM backend override (claude|openai|ollama)")
    parser.add_argument("--env", type=str, help="Path to .env file to load")
    parser.add_argument("--face", action="store_true", help="Start the visual face (waveform display)")
    parser.add_argument("--no-kiosk-window", action="store_true",
                         help="With --face, run the server without auto-opening a kiosk browser window")
    parser.add_argument("--webapp", action="store_true", help="Start the web app API (text/camera/voice from iPhone)")
    args = parser.parse_args()

    # Load .env — always resolve relative to this script's directory,
    # not the current working directory, so it works regardless of where
    # main.py is invoked from.
    from dotenv import load_dotenv
    script_dir = Path(__file__).parent
    env_path = Path(args.env) if args.env else script_dir / ".env"

    if env_path.exists():
        loaded = load_dotenv(dotenv_path=env_path, override=True)
        if not loaded:
            print(f"⚠  Found {env_path} but load_dotenv() reported no variables loaded.")
    else:
        print(f"⚠  No .env file found at {env_path}")

    setup_logging()

    if args.profile:
        config.load_profile(args.profile)

    if not check_env(text_mode=args.text):
        sys.exit(1)

    if args.face:
        from face.server import start_face_server
        port = config.get("face.port", 8765)
        start_face_server(port=port)

        if not args.no_kiosk_window:
            _launch_kiosk_window(port)

    # Web app always starts — kill any stale instance first so we never
    # hit "port already in use" on rapid restarts.
    import subprocess, socket, signal, atexit
    webapp_port = config.get("webapp.port", 8766)

    # Kill any existing process on the webapp port before starting a fresh one
    try:
        subprocess.run(
            ["fuser", "-k", f"{webapp_port}/tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        import time; time.sleep(1.0)  # give the port time to release
    except Exception:
        pass

    webapp_proc = None
    try:
        webapp_proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "webapp" / "server.py")],
            env={**os.environ, "WEBAPP_PORT": str(webapp_port)},
        )
    except Exception as e:
        print(f"[Warning] Web app failed to start: {e} — continuing in voice/text-only mode.")

    # Clean up the webapp subprocess when main exits
    def _kill_webapp():
        if webapp_proc is None:
            return
        try:
            webapp_proc.terminate()
            webapp_proc.wait(timeout=3)
        except Exception:
            pass
    atexit.register(_kill_webapp)

    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "localhost"
    print(f"Web app running at http://{local_ip}:{webapp_port}")

    # Start Forza telemetry listener if enabled
    if config.get("integrations.forza_telemetry.enabled", False):
        try:
            from integrations.forza_telemetry import get_listener
            port = config.get("integrations.forza_telemetry.port", 8000)
            get_listener(port=port)
            print(f"Forza telemetry listener active on UDP port {port}")
        except Exception as e:
            print(f"[Warning] Forza telemetry listener failed to start: {e}")

    # Start AC telemetry listener if enabled (fed by windows/ac_bridge.py)
    if config.get("integrations.ac_telemetry.enabled", False):
        try:
            from integrations.ac_telemetry import get_listener as get_ac_listener
            ac_port = config.get("integrations.ac_telemetry.port", 8001)
            get_ac_listener(port=ac_port)
            print(f"AC telemetry listener active on UDP port {ac_port}")
        except Exception as e:
            print(f"[Warning] AC telemetry listener failed to start: {e}")

    # Start MSFS telemetry listener if enabled (fed by windows/msfs_bridge.py)
    if config.get("integrations.msfs_telemetry.enabled", False):
        try:
            from integrations.msfs_telemetry import get_listener as get_msfs_listener
            msfs_port = config.get("integrations.msfs_telemetry.port", 8002)
            get_msfs_listener(port=msfs_port)
            print(f"MSFS telemetry listener active on UDP port {msfs_port}")
        except Exception as e:
            print(f"[Warning] MSFS telemetry listener failed to start: {e}")

    # Start ED telemetry listener if enabled (fed by windows/ed_bridge.py)
    if config.get("integrations.ed_telemetry.enabled", False):
        try:
            from integrations.ed_telemetry import get_listener as get_ed_listener
            ed_port = config.get("integrations.ed_telemetry.port", 8003)
            get_ed_listener(port=ed_port)
            print(f"ED telemetry listener active on UDP port {ed_port}")
        except Exception as e:
            print(f"[Warning] ED telemetry listener failed to start: {e}")

    # Start Whiplash MIDI listener + Clone Hero song watcher if enabled
    if config.get("whiplash.enabled", False):
        try:
            from integrations.whiplash_midi import get_listener as get_midi_listener
            midi_port = config.get("whiplash.midi_port", "auto")
            result = get_midi_listener().start(port=midi_port)
            print(f"Whiplash MIDI: {result}")
        except Exception as e:
            print(f"[Warning] Whiplash MIDI listener failed to start: {e}")

        if config.get("whiplash.clone_hero.enabled", False):
            songfile = config.get("whiplash.clone_hero.songfile_path", "")
            if songfile:
                try:
                    poll_s = config.get("whiplash.clone_hero.poll_interval_s", 0.5)
                    clone_hero_thread = WhiplashCloneHeroThread(songfile, poll_interval_s=poll_s)
                    clone_hero_thread.start()
                    print(f"Whiplash Clone Hero watcher active on {songfile}")
                except Exception as e:
                    print(f"[Warning] Whiplash Clone Hero watcher failed to start: {e}")
            else:
                print("[Warning] whiplash.clone_hero.enabled is true but songfile_path is empty -- watcher not started")

    # Start proactive alert thread (race engineer / first officer /
    # watchalong) unless every domain is silenced. It gates itself live on
    # each domain's own frequency setting and the active personality
    # profile each poll, so settings-panel/profile changes apply without
    # needing to restart this thread.
    any_alerts_enabled = (
        config.get("race_engineer.frequency", "off") != "off"
        or config.get("first_officer.frequency", "off") != "off"
        or config.get("watchalong.live.frequency", "off") not in ("off", "silent")
        or config.get("integrations.ed_telemetry.enabled", False)
    )
    if any_alerts_enabled:
        try:
            race_alert_thread = RaceEngineerAlertThread()
            race_alert_thread.set_frequency(config.get("race_engineer.frequency", "normal"))
            race_alert_thread.start()
            print(f"Proactive alerts active (race_engineer: {config.get('race_engineer.frequency', 'off')}, "
                  f"first_officer: {config.get('first_officer.frequency', 'off')}, "
                  f"watchalong: {config.get('watchalong.live.frequency', 'off')} "
                  f"(sport: {config.get('watchalong.active_sport', 'f1')}), "
                  f"ed_ship_computer: {config.get('integrations.ed_telemetry.enabled', False)})")
        except Exception as e:
            print(f"[Warning] Proactive alert thread failed to start: {e}")

    try:
        agent = IMQ2Agent(llm_override=args.llm)
    except Exception as e:
        print(f"⚠  Could not start the LLM backend ({config.get('llm.backend', 'claude')}): {e}")
        print("   Check that its API key is set (see .env.template) or pick a different backend with --llm.")
        sys.exit(1)

    if args.text:
        run_text_mode(agent)
    else:
        run_voice_mode(agent)


def _launch_kiosk_window(port: int):
    """
    Open the face page in a dedicated fullscreen browser window.
    Starts fullscreen by default — press F11 to toggle windowed/fullscreen.
    Uses --start-fullscreen without --kiosk so the window manager still
    responds to keyboard shortcuts normally.
    """
    import subprocess
    import shutil

    url = f"http://127.0.0.1:{port}/"
    candidates = ["chromium-browser", "chromium", "google-chrome"]

    for browser in candidates:
        if shutil.which(browser):
            subprocess.Popen(
                [browser, f"--app={url}", "--start-fullscreen", "--noerrdialogs",
                 "--disable-infobars", "--no-first-run"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"Face window launched ({browser}) — F11 to toggle fullscreen.")
            return

    print(f"⚠  No Chromium-family browser found. Open {url} manually.")


if __name__ == "__main__":
    main()
