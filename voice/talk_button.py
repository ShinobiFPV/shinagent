"""
IMQ2 Talk Button / Controller Bridge
Wires the 8BitDo Zero 2 (in gamepad mode, via voice/controller.py's
ControllerManager) to the voice pipeline and agent. Replaces the old
keyboard-mode approach entirely -- see voice/controller.py's module
docstring and README's "Voice Modes" section for why gamepad mode
replaced it (Bluetooth keyboard-mode events could land in whatever text
field had focus in the web app; gamepad mode reads real button/D-pad
events instead).

TalkButtonState is unchanged from the keyboard-mode version -- it's the
same toggle abstraction consumed throughout voice/pipeline.py
(record_utterance_button et al.) and main.py's turn loop, so PTT keeps
its exact existing "press once to start, press again to stop" semantics
regardless of which physical input triggers it.
"""

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)


class TalkButtonState:
    """Thread-safe toggle state, set by the background controller listener,
    consumed by the voice loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._toggle_pending = False

    def signal_toggle(self):
        with self._lock:
            self._toggle_pending = True

    def consume_toggle(self) -> bool:
        """Returns True exactly once per actual toggle event, then clears it."""
        with self._lock:
            if self._toggle_pending:
                self._toggle_pending = False
                return True
            return False


# Module-level singleton — imported by both the controller bridge and the voice loop
talk_button_state = TalkButtonState()

# Set once ControllerBridge() is constructed (main.py's start_controller_bridge()).
# Callers that just need to trigger PTT (e.g. face/server.py's web-app PTT route)
# should prefer talk_button_state.signal_toggle() directly -- it works even if a
# ControllerBridge was never constructed. get_talk_button() exists for callers
# that need the bridge instance itself.
_instance: Optional["ControllerBridge"] = None

def get_talk_button() -> Optional["ControllerBridge"]:
    return _instance


class ControllerBridge:
    """
    Wires voice/controller.py's ControllerManager actions to real Q2
    hooks: PTT toggles talk_button_state (same mechanism the old
    keyboard-mode listener used), cancel/repeat/volume act on the given
    AudioIO instance, mode_switch cycles personality profiles, and nav_*
    actions are published to face/server.py for the web app's D-pad
    navigation overlay.
    """

    def __init__(self, audio):
        self._audio = audio
        self._ctrl = None
        self._flipper = None
        self._multi = None
        global _instance
        _instance = self

    def start(self) -> bool:
        """Returns True if any controller path actually started --
        8BitDo BT (evdev, Linux only) and/or the Flipper Zero WiFi bridge
        (any platform, needs `websockets` installed) -- so the caller can
        fall back to Enter-key push-to-talk only if neither is available.
        P2-P4 USB player controllers (evdev, Linux only) are additive and
        don't affect this return value -- they have no PTT of their own,
        see voice/multi_controller.py's module docstring."""
        from config.loader import config
        cfg = config.raw
        started = False

        # ── 8BitDo BT controller (Linux/evdev only) ────────
        from voice.controller import init_controller, EVDEV_AVAILABLE
        if EVDEV_AVAILABLE:
            ctrl = init_controller(cfg)
            self._ctrl = ctrl

            ctrl.on_action("ptt",         self._ptt)
            ctrl.on_action("cancel",      self._cancel)
            ctrl.on_action("repeat",      self._repeat)
            ctrl.on_action("volume_up",   self._vol_up)
            ctrl.on_action("volume_down", self._vol_dn)
            ctrl.on_action("mode_switch", self._mode_switch)
            for _nav_action in ("nav_up", "nav_down", "nav_left", "nav_right",
                                 "nav_confirm", "nav_back", "nav_toggle"):
                ctrl.on_action(_nav_action, lambda et, a=_nav_action: self._nav_event(et, a))
            ctrl.on_action("toggle_menu",   self._toggle_menu)
            ctrl.on_action("cycle_face",    self._cycle_face)
            ctrl.on_action("mode_select",   self._mode_select)
            ctrl.on_any(self._any_event)

            ctrl.start()
            started = True

        # ── Flipper Zero WiFi Master Controller (any platform) ─────
        # Preferred over BT when connected -- see voice/multi_controller.py's
        # MultiControllerManager.get_all_states() for the priority logic
        # the settings page reads. Wired identically to the BT controller
        # so the rest of the system (PTT, nav, toggle_menu, ...) doesn't
        # care which one actually fired.
        from voice.controller_server import init_flipper_server, WEBSOCKETS_AVAILABLE
        if cfg.get("flipper", {}).get("enabled", True) and WEBSOCKETS_AVAILABLE:
            flipper = init_flipper_server(cfg)
            self._flipper = flipper

            flipper.on_action("ptt",         self._ptt)
            flipper.on_action("cancel",      self._cancel)
            flipper.on_action("repeat",      self._repeat)
            flipper.on_action("volume_up",   self._vol_up)
            flipper.on_action("volume_down", self._vol_dn)
            for _nav_action in ("nav_up", "nav_down", "nav_left", "nav_right",
                                 "nav_confirm", "nav_back", "nav_toggle"):
                flipper.on_action(_nav_action, lambda et, a=_nav_action: self._nav_event(et, a))
            flipper.on_action("toggle_menu", self._toggle_menu)
            flipper.on_action("cycle_face",  self._cycle_face)
            flipper.on_action("mode_select", self._mode_select)
            flipper.on_any(self._any_event)

            flipper.start()
            started = True
        elif cfg.get("flipper", {}).get("enabled", True) and not WEBSOCKETS_AVAILABLE:
            log.warning("Flipper controller enabled in config but 'websockets' isn't installed -- pip install websockets")

        # ── P2-P4 USB player controllers (Linux/evdev only) ────────
        # Game input only, no PTT -- see voice/multi_controller.py.
        if EVDEV_AVAILABLE:
            from voice.multi_controller import init_multi_controller
            multi = init_multi_controller(cfg)
            self._multi = multi
            for pid in ("p2", "p3", "p4"):
                path = cfg.get("controllers", {}).get(pid, {}).get("device_path", "")
                if path:
                    multi.add_player(pid, path)

        return started

    def stop(self):
        if self._ctrl:
            self._ctrl.stop()
        if self._flipper:
            self._flipper.stop()
        if self._multi:
            for pid in ("p2", "p3", "p4"):
                self._multi.remove_player(pid)

    # ── Action handlers ───────────────────────────────────

    def _ptt(self, event_type: str):
        # Toggle semantics (not hold-to-talk): the first press starts
        # recording, the second press stops it -- matching the old
        # keyboard-mode behavior exactly, since holding a gamepad button
        # through an entire sentence is uncomfortable. Only the press
        # edge signals a toggle; release is a no-op.
        if event_type == "press":
            talk_button_state.signal_toggle()

    def _cancel(self, event_type: str):
        if event_type != "press":
            return
        what = self._audio.cancel_current()
        if what != "nothing":
            log.info(f"Controller: cancelled {what}")

    def _repeat(self, event_type: str):
        if event_type == "press":
            self._audio.replay_last()

    def _vol_up(self, event_type: str):
        if event_type == "press":
            self._audio.adjust_volume(+0.1)

    def _vol_dn(self, event_type: str):
        if event_type == "press":
            self._audio.adjust_volume(-0.1)

    def _mode_switch(self, event_type: str):
        if event_type != "hold":
            return
        from pathlib import Path
        from config.loader import config

        profiles = sorted(config.list_profiles())
        if not profiles:
            return
        current = Path(config.get("agent.active_profile", "profiles/q2_default.yaml")).stem
        idx = profiles.index(current) if current in profiles else -1
        next_profile = profiles[(idx + 1) % len(profiles)]
        try:
            config.load_profile(next_profile)
            config.save()
            log.info(f"Controller: mode switch -> {next_profile}")
            self._notify_webapp_reload()
        except Exception as e:
            log.warning(f"Controller: mode switch failed: {e}")

    @staticmethod
    def _notify_webapp_reload():
        """Same best-effort ping used by face/server.py's Settings-panel
        profile switch -- the webapp runs as a separate process with its
        own agent/config singleton, so it needs telling explicitly."""
        try:
            import requests
            from config.loader import config
            port = config.get("webapp.port", 8766)
            requests.post(f"http://127.0.0.1:{port}/reload-personality", timeout=2)
        except Exception:
            pass

    def _toggle_menu(self, event_type: str):
        if event_type != "press":
            return
        try:
            from face.server import emit_controller_event
            emit_controller_event({"type": "ui", "action": "toggle_menu"})
        except Exception:
            pass

    def _cycle_face(self, event_type: str):
        if event_type != "press":
            return
        try:
            from face.server import emit_controller_event, settings_state
            style = settings_state.cycle_face_style()
            emit_controller_event({"type": "ui", "action": "cycle_face", "style": style})
        except Exception:
            pass

    def _nav_event(self, event_type: str, action: str):
        try:
            from face.server import emit_controller_event
            state = self._ctrl.get_state() if self._ctrl else {}
            emit_controller_event({
                "type":       "nav",
                "action":     action,
                "event_type": event_type,
                "nav_mode":   state.get("nav_mode", False),
                "dpad":       state.get("dpad", {}),
            })
        except Exception:
            pass

    def _mode_select(self, event_type: str):
        if event_type != "press":
            return
        try:
            from face.server import emit_controller_event
            emit_controller_event({"type": "ui", "action": "mode_select"})
        except Exception:
            pass

    def _any_event(self, action: str, event_type: str):
        # Publish ALL events to face server for the settings page's live
        # button-test display.
        try:
            from face.server import emit_controller_event
            emit_controller_event({
                "type":       "button",
                "action":     action,
                "event_type": event_type,
            })
        except Exception:
            pass


def start_controller_bridge(audio) -> Optional[ControllerBridge]:
    """
    Start the gamepad-mode controller bridge in a background thread.
    Returns the bridge on success, or None if evdev/the platform doesn't
    support it (Windows/Mac -- the HUD is the interaction surface there)
    or the controller can't be reached, so the caller can fall back to
    Enter-key push-to-talk.
    """
    try:
        bridge = ControllerBridge(audio)
        if bridge.start():
            return bridge
        return None
    except Exception as e:
        log.warning(f"Controller bridge failed to start ({e}) — falling back to Enter key push-to-talk.")
        return None
