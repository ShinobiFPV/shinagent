"""
IMQ2 Voice Pipeline
STT: Deepgram | OpenAI Whisper Cloud | faster-whisper (local)
TTS: ElevenLabs | OpenAI TTS | Piper (local)
Audio capture and playback via sounddevice -- this is the only audio
capture library used anywhere in this file (pyaudio does not appear
here at all; it's only a commented-out alternative in requirements.txt).
"""

import io
import logging
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from typing import Optional, Callable

from config.loader import config

log = logging.getLogger(__name__)


# ===========================================================================
# Voice Activity Detection (silence/speech detection for recording cutoff)
# ===========================================================================

class VoiceActivityDetector:
    """
    Two-stage VAD: a cheap RMS energy gate combined with WebRTC VAD's
    speech/non-speech classification, frame by frame (10/20/30ms frames
    only -- webrtcvad's hard constraint). Energy alone is fast but noisy
    (a cough or a chair creak can look like "loud enough"); webrtcvad
    alone can be too eager to call quiet background noise "speech". Both
    agreeing is a meaningfully better signal than either alone.

    Frames are numpy int16 arrays (matching sounddevice's InputStream.read()
    return type, not pyaudio's raw bytes) -- converted to bytes only for
    the webrtcvad call, which requires a raw little-endian PCM buffer.

    Usage:
        vad = VoiceActivityDetector()
        vad.reset()
        for frame in audio_frames:           # each frame: chunk_samples int16
            result = vad.process(frame)
            if result == "end":
                break                        # silence threshold exceeded, stop
    """

    #: webrtcvad only accepts these sample rates and frame sizes -- anything
    #: else raises inside the C extension, so this is enforced up front
    #: rather than surfacing as a confusing runtime error mid-recording.
    _VALID_SAMPLE_RATES = (8000, 16000, 32000, 48000)
    _VALID_CHUNK_MS = (10, 20, 30)

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_ms: int = 30,
        silence_ms: int = 900,
        min_speech_ms: int = 300,
        energy_thresh: int = 300,
        vad_mode: int = 3,
        adaptive: bool = True,
    ):
        if sample_rate not in self._VALID_SAMPLE_RATES:
            raise ValueError(f"VAD sample_rate must be one of {self._VALID_SAMPLE_RATES}, got {sample_rate}")
        if chunk_ms not in self._VALID_CHUNK_MS:
            raise ValueError(f"VAD chunk_ms must be one of {self._VALID_CHUNK_MS}, got {chunk_ms}")

        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.energy_thresh = energy_thresh
        self.adaptive = adaptive
        self.chunk_size = int(sample_rate * chunk_ms / 1000)

        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(vad_mode)
            self._has_webrtcvad = True
        except ImportError:
            self._vad = None
            self._has_webrtcvad = False
            log.warning("webrtcvad not installed -- falling back to energy-only silence detection "
                        "(see requirements.txt for install notes)")

        self._noise_samples: list = []
        self._noise_floor = energy_thresh
        self.reset()

    def reset(self):
        self._silence_frames = 0
        self._speech_frames = 0
        self._total_frames = 0
        self._in_speech = False
        self.current_level = 0.0  # exposed for the UI level meter

    @staticmethod
    def _rms(frame) -> float:
        """RMS of an int16 numpy frame. Vectorized -- this runs once per
        chunk_ms of audio in a tight loop, so a Python-level struct.unpack
        + sum-of-squares loop (the naive approach) would add real overhead
        at 30ms-per-call cadence; numpy does the same computation in C."""
        import numpy as np
        if len(frame) == 0:
            return 0.0
        return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

    def _update_noise_floor(self, rms: float):
        """Adaptive noise floor -- tracks background level while NOT
        speaking, so a noisier room (fan, TV) raises the effective
        threshold automatically instead of needing a manual re-tune."""
        self._noise_samples.append(rms)
        if len(self._noise_samples) > 50:  # ~1.5s of chunk_ms=30 frames
            self._noise_samples.pop(0)
        if self._noise_samples:
            sorted_s = sorted(self._noise_samples)
            idx = int(len(sorted_s) * 0.75)
            self._noise_floor = max(self.energy_thresh, sorted_s[idx] * 1.5)

    def process(self, frame) -> str:
        """
        Process one chunk_ms frame (numpy int16 array, chunk_size samples).

        Returns:
          "speech"  -- active speech detected
          "silence" -- silence, but not yet past the cutoff duration
          "end"     -- silence threshold exceeded past min_speech_ms of
                       prior speech -- caller should stop recording
          "noise"   -- below the energy threshold and speech hasn't
                       started yet (pre-roll territory)
        """
        self._total_frames += 1
        rms = self._rms(frame)
        self.current_level = rms

        effective_threshold = max(self.energy_thresh, self._noise_floor) if self.adaptive else self.energy_thresh
        energy_speech = rms > effective_threshold

        if self._has_webrtcvad and energy_speech:
            try:
                vad_speech = self._vad.is_speech(frame.tobytes(), self.sample_rate)
            except Exception:
                # A single bad frame (odd length, etc.) should degrade to
                # the energy gate's own opinion, not abort the recording.
                vad_speech = energy_speech
        else:
            vad_speech = energy_speech

        is_speech = energy_speech and vad_speech

        if is_speech:
            self._speech_frames += 1
            self._silence_frames = 0
            self._in_speech = True
        else:
            self._silence_frames += 1
            if not self._in_speech and self.adaptive:
                self._update_noise_floor(rms)

        silence_ms = self._silence_frames * self.chunk_ms

        # NOTE: deliberately NOT gated on min_speech_ms here -- "when to stop
        # recording" and "whether the result is long enough to keep" are
        # separate questions. A too-short blip (e.g. a cough, or a button
        # mash under min_speech_ms) must still stop recording once trailing
        # silence passes silence_ms, rather than running to max_duration_s;
        # has_speech (below) is what enforces the minimum-speech guard on
        # the caller's side, discarding short results without ever sending
        # them to STT.
        if silence_ms >= self.silence_ms and self._in_speech:
            return "end"
        if is_speech:
            return "speech"
        if self._in_speech:
            return "silence"
        return "noise"

    @property
    def has_speech(self) -> bool:
        return self._speech_frames * self.chunk_ms >= self.min_speech_ms


