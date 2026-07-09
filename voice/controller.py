"""
Q2 Controller Input System
===========================
Reads the 8BitDo Zero 2 in Android/gamepad mode via evdev. Replaces the
old keyboard-mode approach (voice/talk_button.py's original
TalkButtonListener) entirely -- gamepad mode means real button/D-pad
events instead of emulated keystrokes, so there's no more risk of the
controller's keypresses landing in whatever text field happens to have
focus in the web app.

In gamepad mode the Zero 2 presents these events:
  EV_KEY: BTN_SOUTH(304)=A  BTN_EAST(305)=B
          BTN_NORTH(307)=X  BTN_WEST(308)=Y
          BTN_TL(310)=L     BTN_TR(311)=R
          BTN_SELECT(314)   BTN_START(315)
  EV_ABS: ABS_X(0)/ABS_Y(1)       = D-pad in stick mode
          ABS_HAT0X(16)/ABS_HAT0Y(17) = D-pad in hat mode

D-pad is left analog stick by default. Press LEFT+SELECT on the
controller to switch to hat mode (recommended -- see README).
"""

import sys
import threading
import time
from pathlib import Path
from typing import Optional, Callable, Dict

# evdev is Linux-only -- on Windows/Mac the HUD is the interaction surface
# instead, so this module simply reports "not available" there.
if sys.platform != 'linux':
    EVDEV_AVAILABLE = False
else:
    try:
        from evdev import InputDevice, list_devices
        EVDEV_AVAILABLE = True
    except ImportError:
        EVDEV_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────

BTN_CODES = {
    304: "A",
    305: "B",
    307: "X",
    308: "Y",
    310: "L",
    311: "R",
    314: "SELECT",
    315: "START",
}

EV_KEY    = 1
EV_ABS    = 3
ABS_X     = 0
ABS_Y     = 1
ABS_HAT0X = 16
ABS_HAT0Y = 17

DPAD_THRESHOLD = 0.5

DEFAULT_MAPPING = {
    "A":          "ptt",
    "B":          "cancel",
    "X":          "repeat",
    "Y":          "unassigned",
    "L":          "volume_down",
    "R":          "volume_up",
    "SELECT":     "nav_toggle",
    "START":      "mode_switch",
    "DPAD_UP":    "nav_up",
    "DPAD_DOWN":  "nav_down",
    "DPAD_LEFT":  "nav_left",
    "DPAD_RIGHT": "nav_right",
}

DEVICE_NAMES = [
    "8bitdo zero 2 gamepad",
    "8bitdo zero2 gamepad",
    "zero 2 gamepad",
]


class ControllerState:
    def __init__(self):
        self.buttons: Dict[str, bool] = {b: False for b in BTN_CODES.values()}
        self.dpad     = {"x": 0, "y": 0}
        self.nav_mode = False


