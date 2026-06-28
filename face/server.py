"""
IMQ2 Face Server
A minimal local HTTP server (stdlib only, no new dependencies) that serves
the kiosk-mode waveform face, exposes the state polling endpoint, and
provides a settings panel at /settings.html with GET/POST /settings for
runtime configuration — wake word, visualizer colours, profile, output
device, talk button key, and wake word sensitivity.

Runs in a background thread inside the main process.
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from config.loader import config

log = logging.getLogger(__name__)

FACE_DIR = Path(__file__).parent
INDEX_PATH = FACE_DIR / "index.html"
SETTINGS_PATH = FACE_DIR / "settings.html"


# ---------------------------------------------------------------------------
# Face playback state (speaking / listening / envelope)
# ---------------------------------------------------------------------------

class _FaceState:
    """Thread-safe shared state between AudioIO.play() and the HTTP handler."""

    def __init__(self):
        self._lock = threading.Lock()
        self._speaking = False
        self._envelope: list[float] = []
        self._duration_s: float = 0.0
        self._started_at: float = 0.0
        self._listening = False
        self._thinking = False

    def start_speaking(self, envelope: list[float], duration_s: float, start_at: Optional[float] = None):
        with self._lock:
            self._speaking = True
            self._envelope = envelope
            self._duration_s = duration_s
            self._started_at = start_at if start_at is not None else time.time()

    def stop_speaking(self):
        with self._lock:
            self._speaking = False

    def set_listening(self, is_listening: bool):
        with self._lock:
            self._listening = is_listening

    def set_thinking(self, is_thinking: bool):
        """Set while Q2 is processing/calling the LLM after recording stops."""
        with self._lock:
            self._thinking = is_thinking

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "speaking": self._speaking,
                "envelope": self._envelope,
                "duration_s": self._duration_s,
                "started_at": self._started_at,
                "listening": self._listening,
                "thinking": self._thinking,
            }


face_state = _FaceState()


# ---------------------------------------------------------------------------
# Settings state — runtime config readable/writable by the settings page
# ---------------------------------------------------------------------------

class _SettingsState:
    """
    Runtime settings bridge between the settings HTML page (via HTTP POST)
    and Q2's live config + voice loop. All mutations are thread-safe.
    Visualizer colours are stored here so index.html can poll and apply
    them without a page reload.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._restart_as_text_mode = False
        # Visualizer colours — loaded from config on first GET
        self._face_style   = config.get("face.style",       7)
        self._bg_colour    = config.get("face.bg_colour",    "#0d1b4c")
        self._bar_colour_1 = config.get("face.bar_colour_1", "#ff2fb0")
        self._bar_colour_2 = config.get("face.bar_colour_2", "#f5f5ff")
        # Pending changes for the voice loop to apply
        self._pending: dict = {}

    def get(self) -> dict:
        from config.loader import config
        from personality.dials import DIAL_BANDS
        from personality.builder import _resolve_profile_dials
        import subprocess

        # Fetch available sinks from pactl for the output device dropdown
        try:
            result = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True, text=True, timeout=3
            )
            sinks = [line.split("\t")[1] for line in result.stdout.strip().splitlines() if line]
        except Exception:
            sinks = []

        # Resolve current dial values from the active profile
        dials = _resolve_profile_dials(config.profile)
        dial_values = {name: getattr(dials, name) for name in DIAL_BANDS}
        dial_values["probability_narration"] = dials.probability_narration
        dial_values["wellness_checkins"] = dials.wellness_checkins

        # Get all preset names for the "save as" dropdown
        try:
            from personality.presets import PRESETS
            preset_names = list(PRESETS.keys())
        except Exception:
            preset_names = []

        # Available input devices for the talk button device selector
        try:
            import evdev as _evdev
            input_devices = [
                {"name": _evdev.InputDevice(p).name, "path": p}
                for p in _evdev.list_devices()
                if _evdev.InputDevice(p).name not in ("", "?")
            ]
        except Exception:
            input_devices = []

        with self._lock:
            return {
                "wake_word_enabled": config.get("voice.wake_word.enabled", False),
                "wake_word_sensitivity": config.get("voice.wake_word.sensitivity", 0.5),
                "talk_button_key": config.get("voice.talk_button.key", "g"),
                "talk_button_device": config.get("voice.talk_button.device_name", ""),
                "available_input_devices": input_devices,
                "active_profile": config.profile.get("name", "Q2"),
                "llm_backend":    config.get("llm.backend", "claude"),
                "available_profiles": config.list_profiles(),
                "available_presets": preset_names,
                "output_device": config.get("voice.output_device", "pipewire"),
                "tts_voice": config.get("voice.deepgram_tts.model", "aura-2-pluto-en"),
                "face_style": config.get("face.style", 7),
                "available_sinks": sinks,
                "bg_colour": self._bg_colour,
                "bar_colour_1": self._bar_colour_1,
                "bar_colour_2": self._bar_colour_2,
                "restart_as_text_mode": self._restart_as_text_mode,
                "dials": dial_values,
                "input_device": config.get("voice.input_device", "default"),
                "input_device_options": config.get("voice.input_device_options", []),
                "shipping": {
                    "preferred_services": config.get("purchasing.shipping.preferred_services",
                                                     ["Canada Post Xpresspost", "Canada Post"]),
                    "fallback": config.get("purchasing.shipping.fallback", "cheapest"),
                    "confirm_above_cad": config.get("purchasing.shipping.confirm_above_cad", 25.00),
                    "name": config.get("purchasing.shipping.address.name", ""),
                    "line1": config.get("purchasing.shipping.address.line1", ""),
                    "line2": config.get("purchasing.shipping.address.line2", ""),
                    "city": config.get("purchasing.shipping.address.city", ""),
                    "province": config.get("purchasing.shipping.address.province", ""),
                    "postal_code": config.get("purchasing.shipping.address.postal_code", ""),
                    "phone": config.get("purchasing.shipping.address.phone", ""),
                },
            }

    def apply(self, changes: dict):
        from config.loader import config
        with self._lock:
            if "wake_word_enabled" in changes:
                config.raw.setdefault("voice", {}).setdefault("wake_word", {})["enabled"] = changes["wake_word_enabled"]
                log.info(f"Settings: wake_word_enabled -> {changes['wake_word_enabled']}")
            if "wake_word_sensitivity" in changes:
                val = max(0.0, min(1.0, float(changes["wake_word_sensitivity"])))
                config.raw.setdefault("voice", {}).setdefault("wake_word", {})["sensitivity"] = val
                log.info(f"Settings: wake_word_sensitivity -> {val}")
            if "talk_button_key" in changes:
                key = str(changes["talk_button_key"]).strip().lower()[:1]
                if key:
                    config.raw.setdefault("voice", {}).setdefault("talk_button", {})["key"] = key
                    log.info(f"Settings: talk_button_key -> {key}")
            if "talk_button_device" in changes:
                config.raw.setdefault("voice", {}).setdefault("talk_button", {})["device_name"] = changes["talk_button_device"]
                log.info(f"Settings: talk_button_device -> {changes['talk_button_device']}")
            if "active_profile" in changes:
                try:
                    config.load_profile(changes["active_profile"])
                    log.info(f"Settings: profile -> {changes['active_profile']}")
                except Exception as e:
                    log.warning(f"Settings: profile switch failed: {e}")
            if "llm_backend" in changes:
                backend = str(changes["llm_backend"]).strip()
                config.raw.setdefault("llm", {})["backend"] = backend
                config.save()
                log.info(f"Settings: LLM backend -> {backend} (restart required)")
            if "output_device" in changes:
                import subprocess
                try:
                    subprocess.run(
                        ["pactl", "set-default-sink", changes["output_device"]],
                        timeout=3, check=True
                    )
                    config.raw.setdefault("voice", {})["output_device"] = changes["output_device"]
                    log.info(f"Settings: output_device -> {changes['output_device']}")
                except Exception as e:
                    log.warning(f"Settings: output device switch failed: {e}")

            # Dial values — apply live to the active profile's overrides so
            # the next LLM call picks them up immediately without restarting.
            if "dials" in changes:
                from personality.dials import DIAL_BANDS
                dial_changes = changes["dials"]
                profile_overrides = config.profile.setdefault("dial_overrides", {})
                for name in DIAL_BANDS:
                    if name in dial_changes:
                        val = max(0, min(100, int(dial_changes[name])))
                        profile_overrides[name] = val
                if "probability_narration" in dial_changes:
                    profile_overrides["probability_narration"] = bool(dial_changes["probability_narration"])
                if "wellness_checkins" in dial_changes:
                    profile_overrides["wellness_checkins"] = str(dial_changes["wellness_checkins"])
                log.info(f"Settings: dials updated — {list(dial_changes.keys())}")

            # Apply a named preset directly to the active profile's dial overrides
            if "apply_preset" in changes:
                preset_name = str(changes["apply_preset"]).strip()
                try:
                    from personality.presets import get_preset
                    from personality.dials import DIAL_BANDS
                    preset = get_preset(preset_name)
                    overrides = config.profile.setdefault("dial_overrides", {})
                    for name in DIAL_BANDS:
                        overrides[name] = getattr(preset, name)
                    overrides["probability_narration"] = preset.probability_narration
                    overrides["wellness_checkins"]     = preset.wellness_checkins
                    log.info(f"Settings: applied preset '{preset_name}' to active profile")
                except Exception as e:
                    log.warning(f"Settings: failed to apply preset '{preset_name}': {e}")
                    raise

            # Save as new named preset
            if "save_preset_as" in changes:
                preset_name = str(changes["save_preset_as"]).strip()
                if preset_name:
                    self._save_preset(preset_name, config.profile.get("dial_overrides", {}),
                                      config.profile.get("dial_preset", "Q2"))

            if "bg_colour" in changes:
                self._bg_colour = changes["bg_colour"]
                config.raw.setdefault("face", {})["bg_colour"] = self._bg_colour
            if "bar_colour_1" in changes:
                self._bar_colour_1 = changes["bar_colour_1"]
                config.raw.setdefault("face", {})["bar_colour_1"] = self._bar_colour_1
            if "bar_colour_2" in changes:
                self._bar_colour_2 = changes["bar_colour_2"]
                config.raw.setdefault("face", {})["bar_colour_2"] = self._bar_colour_2
            if any(k in changes for k in ("bg_colour", "bar_colour_1", "bar_colour_2")):
                config.save()
                log.info(f"Face colours saved to config")
            if changes.get("restart_as_text_mode"):
                self._restart_as_text_mode = True
                log.info("Settings: restart as text mode requested")

            # Shipping preferences
            if "input_device" in changes:
                device_name = str(changes["input_device"]).strip()
                config.raw.setdefault("voice", {})["input_device"] = device_name
                # Also update sample_rate to match the device's preferred rate
                for opt in config.get("voice.input_device_options", []):
                    if opt.get("name", "").lower() in device_name.lower():
                        sr = opt.get("sample_rate")
                        if sr:
                            config.raw.setdefault("voice", {})["sample_rate"] = sr
                            log.info(f"Settings: input_device -> {device_name} (sample_rate -> {sr}Hz)")
                        break
                else:
                    log.info(f"Settings: input_device -> {device_name}")
                config.save()

            if "face_style" in changes:
                style = int(changes["face_style"])
                self._face_style = style
                config.raw.setdefault("face", {})["style"] = style
                config.save()
                log.info(f"Settings: face_style -> {style}")

            if "tts_voice" in changes:
                model = str(changes["tts_voice"]).strip()
                if model:
                    config.raw.setdefault("voice", {}).setdefault("deepgram_tts", {})["model"] = model
                    config.save()
                    log.info(f"Settings: tts_voice -> {model}")

            if "shipping" in changes:
                s = changes["shipping"]
                shipping = config.raw.setdefault("purchasing", {}).setdefault("shipping", {})
                addr = shipping.setdefault("address", {})
                if "preferred_services" in s:
                    # Accept either a list or a newline-separated string from the textarea
                    val = s["preferred_services"]
                    if isinstance(val, str):
                        val = [v.strip() for v in val.splitlines() if v.strip()]
                    shipping["preferred_services"] = val
                if "fallback" in s:
                    shipping["fallback"] = s["fallback"]
                if "confirm_above_cad" in s:
                    shipping["confirm_above_cad"] = max(0.0, float(s["confirm_above_cad"]))
                for field in ("name", "line1", "line2", "city", "province", "postal_code", "phone"):
                    if field in s:
                        addr[field] = str(s[field])
                log.info("Settings: shipping preferences updated")

    def _save_preset(self, name: str, dial_overrides: dict, base_preset: str):
        """Persist a new named preset to personality/presets.py at runtime."""
        try:
            from personality.dials import PersonalityDials
            from personality.presets import PRESETS, get_preset
            base = get_preset(base_preset)
            merged = {k: getattr(base, k) for k in PersonalityDials.__dataclass_fields__}
            merged.update(dial_overrides)
            new_preset = PersonalityDials.from_dict(merged)
            PRESETS[name] = new_preset
            log.info(f"Settings: preset '{name}' saved to runtime registry")
            # Also write to presets.py so it persists across restarts
            self._write_preset_to_file(name, merged)
        except Exception as e:
            log.warning(f"Settings: failed to save preset '{name}': {e}")

    def _write_preset_to_file(self, name: str, dial_values: dict):
        """Append a new preset entry to personality/presets.py."""
        try:
            from pathlib import Path
            presets_path = Path(__file__).parent.parent / "personality" / "presets.py"
            content = presets_path.read_text()
            # Build the new preset entry
            numeric_dials = {k: v for k, v in dial_values.items()
                           if isinstance(v, int) and k not in ('probability_narration', 'wellness_checkins')}
            bool_dials = {k: v for k, v in dial_values.items() if isinstance(v, bool)}
            str_dials = {k: v for k, v in dial_values.items() if isinstance(v, str)}
            lines = [f'\n    "{name}": PersonalityDials.from_dict({{']
            for k, v in numeric_dials.items():
                lines.append(f'        "{k}": {v},')
            for k, v in bool_dials.items():
                lines.append(f'        "{k}": {str(v)},')
            for k, v in str_dials.items():
                lines.append(f'        "{k}": "{v}",')
            lines.append('    }),')
            entry = '\n'.join(lines)
            # Insert before the closing brace of PRESETS dict
            content = content.replace('\n}\n', entry + '\n}\n')
            presets_path.write_text(content)
            log.info(f"Settings: preset '{name}' written to presets.py")
        except Exception as e:
            log.warning(f"Settings: failed to write preset to file: {e}")

    def consume_restart_request(self) -> bool:
        """Returns True once if a text-mode restart was requested, then clears it."""
        with self._lock:
            if self._restart_as_text_mode:
                self._restart_as_text_mode = False
                return True
            return False

    def get_colours(self) -> dict:
        with self._lock:
            return {
                "face_style": self._face_style,
                "bg_colour": self._bg_colour,
                "bar_colour_1": self._bar_colour_1,
                "bar_colour_2": self._bar_colour_2,
            }