def _vad_from_config(sample_rate: Optional[int] = None) -> VoiceActivityDetector:
    """
    Build a VoiceActivityDetector from config.yaml's voice.vad.* settings.
    `sample_rate`, if given, overrides voice.vad.sample_rate -- used so the
    VAD operates at whatever rate the currently-selected input device is
    actually configured for (see voice.input_device_options), rather than a
    fixed rate that might not match. Every device currently configured
    (16000Hz for the C920, 48000Hz for the DW20) is one of webrtcvad's
    supported rates, so this should always succeed; if a future device
    isn't, the caller falls back to 16000Hz.
    """
    return VoiceActivityDetector(
        sample_rate=sample_rate if sample_rate is not None else config.get("voice.vad.sample_rate", 16000),
        chunk_ms=config.get("voice.vad.chunk_ms", 30),
        silence_ms=config.get("voice.vad.silence_ms", 900),
        min_speech_ms=config.get("voice.vad.min_speech_ms", 300),
        energy_thresh=config.get("voice.vad.energy_thresh", 300),
        vad_mode=config.get("voice.vad.mode", 3),
        adaptive=config.get("voice.vad.adaptive", True),
    )


# ===========================================================================
# STT Backends
# ===========================================================================

class STTBackend(ABC):
    @abstractmethod
    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        ...


class DeepgramSTT(STTBackend):
    def __init__(self):
        from deepgram import DeepgramClient
        self._client = DeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])
        self._model = config.get("voice.deepgram.model", "nova-3")
        self._language = config.get("voice.deepgram.language", "en-US")

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        # No sample_rate/encoding params needed — Deepgram reads this directly
        # from the WAV container's header. Per Deepgram's docs: sample_rate is
        # only required for raw/non-containerized audio, and should be omitted
        # entirely for containerized formats like the WAV files we send here.
        response = self._client.listen.v1.media.transcribe_file(
            request=audio_bytes,
            model=self._model,
            language=self._language,
            smart_format=True,
        )
        return response.results.channels[0].alternatives[0].transcript


class WhisperCloudSTT(STTBackend):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self._model = config.get("voice.whisper_cloud.model", "whisper-1")

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            fname = f.name
        with open(fname, "rb") as f:
            result = self._client.audio.transcriptions.create(model=self._model, file=f)
        os.unlink(fname)
        return result.text


class FasterWhisperSTT(STTBackend):
    def __init__(self):
        from faster_whisper import WhisperModel
        size = config.get("voice.faster_whisper.model_size", "small")
        device = config.get("voice.faster_whisper.device", "cpu")
        self._model = WhisperModel(size, device=device)
        log.info(f"faster-whisper loaded: {size} on {device}")

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        import numpy as np
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio_np, beam_size=5)
        return " ".join(s.text for s in segments).strip()


# ===========================================================================
# Wake Word Detection (Porcupine)
# ===========================================================================