class ControllerManager:
    """
    Manages the 8BitDo Zero 2 in gamepad mode. Auto-detects the device,
    handles reconnection (controller sleep/battery-out/re-pair), and
    fires action callbacks on button/D-pad events per the configured
    mapping.
    """

    def __init__(self, config: dict = None):
        self._config      = config or {}
        self._mapping     = dict(DEFAULT_MAPPING)
        self._device      = None
        self._device_path = None
        self._dpad_mode   = "unknown"
        self._running     = False
        self._thread      = None
        self._state       = ControllerState()
        self._lock        = threading.Lock()
        self._callbacks: Dict[str, Callable] = {}
        self._press_times: Dict[str, float] = {}
        self._hold_fired: Dict[str, bool] = {}
        self._hold_timers: Dict[str, threading.Timer] = {}
        self._hold_ms = int(self._config.get("controller", {}).get("hold_ms", 1000))

        saved = self._config.get("controller", {}).get("mapping", {})
        self._mapping.update(saved)

    # ── Public ────────────────────────────────────────────

    def on_action(self, action: str, cb: Callable):
        self._callbacks[action] = cb

    def on_any(self, cb: Callable):
        self._callbacks["__any__"] = cb

    def start(self):
        if not EVDEV_AVAILABLE:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        dev = self._device
        if dev:
            try:
                dev.close()
            except Exception:
                pass

    def get_state(self) -> dict:
        with self._lock:
            return {
                "connected": self._device is not None,
                "device":    self._device_path,
                "dpad_mode": self._dpad_mode,
                "nav_mode":  self._state.nav_mode,
                "dpad":      dict(self._state.dpad),
                "buttons":   dict(self._state.buttons),
                "mapping":   dict(self._mapping),
            }

    def update_mapping(self, new_mapping: dict):
        with self._lock:
            self._mapping.update(new_mapping)

    def set_nav_mode(self, enabled: bool):
        """Explicit set (vs. the SELECT-button toggle in _dispatch) so a
        modal UI -- e.g. face/index.html's mode-select overlay -- can force
        A/B into nav_confirm/nav_back for as long as it's open and restore
        plain ptt/cancel on close, without racing a toggle against whatever
        nav_mode was already doing for the mobile PWA's own D-pad nav."""
        with self._lock:
            self._state.nav_mode = bool(enabled)

    def detect_device(self) -> Optional[str]:
        if not EVDEV_AVAILABLE:
            return None

        # Try the udev symlink first (see README's udev rule) -- stable
        # across reboots/reconnects regardless of enumeration order.
        if Path("/dev/input/gamepad").exists():
            return "/dev/input/gamepad"

        explicit = self._config.get("controller", {}).get("device_path", "")
        if explicit and Path(explicit).exists():
            return explicit

        if not self._config.get("controller", {}).get("auto_detect", True):
            return None

        try:
            for path in list_devices():
                try:
                    dev = InputDevice(path)
                    name = dev.name.lower()
                    dev.close()
                    if any(n in name for n in DEVICE_NAMES):
                        return path
                except Exception:
                    continue
        except Exception:
            pass

        return None

    def _detect_dpad_mode(self) -> str:
        if not self._device:
            return "unknown"
        try:
            caps = self._device.capabilities()
            abs_codes = [c for c, _ in caps.get(EV_ABS, [])]
            if ABS_HAT0X in abs_codes:
                return "hat"
            if ABS_X in abs_codes:
                return "axis"
        except Exception:
            pass
        return "unknown"

    # ── Main loop ─────────────────────────────────────────

    def _run(self):
        """Main loop with auto-reconnect -- covers the controller going to
        sleep after ~15min idle or running out of battery. Reconnects
        automatically once re-paired/woken, no restart needed."""
        while self._running:
            path = self.detect_device()

            if not path:
                time.sleep(2.0)
                continue

            try:
                with self._lock:
                    self._device = InputDevice(path)
                    self._device_path = path
                    self._dpad_mode = self._detect_dpad_mode()

                for event in self._device.read_loop():
                    if not self._running:
                        break
                    self._handle_event(event)

            except Exception:
                pass
            finally:
                with self._lock:
                    self._device = None
                    self._device_path = None
                time.sleep(1.0)

    def _handle_event(self, event):
        if event.type == EV_KEY:
            name = BTN_CODES.get(event.code)
            if not name:
                return
            if event.value == 1:
                self._on_press(name)
            elif event.value == 0:
                self._on_release(name)

        elif event.type == EV_ABS:
            self._handle_abs(event)

    def _handle_abs(self, event):
        with self._lock:
            prev_x = self._state.dpad["x"]
            prev_y = self._state.dpad["y"]

            if self._dpad_mode == "hat":
                if event.code == ABS_HAT0X:
                    v = event.value
                    self._state.dpad["x"] = -1 if v < 0 else 1 if v > 0 else 0
                elif event.code == ABS_HAT0Y:
                    v = event.value
                    self._state.dpad["y"] = -1 if v < 0 else 1 if v > 0 else 0

            elif self._dpad_mode == "axis":
                if event.code in (ABS_X, ABS_Y):
                    try:
                        ai = self._device.absinfo(event.code)
                        mid = (ai.max + ai.min) / 2
                        rng = (ai.max - ai.min) / 2
                        norm = (event.value - mid) / rng if rng else 0
                    except Exception:
                        norm = (event.value - 128) / 128.0

                    val = -1 if norm < -DPAD_THRESHOLD else 1 if norm > DPAD_THRESHOLD else 0

                    if event.code == ABS_X:
                        self._state.dpad["x"] = val
                    else:
                        self._state.dpad["y"] = val

            nx, ny = self._state.dpad["x"], self._state.dpad["y"]

        if nx != prev_x:
            if nx == -1:
                self._fire_dpad("DPAD_LEFT")
            elif nx == 1:
                self._fire_dpad("DPAD_RIGHT")

        if ny != prev_y:
            if ny == -1:
                self._fire_dpad("DPAD_UP")
            elif ny == 1:
                self._fire_dpad("DPAD_DOWN")

    def _fire_dpad(self, direction: str):
        action = self._mapping.get(direction, "")
        if action:
            self._dispatch(action, "press")

    def _on_press(self, btn: str):
        with self._lock:
            self._state.buttons[btn] = True
        self._press_times[btn] = time.time()
        self._hold_fired[btn] = False

        action = self._mapping.get(btn, "")
        if action and action != "mode_switch":
            self._dispatch(action, "press")

        # Cancel any stale timer from a previous press before starting a
        # fresh one -- without this, rapid re-presses (e.g. a bad
        # connection resending press events) could pile up timers.
        old_timer = self._hold_timers.pop(btn, None)
        if old_timer:
            old_timer.cancel()
        timer = threading.Timer(self._hold_ms / 1000.0, self._check_hold, args=[btn])
        timer.daemon = True
        self._hold_timers[btn] = timer
        timer.start()

    def _on_release(self, btn: str):
        with self._lock:
            self._state.buttons[btn] = False

        timer = self._hold_timers.pop(btn, None)
        if timer:
            timer.cancel()

        action = self._mapping.get(btn, "")
        if action:
            held = self._hold_fired.get(btn, False)
            if action == "mode_switch" and not held:
                pass  # short START press = no action
            else:
                self._dispatch(action, "release")

        self._press_times.pop(btn, None)

    def _check_hold(self, btn: str):
        if not self._state.buttons.get(btn, False):
            return
        if self._hold_fired.get(btn, False):
            return
        self._hold_fired[btn] = True
        action = self._mapping.get(btn, "")
        if action == "mode_switch":
            self._dispatch("mode_switch", "hold")
        elif action:
            self._dispatch(action + "_hold", "hold")

    def _dispatch(self, action: str, event_type: str):
        # Nav mode toggle
        if action == "nav_toggle" and event_type == "press":
            with self._lock:
                self._state.nav_mode = not self._state.nav_mode

        # In nav mode, A/B remap to confirm/back instead of PTT/cancel
        if self._state.nav_mode:
            if action == "ptt" and event_type == "press":
                action = "nav_confirm"
            elif action == "cancel" and event_type == "press":
                action = "nav_back"

        cb = self._callbacks.get(action)
        if cb:
            try:
                cb(event_type)
            except Exception:
                pass

        any_cb = self._callbacks.get("__any__")
        if any_cb:
            try:
                any_cb(action, event_type)
            except Exception:
                pass


# ── Singleton ─────────────────────────────────────────────

_manager: Optional[ControllerManager] = None


def get_controller() -> Optional[ControllerManager]:
    return _manager


def init_controller(config: dict = None) -> ControllerManager:
    global _manager
    _manager = ControllerManager(config or {})
    return _manager