settings_state = _SettingsState()


# ---------------------------------------------------------------------------
# Ledger helpers — called by the HTTP handler for /ledger GET and POST
# ---------------------------------------------------------------------------

def _get_ledger_data() -> dict:
    """Return gift card balances and recent purchase history for the settings page."""
    try:
        from purchasing.ledger import BudgetLedger
        ledger = BudgetLedger()
        cards = ledger.list_gift_cards(include_inactive=False)
        history = ledger.get_purchase_history(limit=20)
        total = ledger.total_available_balance()
        ledger.close()
        # Mask card codes — show only last 4 chars in the list view for safety;
        # the full code is only ever retrieved programmatically during checkout,
        # not displayed in the UI where it could be shoulder-surfed.
        for card in cards:
            code = card.get("card_code", "")
            card["card_code_masked"] = f"****{code[-4:]}" if len(code) >= 4 else ("****" if code else "")
            del card["card_code"]  # never send raw code to the browser
            # payment_type is safe to send — it's not a credential
        return {"ok": True, "cards": cards, "history": history, "total_balance": total}
    except Exception as e:
        return {"ok": False, "error": str(e), "cards": [], "history": [], "total_balance": 0}


def _apply_ledger_action(action: dict) -> dict:
    """Handle add_card and deactivate_card actions from the settings page."""
    try:
        from purchasing.ledger import BudgetLedger
        ledger = BudgetLedger()
        cmd = action.get("cmd")

        if cmd == "add_card":
            label        = str(action.get("label", "")).strip()
            amount       = float(action.get("amount", 0))
            code         = str(action.get("card_code", "")).strip()
            payment_type = str(action.get("payment_type", "site_gc")).strip()
            if not label:
                return {"ok": False, "error": "Label is required."}
            if amount <= 0:
                return {"ok": False, "error": "Amount must be greater than zero."}
            card_id = ledger.add_gift_card(label=label, amount=amount,
                                           card_code=code, payment_type=payment_type)
            ledger.close()
            log.info(f"Ledger: card added via settings — '{label}' ${amount:.2f} [{payment_type}] (id={card_id})")
            return {"ok": True, "card_id": card_id}

        elif cmd == "deactivate_card":
            card_id = int(action.get("card_id", 0))
            ledger.deactivate_gift_card(card_id)
            ledger.close()
            log.info(f"Ledger: card {card_id} deactivated via settings")
            return {"ok": True}

        else:
            ledger.close()
            return {"ok": False, "error": f"Unknown ledger command: {cmd}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _FaceRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default request logging

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_file(INDEX_PATH, "text/html")
        elif self.path == "/settings.html":
            self._serve_file(SETTINGS_PATH, "text/html")
        elif self.path == "/state":
            data = face_state.snapshot()
            data.update(settings_state.get_colours())
            self._serve_json(data)
        elif self.path == "/settings":
            self._serve_json(settings_state.get())
        elif self.path == "/ledger":
            self._serve_json(_get_ledger_data())
        elif self.path.startswith("/photo"):
            # Full-screen display of a saved photo file
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            file_path = qs.get("file", [""])[0]
            try:
                from pathlib import Path as _Path
                import base64 as _b64
                img = _Path(file_path).read_bytes()
                b64 = _b64.b64encode(img).decode()
                ext = _Path(file_path).suffix.lower()
                mime = "image/png" if ext == ".png" else "image/jpeg"
                html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  html,body{{margin:0;padding:0;background:#000;width:100vw;height:100vh;
             display:flex;align-items:center;justify-content:center;overflow:hidden}}
  img{{max-width:100vw;max-height:100vh;object-fit:contain}}
  #overlay{{position:fixed;bottom:20px;right:20px;color:rgba(255,255,255,0.5);
            font-family:monospace;font-size:12px;text-align:right}}