class WakeWordDetector:
    """
    Listens continuously for the "Hey Dude" wake word using Picovoice Porcupine.
    Runs in a background thread; sets a threading.Event when triggered.
    """

    def __init__(self):
        import pvporcupine
        from pathlib import Path as _Path
        access_key  = config.get("voice.wake_word.access_key", "")
        # Fall back to environment variable if config key is empty
        if not access_key:
            import os
            from dotenv import load_dotenv
            load_dotenv(_Path(__file__).parent.parent / ".env")
            access_key = os.environ.get("PORCUPINE_ACCESS_KEY", "")
        ppn_path    = _Path(__file__).parent.parent / config.get(
            "voice.wake_word.ppn_path", "wake_words/Hey-Dude_en_raspberry-pi_v4_0_0.ppn"
        )
        sensitivity = float(config.get("voice.wake_word.sensitivity", 0.5))

        if not access_key:
            raise RuntimeError("PORCUPINE_ACCESS_KEY not set in config or .env")
        if not ppn_path.exists():
            raise FileNotFoundError(f"Wake word model not found: {ppn_path}")

        self._porcupine = pvporcupine.create(
            access_key=access_key,
            keyword_paths=[str(ppn_path)],
            sensitivities=[sensitivity],
        )
        self._triggered  = threading.Event()
        self._running    = False
        self._thread     = None
        self._input_device = self._resolve_input_device()
        log.info(f"Wake word detector ready — 'Hey Dude' (sensitivity {sensitivity})")

    def _resolve_input_device(self) -> Optional[int]:
        name = config.get("voice.input_device", "default")
        if name == "default":
            return None
        import sounddevice as sd
        for i, dev in enumerate(sd.query_devices()):
            if name.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
                return i
        return None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def wait_for_wake_word(self, timeout: Optional[float] = None) -> bool:
        """Block until wake word detected. Returns True if triggered, False on timeout."""
        self._triggered.clear()
        triggered = self._triggered.wait(timeout=timeout)
        if triggered:
            # Stop the listener stream so the mic is free for VAD recording
            self._running = False
            if self._thread:
                self._thread.join(timeout=2.0)
        return triggered

    def rearm(self):
        """Restart the listener after a recording session."""
        self._running = True
        self._thread  = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def _listen_loop(self):
        import struct
        import sounddevice as sd
        frame_len = self._porcupine.frame_length
        sample_rate = self._porcupine.sample_rate

        log.info("Wake word listener active — say 'Hey Dude' to activate Q2.")
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=self._input_device,
            blocksize=frame_len,
        ) as stream:
            while self._running:
                try:
                    pcm_bytes, _ = stream.read(frame_len)
                    pcm = struct.unpack_from("h" * frame_len, pcm_bytes.tobytes())
                    result = self._porcupine.process(pcm)
                    if result >= 0:
                        log.info("Wake word detected!")
                        self._triggered.set()
                        # Brief pause before re-arming so it doesn't retrigger immediately
                        import time; time.sleep(1.0)
                        self._triggered.clear()
                except Exception as e:
                    # A single bad frame (mic hiccup, buffer overrun) must not
                    # permanently kill this daemon thread — log and keep
                    # listening rather than silently going deaf for the rest
                    # of the session.
                    log.warning(f"Wake word listener frame error: {e}")

    def delete(self):
        self.stop()
        if hasattr(self, "_porcupine"):
            self._porcupine.delete()


# ===========================================================================
# TTS Backends
# ===========================================================================

