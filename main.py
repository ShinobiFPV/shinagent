#!/usr/bin/env python3
"""
IMQ2 — Main Entry Point
"I am Kew too"

Usage:
  python main.py             # voice mode (default)
  python main.py --text      # text/CLI mode (no mic/speaker required)
  python main.py --profile q2_guest   # load a non-default personality profile
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on the path regardless of invocation directory
sys.path.insert(0, str(Path(__file__).parent))

from config.loader import config
from core.agent import IMQ2Agent


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
    tts = get_tts()
    audio = AudioIO()

    talk_button_listener = None
    talk_button_state = None
    wake_word_listener = None

    if config.get("voice.talk_button.enabled", False):
        from voice.talk_button import start_talk_button_listener, talk_button_state as tb_state
        key = config.get("voice.talk_button.key", "g")
        device_name = config.get("voice.talk_button.device_name", "")
        talk_button_listener = start_talk_button_listener(key_name=key, device_name=device_name)
        if talk_button_listener:
            talk_button_state = tb_state
            print(f"\n{name} voice mode active — talk button ready (key: '{key}').")
        else:
            print(f"\n{name} voice mode active — talk button unavailable, using Enter key push-to-talk.")
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
                # Wake word + PTT combo — "Hey Dude" activates, talk button stops
                print("Listening for wake word...")
                wake_word_listener.wait_for_wake_word()
                audio.play_tone(frequency=880, duration_s=0.12)
                audio._signal_face_listening(True)
                print("🎙  Recording... press talk button to stop.")
                audio_bytes = audio.record_utterance_button(talk_button_state)
                wake_word_listener.rearm()
            elif wake_word_listener is not None:
                # Wake word only — use Enter key to stop
                print("Listening for wake word...")
                wake_word_listener.wait_for_wake_word()
                audio.play_tone(frequency=880, duration_s=0.12)
                audio_bytes = audio.record_utterance_ptt()
                wake_word_listener.rearm()
            elif talk_button_state is not None:
                audio_bytes = audio.record_utterance_button(talk_button_state)
            else:
                audio_bytes = audio.record_utterance_ptt()

            if not audio_bytes:
                continue

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

            speech = tts.synthesize(reply)
            audio.play(speech)
            print()

    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        if talk_button_listener:
            talk_button_listener.stop()
        if wake_word_listener:
            wake_word_listener.delete()


def main():
    parser = argparse.ArgumentParser(description="IMQ2 — I am Kew too")
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

    webapp_proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "webapp" / "server.py")],
        env={**os.environ, "WEBAPP_PORT": str(webapp_port)},
    )

    # Clean up the webapp subprocess when main exits
    def _kill_webapp():
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

    agent = IMQ2Agent(llm_override=args.llm)

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