</style></head>
<body>
<img src="data:{mime};base64,{b64}">
<div id="overlay">{_Path(file_path).name}<br>H — back to face</div>
<script>
  document.addEventListener('keydown', e => {{
    if (e.key.toLowerCase() === 'h') location.href = '/';
  }});
</script>
</body></html>"""
                self._serve_html(html)
            except Exception as e:
                self._serve_html(f"<h2 style='color:#f55;font-family:monospace'>Photo error: {e}</h2>")

        elif self.path.startswith("/camera/photo"):
            # Full-screen photo page — captures a fresh frame and displays it.
            # Auto-returns to the face after 10 seconds (or press H to go back).
            try:
                from integrations.webcam import webcam
                if not webcam.is_running:
                    webcam.start()
                    import time; time.sleep(0.4)
                jpeg = webcam.grab_jpeg(1920, 1080, quality=92)
                if not jpeg:
                    self._serve_html("<h2 style='color:#fff;font-family:monospace'>No frame available</h2>")
                    return
                import base64
                b64 = base64.b64encode(jpeg).decode()
                html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  html,body{{margin:0;padding:0;background:#000;width:100vw;height:100vh;
             display:flex;align-items:center;justify-content:center;overflow:hidden}}
  img{{max-width:100vw;max-height:100vh;object-fit:contain}}
  #overlay{{position:fixed;bottom:20px;right:20px;color:rgba(255,255,255,0.4);
            font-family:monospace;font-size:13px;text-align:right}}
  #bar{{position:fixed;bottom:0;left:0;height:3px;background:#ff2fb0;
        transition:width linear}}
</style></head>
<body>
<img src="data:image/jpeg;base64,{b64}">
<div id="overlay">H — back to face<br><span id="countdown">10</span>s</div>
<div id="bar" style="width:100%"></div>
<script>
  let t = 10;
  const bar = document.getElementById('bar');
  const cd  = document.getElementById('countdown');
  const iv = setInterval(() => {{
    t--;
    cd.textContent = t;
    bar.style.width = (t/10*100) + '%';
    if (t <= 0) {{ clearInterval(iv); location.href = '/'; }}
  }}, 1000);
  document.addEventListener('keydown', e => {{
    if (e.key.toLowerCase() === 'h') {{ clearInterval(iv); location.href = '/'; }}
  }});
</script>
</body></html>"""
                self._serve_html(html)
            except Exception as e:
                self._serve_html(f"<h2 style='color:#f55;font-family:monospace'>Camera error: {e}</h2>")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                changes = json.loads(body)
                settings_state.apply(changes)
                self._serve_json({"ok": True})
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        elif self.path == "/ledger":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                action = json.loads(body)
                result = _apply_ledger_action(action)
                self._serve_json(result)
            except Exception as e:
                self._serve_json({"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        # Allow cross-origin requests from the kiosk pages
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_html(self, html: str):
        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _serve_file(self, path: Path, content_type: str):
        try:
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _serve_json(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server_instance: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None


def start_face_server(port: int = 8765):
    global _server_instance, _server_thread
    if _server_instance is not None:
        log.warning("Face server already running.")
        return
    _server_instance = ThreadingHTTPServer(("127.0.0.1", port), _FaceRequestHandler)
    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    _server_thread.start()
    log.info(f"Face server running at http://127.0.0.1:{port}")


def stop_face_server():
    global _server_instance
    if _server_instance is not None:
        _server_instance.shutdown()
        _server_instance = None
        log.info("Face server stopped.")