class TTSBackend(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Return raw audio bytes (WAV or MP3)."""
        ...


class DeepgramTTS(TTSBackend):
    """
    Deepgram Aura-2 TTS. Uses the same vendor as STT, which keeps the whole
    speech loop on one provider's infrastructure — fewer handoffs, lower
    latency, and one API key to manage instead of two.
    """

    def __init__(self):
        from deepgram import DeepgramClient
        self._client = DeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])
        self._model = config.get("voice.deepgram_tts.model", "aura-2-zeus-en")

    def synthesize(self, text: str) -> bytes:
        # Explicitly request linear16 PCM in a WAV container at 48kHz — matching
        # the 7RYMS DW20's native rate. Aura-2 defaults to 24kHz, which the
        # DW20 hardware rejected outright (PaErrorCode -9997, invalid sample
        # rate) since USB audio devices often only accept specific fixed rates.
        chunks = self._client.speak.v1.audio.generate(
            text=text,
            model=self._model,
            encoding="linear16",
            container="wav",
            sample_rate=48000,
        )
        return b"".join(chunks)


class ElevenLabsTTS(TTSBackend):
    def __init__(self):
        from elevenlabs.client import ElevenLabs
        self._client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
        self._voice_id = config.get("voice.elevenlabs.voice_id", "")
        self._model = config.get("voice.elevenlabs.model", "eleven_turbo_v2")

    def synthesize(self, text: str) -> bytes:
        audio = self._client.generate(
            text=text,
            voice=self._voice_id,
            model=self._model,
        )
        return b"".join(audio)


class OpenAITTS(TTSBackend):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self._voice = config.get("voice.openai_tts.voice", "nova")
        self._model = config.get("voice.openai_tts.model", "tts-1")

    def synthesize(self, text: str) -> bytes:
        response = self._client.audio.speech.create(
            model=self._model, voice=self._voice, input=text
        )
        return response.content


class PiperTTS(TTSBackend):
    """Local TTS via Piper. Requires piper binary on PATH and a model .onnx file."""

    def __init__(self):
        import subprocess
        self._subprocess = subprocess
        self._model_path = config.get("voice.piper.model_path", "models/piper/en_US-lessac-medium.onnx")

    def synthesize(self, text: str) -> bytes:
        result = self._subprocess.run(
            ["piper", "--model", self._model_path, "--output_raw"],
            input=text.encode(),
            capture_output=True,
            timeout=15,
        )
        return result.stdout


# ===========================================================================
# Audio Capture + Playback
# ===========================================================================

class AudioIO:
    """Handles microphone capture (push-to-talk) and speaker playback."""

    def __init__(self):
        input_device_name = config.get("voice.input_device", "default")

        # Check for a per-device sample rate override before falling back to
        # the global voice.sample_rate. This lets devices like the C920 (which
        # only supports 16000/32000 Hz) coexist with the DW20 (48000 Hz)
        # without requiring a manual config.yaml edit on every switch.
        device_sample_rate = None
        for opt in config.get("voice.input_device_options", []):
            if opt.get("name", "").lower() in input_device_name.lower():
                device_sample_rate = opt.get("sample_rate")
                break

        self._sample_rate = device_sample_rate or config.get("voice.sample_rate", 48000)
        if device_sample_rate:
            log.info(f"Input device '{input_device_name}' sample rate override: {self._sample_rate}Hz")

        self._input_device = self._resolve_device(input_device_name, kind="input")
        self._output_device = self._resolve_device(
            config.get("voice.output_device", "default"), kind="output"
        )

        # Controller-driven extras (voice/controller.py's volume/repeat/cancel
        # actions) -- in-memory only, reset on restart, no config persistence.
        self._volume = 1.0
        self._last_audio_bytes: Optional[bytes] = None
        self._is_recording = False
        self._is_playing = False
        self._cancel_recording_flag = threading.Event()
        self._cancel_playback_flag = threading.Event()

    def adjust_volume(self, delta: float):
        """Nudge playback gain by `delta`, clamped to [0.1, 2.0]."""
        self._volume = max(0.1, min(2.0, self._volume + delta))
        log.info(f"Playback volume -> {self._volume:.2f}")

    def replay_last(self):
        """Re-play the last synthesized TTS response, if any -- used by the
        controller's 'repeat' action."""
        if self._last_audio_bytes:
            self.play(self._last_audio_bytes)
        else:
            log.info("Replay requested but nothing has been played yet this session.")

    def cancel_current(self) -> str:
        """Cancel whichever is currently happening -- recording or TTS
        playback -- used by the controller's 'cancel' action. Returns a
        short string describing what (if anything) was cancelled."""
        if self._is_recording:
            self._cancel_recording_flag.set()
            return "recording"
        if self._is_playing:
            self._cancel_playback_flag.set()
            return "playback"
        return "nothing"

    @staticmethod
    def _resolve_device(name_or_default: str, kind: str) -> Optional[int]:
        """
        Resolve a config device name to a sounddevice index via substring match.
        Returns None for 'default' (let sounddevice/PortAudio pick), which then
        falls back to whatever the system's current default device is.
        """
        if name_or_default == "default":
            return None

        import sounddevice as sd
        devices = sd.query_devices()
        channel_key = "max_input_channels" if kind == "input" else "max_output_channels"

        for i, dev in enumerate(devices):
            if name_or_default.lower() in dev["name"].lower() and dev[channel_key] > 0:
                log.info(f"Resolved {kind} device '{name_or_default}' -> index {i} ({dev['name']})")
                return i

        log.warning(
            f"Could not find {kind} device matching '{name_or_default}' — "
            f"falling back to system default. Run sd.query_devices() to see available devices."
        )
        return None

    def record_utterance_vad(self, early_stop_check: Optional[Callable[[], bool]] = None) -> bytes:
        """
        Record with VAD auto-start/auto-stop -- no manual button/Enter press
        needed to START. This is what the wake-word flow uses: once "Hey
        Dude" fires, recording starts the instant this is called and stops
        on its own once VoiceActivityDetector decides the speaker has gone
        quiet for voice.vad.silence_ms. Previously this method existed but
        had no caller anywhere in the codebase (confirmed via repo-wide
        search), and main.py's wake-word dispatch still required a manual
        button press after the wake word triggered -- this is what actually
        makes that path hands-free.

        `early_stop_check`, if given, is polled non-blockingly alongside VAD
        silence detection so the wake-word + talk-button combo mode can
        still stop early with a manual button press.

        If voice.vad.enabled is False, there's no button available in pure
        wake-word mode to fall back on, so this degrades to waiting on
        `early_stop_check` (if given) or an Enter press.
        """
        if not config.get("voice.vad.enabled", True):
            log.info("voice.vad.enabled is False — falling back to manual-stop only")
            if early_stop_check is not None:
                wav_bytes = self._record_until(stop_condition=self._blocking_wait(early_stop_check))
            else:
                print("🎙  Recording... press Enter to stop.")
                wav_bytes = self._record_until(stop_condition=lambda: input() or True)
            return wav_bytes

        print("🎙  Listening... (auto-stops on silence)")
        wav_bytes = self._record_with_vad(early_stop_check=early_stop_check)
        print("⏹  Silence detected — done." if wav_bytes else "⏹  No speech detected.")
        return wav_bytes

    def record_utterance_ptt(self) -> bytes:
        """
        Push-to-talk recording via Enter key: press Enter to start, speak.
        Recording auto-stops on VAD silence detection, OR you can press
        Enter again to stop early -- whichever comes first. The Enter-key
        watch runs on a background thread (input() is a blocking call, so
        it can't be polled directly alongside the VAD loop).

        If voice.vad.enabled is False, reverts to the original manual-only
        behavior (press Enter to stop, no auto-cutoff).
        """
        input("Press Enter to start speaking...")

        if not config.get("voice.vad.enabled", True):
            print("🎙  Recording... press Enter to stop.")
            wav_bytes = self._record_until(stop_condition=lambda: input() or True)
            print("⏹  Stopped recording.")
            return wav_bytes

        print("🎙  Recording... speak now (auto-stops on silence, or press Enter to stop early).")
        stop_check = self._make_enter_watcher()
        wav_bytes = self._record_with_vad(early_stop_check=stop_check)
        print("⏹  Stopped recording.")
        return wav_bytes

    @staticmethod
    def _blocking_wait(check: Callable[[], bool]) -> Callable[[], bool]:
        """Adapts a non-blocking check() callable into a blocking one that
        polls until it returns True — for handing to _record_until(), which
        expects a stop_condition that blocks."""
        import time

        def _wait():
            while not check():
                time.sleep(0.05)
            return True

        return _wait

    def record_utterance_button(self, talk_button_state) -> bytes:
        """
        Push-to-talk recording via the talk button toggle (8BitDo Zero 2 or
        Flipper Zero in keyboard mode, or any other device sending the
        configured key). First toggle starts recording. Recording then
        auto-stops on VAD silence detection, OR a second toggle stops it
        manually -- whichever comes first (button release still works
        exactly as before; VAD silence detection is an additional trigger,
        not a replacement).

        Plays a short acknowledgement tone on both start and stop (distinct
        pitches, so you can tell which one happened by ear without needing
        to see the terminal), and signals the face server so the visualizer
        can show a "listening" state — useful since push-to-talk otherwise
        gives no feedback once you're not looking at a screen with logs.
        """
        import time

        print("Waiting for talk button (press once to start)...")
        while not talk_button_state.consume_toggle():
            time.sleep(0.05)
            # Check for restart flag while idle — this is the only long-blocking
            # wait in the voice loop, so we must poll here or restarts are delayed
            # until after the next full recording cycle completes.
            from pathlib import Path as _Path
            _flag = _Path(__file__).parent.parent / ".restart_requested"
            if _flag.exists():
                _flag.unlink()
                import os as _os, sys as _sys
                print("\n[Webapp: restart requested — restarting Q2...]")
                _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        self.play_tone(frequency=880, duration_s=0.12)  # higher pitch: "listening started"
        self._signal_face_listening(True)

        if not config.get("voice.vad.enabled", True):
            print("🎙  Recording... press talk button again to stop.")
            wav_bytes = self._record_until(stop_condition=self._blocking_wait(talk_button_state.consume_toggle))
        else:
            print("🎙  Recording... auto-stops on silence, or press talk button again to stop early.")
            wav_bytes = self._record_with_vad(early_stop_check=talk_button_state.consume_toggle)

        self._signal_face_listening(False)
        self.play_tone(frequency=440, duration_s=0.12)  # lower pitch: "listening stopped"
        print("⏹  Stopped recording.")
        return wav_bytes

    @staticmethod
    def _make_enter_watcher() -> Callable[[], bool]:
        """
        Returns a non-blocking check() callable that becomes True once Enter
        is pressed, by running the blocking input() call on a background
        daemon thread. Needed because _record_with_vad()'s loop must poll
        the "stop early" condition without blocking on it (unlike the old
        _record_until()'s stop_condition, which was allowed to block).
        """
        event = threading.Event()

        def _wait():
            try:
                input()
            except Exception:
                pass
            event.set()

        threading.Thread(target=_wait, daemon=True).start()
        return event.is_set

    def _record_with_vad(self, early_stop_check: Optional[Callable[[], bool]] = None) -> bytes:
        """
        Record audio using VoiceActivityDetector for automatic silence-based
        cutoff, with an optional non-blocking early_stop_check() polled
        alongside it each frame (e.g. a second button toggle) so recording
        can also end manually — button/Enter release OR silence detection,
        whichever comes first.

        Uses blocking sounddevice reads (stream.read(chunk_size), returning
        a NumPy int16 array — NOT pyaudio's raw bytes) rather than the
        callback-based approach in _record_until(), since the VAD needs to
        inspect each chunk synchronously as it arrives to decide whether to
        keep going. Retains a short pre-roll buffer of pre-speech chunks so
        the first word isn't clipped by VAD detection lag.
        """
        import sounddevice as sd
        import numpy as np
        import wave
        import time

        try:
            vad = _vad_from_config(sample_rate=self._sample_rate)
        except ValueError as e:
            log.warning(f"VAD sample rate mismatch ({e}) — falling back to 16000Hz for VAD only")
            vad = _vad_from_config(sample_rate=16000)
        max_duration = config.get("voice.vad.max_duration_s", 30.0)
        pre_roll_ms = config.get("voice.vad.pre_roll_ms", 100)
        pre_roll_chunks = max(1, int(pre_roll_ms / vad.chunk_ms))

        frames: list = []
        pre_roll: list = []
        started_at = time.time()
        cancelled = False

        self._is_recording = True
        self._cancel_recording_flag.clear()
        try:
            stream = sd.InputStream(
                samplerate=vad.sample_rate, channels=1, dtype="int16",
                device=self._input_device, blocksize=vad.chunk_size,
            )
            with stream:
                while True:
                    chunk, _overflowed = stream.read(vad.chunk_size)
                    chunk = chunk.reshape(-1)

                    result = vad.process(chunk)
                    self._signal_face_audio_level(vad.current_level)

                    if result == "noise":
                        pre_roll.append(chunk)
                        if len(pre_roll) > pre_roll_chunks:
                            pre_roll.pop(0)
                    else:
                        if pre_roll:
                            frames.extend(pre_roll)
                            pre_roll = []
                        frames.append(chunk)

                    if result == "end":
                        break
                    if self._cancel_recording_flag.is_set():
                        cancelled = True
                        break
                    if early_stop_check is not None and early_stop_check():
                        break
                    if time.time() - started_at > max_duration:
                        break
        finally:
            self._is_recording = False
            self._signal_face_audio_level(0.0)

        if cancelled or not frames or not vad.has_speech:
            return b""

        audio_np = np.concatenate(frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(vad.sample_rate)
            wf.writeframes(audio_np.tobytes())
        return buf.getvalue()

    def play_tone(self, frequency: float = 880, duration_s: float = 0.12, volume: float = 0.3):
        """
        Synthesize and play a short pure-tone acknowledgement beep — no
        external sound file needed, just a generated sine wave with a quick
        fade in/out to avoid clicking. Used for talk-button start/stop
        feedback so you don't need to watch the terminal to know Q2 heard
        the button press.
        """
        import sounddevice as sd
        import numpy as np

        sr = self._sample_rate
        n_samples = int(sr * duration_s)
        t = np.linspace(0, duration_s, n_samples, endpoint=False)
        tone = np.sin(2 * np.pi * frequency * t).astype(np.float32) * volume

        # Quick fade in/out (a few ms) to prevent an audible click/pop at the
        # start and end of the tone — a hard-edged sine wave has a sharp
        # discontinuity at t=0 that the ear hears as a click.
        fade_samples = max(1, int(sr * 0.01))
        fade_in = np.linspace(0, 1, fade_samples)
        fade_out = np.linspace(1, 0, fade_samples)
        tone[:fade_samples] *= fade_in
        tone[-fade_samples:] *= fade_out

        # Apply the same silent pre-roll used for TTS playback (see play()).
        # Without it, a short tone like this is exactly the kind of audio
        # most likely to get fully swallowed by a Bluetooth speaker's
        # wake-from-idle latency, since it fires right at the moment of a
        # button press — often the first sound after a period of silence,
        # which is precisely when the speaker is most likely to be asleep.
        padded_tone = self._prepend_silence(tone, sr)
        stereo_tone = self._to_stereo(padded_tone)

        try:
            sd.play(stereo_tone, sr, device=self._output_device)
            sd.wait()
        except Exception as e:
            # Tone playback failing should never block actual recording —
            # log and move on rather than raising.
            log.warning(f"Acknowledgement tone playback failed: {e}")

    @staticmethod
    def _signal_face_listening(is_listening: bool):
        """Notify the face server of listening state, if it's running."""
        try:
            from face.server import face_state
            face_state.set_listening(is_listening)
        except ImportError:
            pass

    @staticmethod
    def _signal_face_thinking(is_thinking: bool):
        """Notify the face server of thinking state, if it's running."""
        try:
            from face.server import face_state
            face_state.set_thinking(is_thinking)
        except ImportError:
            pass

    @staticmethod
    def _signal_face_audio_level(level: float):
        """Push the current VAD RMS level to the face server, if it's
        running, for the settings.html live meter and any future
        visualizer use. Best-effort — never allowed to interrupt recording."""
        try:
            from face.server import face_state
            face_state.set_audio_level(level)
        except ImportError:
            pass
        except Exception:
            pass

    def _record_until(self, stop_condition) -> bytes:
        """
        Shared recording core. Starts capturing audio immediately, then
        blocks on stop_condition() (a callable that blocks until the
        record should stop) before finalizing and returning WAV bytes.
        Used by both the Enter-key and talk-button trigger mechanisms so
        the actual audio capture logic only exists in one place.
        """
        import sounddevice as sd
        import numpy as np
        import wave

        frames = []

        def _callback(indata, frame_count, time_info, status):
            frames.append(indata.copy())

        stream = sd.InputStream(
            samplerate=self._sample_rate, channels=1, dtype="int16",
            device=self._input_device, callback=_callback
        )
        with stream:
            stop_condition()  # blocks until the stop trigger fires, while callback fills `frames`

        if not frames:
            return b""

        audio_np = np.concatenate(frames, axis=0)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio_np.tobytes())
        return buf.getvalue()

    def play(self, audio_bytes: bytes):
        """Play audio bytes (MP3 or WAV) to the configured output device."""
        import sounddevice as sd
        import soundfile as sf
        import time

        self._last_audio_bytes = audio_bytes  # for the controller's 'repeat' action

        buf = io.BytesIO(audio_bytes)
        data, sr = sf.read(buf, dtype="float32")
        data = self._to_stereo(data)

        if self._volume != 1.0:
            data = data * self._volume

        # Extract the amplitude envelope from the ORIGINAL (pre-padding) audio
        # for the face visualizer. The face's "speaking" clock must start
        # preroll_s seconds AFTER sd.play() is called, not immediately —
        # otherwise the visualizer animates during the silent Bluetooth-wakeup
        # buffer, before any sound is actually audible.
        original_duration_s = len(data) / sr
        envelope = self._compute_envelope(data)
        preroll_s = config.get("voice.playback_preroll_s", 0.35)

        padded_data = self._prepend_silence(data, sr)

        speech_start_at = time.time() + preroll_s
        self._signal_face_speaking(envelope, original_duration_s, start_at=speech_start_at)

        # Clear thinking state right before audio starts — this keeps the
        # white "thinking" face visible all the way through TTS synthesis and
        # up to the exact moment Q2's voice begins, rather than flashing back
        # to blue during the synthesis gap between agent.chat() returning and
        # play() being called.
        self._signal_face_thinking(False)

        self._is_playing = True
        self._cancel_playback_flag.clear()
        try:
            sd.play(padded_data, sr, device=self._output_device)
            self._wait_playback_cancellable(sd)
        except sd.PortAudioError as e:
            # Hardware rejected this sample rate (PaErrorCode -9997 is the
            # classic "invalid sample rate" case — USB audio devices often
            # only accept a small fixed set of rates). Resample to the
            # device's expected rate and retry once before giving up.
            log.warning(f"Playback failed at {sr}Hz ({e}); resampling to {self._sample_rate}Hz and retrying.")
            resampled = self._resample(padded_data, sr, self._sample_rate)
            sd.play(resampled, self._sample_rate, device=self._output_device)
            self._wait_playback_cancellable(sd)
        finally:
            self._is_playing = False
            self._signal_face_idle()

    def _wait_playback_cancellable(self, sd_module):
        """Like sd.wait(), but polls for the controller's 'cancel' action
        (self._cancel_playback_flag) every 50ms and stops playback early if
        set, instead of blocking uninterruptibly until the clip finishes."""
        import time

        while sd_module.get_stream().active:
            if self._cancel_playback_flag.is_set():
                sd_module.stop()
                break
            time.sleep(0.05)

    @staticmethod
    def _compute_envelope(data, n_buckets: int = 100) -> list:
        """
        Downsample audio data into a coarse amplitude envelope (n_buckets values,
        each 0.0-1.0) for the face visualizer to animate against. We don't need
        sample-accurate detail for a visual effect — ~100 points across the
        whole utterance is plenty smooth at any reasonable speech length.
        """
        import numpy as np
        # Collapse to mono for envelope purposes regardless of stereo input
        mono = data.mean(axis=1) if data.ndim > 1 else data
        amplitude = np.abs(mono)

        if len(amplitude) == 0:
            return []

        bucket_size = max(1, len(amplitude) // n_buckets)
        buckets = [
            float(amplitude[i:i + bucket_size].mean())
            for i in range(0, len(amplitude), bucket_size)
        ]

        # Normalize to 0-1 range so the face's visual scale is consistent
        # regardless of the TTS voice's actual loudness/gain.
        peak = max(buckets) if buckets else 1.0
        if peak > 0:
            buckets = [b / peak for b in buckets]

        return buckets

    @staticmethod
    def _signal_face_speaking(envelope: list, duration_s: float, start_at: float):
        """Notify the face server that speech is starting, if it's running."""
        try:
            from face.server import face_state
            face_state.start_speaking(envelope, duration_s, start_at=start_at)
        except ImportError:
            pass  # face server not in use — fine, this is an optional add-on

    @staticmethod
    def _signal_face_idle():
        """Notify the face server that speech has ended, if it's running."""
        try:
            from face.server import face_state
            face_state.stop_speaking()
        except ImportError:
            pass

    def _prepend_silence(self, data, sr: int):
        """
        Prepend a short near-silent buffer before playback. Bluetooth speakers
        commonly take a moment to wake from idle/power-save and actually start
        outputting audio once a stream begins — without this, the first
        syllable or two of Q2's response gets clipped while the speaker (or
        PortAudio/PipeWire's own stream startup) catches up.
        """
        import numpy as np
        pad_seconds = config.get("voice.playback_preroll_s", 0.35)
        pad_samples = int(sr * pad_seconds)
        if data.ndim == 1:
            silence = np.zeros(pad_samples, dtype=data.dtype)
        else:
            silence = np.zeros((pad_samples, data.shape[1]), dtype=data.dtype)
        return np.concatenate([silence, data], axis=0)

    @staticmethod
    def _to_stereo(data):
        """
        Duplicate mono audio to both channels. Without this, mono playback to
        a stereo sink (e.g. Bluetooth headphones) often routes to the left
        channel only, since there's no universal convention for how a mono
        signal should map onto a stereo output.
        """
        import numpy as np
        if data.ndim == 1:
            return np.column_stack([data, data])
        return data

    @staticmethod
    def _resample(data, orig_sr: int, target_sr: int):
        """Simple linear-interpolation resample — adequate for speech playback.
        Handles both mono (1D) and stereo (2D, shape (N, channels)) arrays."""
        import numpy as np
        if orig_sr == target_sr:
            return data

        n_samples = data.shape[0]
        duration = n_samples / orig_sr
        target_len = int(duration * target_sr)
        orig_indices = np.linspace(0, n_samples - 1, num=n_samples)
        target_indices = np.linspace(0, n_samples - 1, num=target_len)

        if data.ndim == 1:
            return np.interp(target_indices, orig_indices, data)
        else:
            # Resample each channel independently, then stack back together
            channels = [np.interp(target_indices, orig_indices, data[:, c]) for c in range(data.shape[1])]
            return np.column_stack(channels)


# ===========================================================================
# Factory functions
# ===========================================================================

_STT_BACKENDS = {
    "deepgram": DeepgramSTT,
    "whisper_cloud": WhisperCloudSTT,
    "faster_whisper": FasterWhisperSTT,
}

_TTS_BACKENDS = {
    "deepgram_tts": DeepgramTTS,
    "elevenlabs": ElevenLabsTTS,
    "openai_tts": OpenAITTS,
    "piper": PiperTTS,
}


def get_stt(override: Optional[str] = None) -> STTBackend:
    name = override or config.get("voice.stt_backend", "deepgram")
    cls = _STT_BACKENDS.get(name)
    if not cls:
        raise ValueError(f"Unknown STT backend: {name}")
    return cls()


def get_tts(override: Optional[str] = None) -> TTSBackend:
    name = override or config.get("voice.tts_backend", "elevenlabs")
    cls = _TTS_BACKENDS.get(name)
    if not cls:
        raise ValueError(f"Unknown TTS backend: {name}")
    return cls()
