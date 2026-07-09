"""
Multi-Controller Manager
=========================
Coordinates the Master Controller (Flipper WiFi or 8BitDo BT, see
voice/controller_server.py and voice/controller.py) and up to 3 additional
USB player controllers (P2-P4).

Master Controller:
  - Has PTT and all of Q2's own voice/nav functions
  - Flipper Zero WiFi preferred (auto-detected when connected)
  - 8BitDo BT fallback (existing evdev system) when Flipper is absent

Player Controllers (P2-P4):
  - Game input only (A/B/X/Y/D-pad) -- no PTT, no voice/nav functions.
    There's only ever one voice identity (Q2 talking to whoever's at the
    Master Controller), so "which player is speaking" isn't a thing these
    controllers do -- what they DO give Q2 is which player pressed which
    button, e.g. for a multiplayer game mode reading player_button events.
  - USB gamepads, auto-detected or configured via Settings > Controller >
    Players.
"""

import threading
import time
import sys
import logging
from typing import Optional, Callable, Dict, List

log = logging.getLogger(__name__)

if sys.platform == "linux":
    try:
        from evdev import InputDevice, list_devices
        EVDEV_OK = True
    except ImportError:
        EVDEV_OK = False
else:
    EVDEV_OK = False

# USB gamepad button codes -> face button names (same EV_KEY codes
# voice/controller.py's BTN_CODES uses for the 8BitDo, since most USB
# gamepads report the same standard HID-to-evdev button codes).
USB_BTN_CODES = {
    304: "A",  # BTN_SOUTH
    305: "B",  # BTN_EAST
    307: "X",  # BTN_NORTH
    308: "Y",  # BTN_WEST
    310: "L",
    311: "R",
    314: "SELECT",
    315: "START",
}

PLAYER_COLORS = {
    "master": "#00dc78",
    "p1":     "#00dc78",
    "p2":     "#ff9900",
    "p3":     "#00aaff",
    "p4":     "#ff3c3c",
}

PLAYER_LABELS = {
    "master": "Master",
    "p1":     "Player 1",
    "p2":     "Player 2",
    "p3":     "Player 3",
    "p4":     "Player 4",
}


class PlayerController:
    """One USB gamepad (P2-P4), read via evdev. Fires simplified events:
    A/B/X/Y/L/R/SELECT/START/DPAD_UP/DPAD_DOWN/DPAD_LEFT/DPAD_RIGHT --
    raw button names, not semantic actions (unlike the Master controller's
    ptt/cancel/nav_* mapping), since players only ever provide game input."""

    def __init__(self, player_id: str, device_path: str, config: dict = None):
        self.player_id = player_id
        self.device_path = device_path
        self._config = config or {}
        self._device = None
        self._running = False
        self._thread = None
        self._connected = False
        self._dpad = {"x": 0, "y": 0}
        self._callbacks: Dict[str, Callable] = {}

    def on_action(self, action: str, cb: Callable):
        self._callbacks[action] = cb

    def on_any(self, cb: Callable):
        self._callbacks["__any__"] = cb

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self):
        if not EVDEV_OK or not self.device_path:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass

    def get_state(self) -> dict:
        return {
            "player_id": self.player_id,
            "label": PLAYER_LABELS.get(self.player_id, self.player_id),
            "color": PLAYER_COLORS.get(self.player_id, "#888"),
            "connected": self._connected,
            "device_path": self.device_path,
        }

    def _run(self):
        while self._running:
            try:
                self._device = InputDevice(self.device_path)
                self._connected = True
                log.info(f"{self.player_id} connected: {self._device.name}")
                self._dispatch("connected", "connect")

                for event in self._device.read_loop():
                    if not self._running:
                        break
                    self._handle_event(event)
            except Exception:
                pass
            finally:
                self._connected = False
                if self._running:
                    time.sleep(1.0)

    def _handle_event(self, event):
        EV_KEY = 1
        EV_ABS = 3

        if event.type == EV_KEY:
            name = USB_BTN_CODES.get(event.code)
            if not name:
                return
            state = "press" if event.value == 1 else "release"
            self._dispatch(name, state)

        elif event.type == EV_ABS:
            ABS_HAT0X, ABS_HAT0Y = 16, 17
            ABS_X, ABS_Y = 0, 1
            prev = dict(self._dpad)

            if event.code == ABS_HAT0X:
                self._dpad["x"] = -1 if event.value < 0 else 1 if event.value > 0 else 0
            elif event.code == ABS_HAT0Y:
                self._dpad["y"] = -1 if event.value < 0 else 1 if event.value > 0 else 0
            elif event.code == ABS_X:
                norm = (event.value - 128) / 128.0
                self._dpad["x"] = -1 if norm < -0.5 else 1 if norm > 0.5 else 0
            elif event.code == ABS_Y:
                norm = (event.value - 128) / 128.0
                self._dpad["y"] = -1 if norm < -0.5 else 1 if norm > 0.5 else 0

            if self._dpad["x"] != prev["x"]:
                if self._dpad["x"] == -1:
                    self._dispatch("DPAD_LEFT", "press")
                elif self._dpad["x"] == 1:
                    self._dispatch("DPAD_RIGHT", "press")
            if self._dpad["y"] != prev["y"]:
                if self._dpad["y"] == -1:
                    self._dispatch("DPAD_UP", "press")
                elif self._dpad["y"] == 1:
                    self._dispatch("DPAD_DOWN", "press")

    def _dispatch(self, action: str, event_type: str):
        cb = self._callbacks.get(action)
        if cb:
            try:
                cb(self.player_id, event_type)
            except Exception:
                pass
        any_cb = self._callbacks.get("__any__")
        if any_cb:
            try:
                any_cb(self.player_id, action, event_type)
            except Exception:
                pass


class MultiControllerManager:
    """Top-level orchestrator for P2-P4 player controllers, plus Master
    Controller status reporting (get_all_states() reads the existing
    ControllerManager/FlipperControllerServer singletons directly rather
    than duplicating their state)."""

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._players: Dict[str, Optional[PlayerController]] = {"p2": None, "p3": None, "p4": None}
        self._callbacks: Dict[str, Callable] = {}
        self._any_callbacks: List[Callable] = []

    # ── Public ─────────────────────────────────────────────

    def on_player_action(self, action: str, cb: Callable):
        """cb(player_id, event_type)"""
        self._callbacks[action] = cb

    def on_any_player(self, cb: Callable):
        """cb(player_id, action, event_type)"""
        self._any_callbacks.append(cb)

    def add_player(self, player_id: str, device_path: str) -> str:
        if player_id not in ("p2", "p3", "p4"):
            return f"Invalid player ID: {player_id}"
        if not device_path:
            return "No device path provided."

        existing = self._players.get(player_id)
        if existing:
            existing.stop()

        ctrl = PlayerController(player_id, device_path, self._config)
        ctrl.on_any(self._on_player_event)
        self._players[player_id] = ctrl
        ctrl.start()
        return f"{PLAYER_LABELS[player_id]} controller added: {device_path}"

    def remove_player(self, player_id: str) -> str:
        ctrl = self._players.get(player_id)
        if ctrl:
            ctrl.stop()
            self._players[player_id] = None
            return f"{PLAYER_LABELS[player_id]} removed."
        return f"No controller for {player_id}."

    def get_all_states(self) -> dict:
        from voice.controller_server import get_flipper_server
        from voice.controller import get_controller

        flipper = get_flipper_server()
        bt = get_controller()

        if flipper and flipper.connected:
            master_source = "flipper"
            master_label = flipper.device_name or "Flipper Zero"
            master_ok = True
        elif bt:
            state = bt.get_state()
            master_source = "bt"
            master_label = "8BitDo Zero 2"
            master_ok = state.get("connected", False)
        else:
            master_source = "none"
            master_label = "No master controller"
            master_ok = False

        players = {}
        for pid, ctrl in self._players.items():
            players[pid] = ctrl.get_state() if ctrl else {
                "player_id": pid, "label": PLAYER_LABELS[pid],
                "color": PLAYER_COLORS[pid], "connected": False, "device_path": "",
            }

        return {
            "master": {
                "source": master_source, "label": master_label,
                "connected": master_ok, "color": PLAYER_COLORS["master"],
            },
            "players": players,
        }

    def detect_usb_gamepads(self) -> List[dict]:
        """Scan for USB gamepads not already assigned to a player slot or
        already claimed by the BT controller."""
        if not EVDEV_OK:
            return []

        assigned_paths = {ctrl.device_path for ctrl in self._players.values() if ctrl}

        from voice.controller import get_controller
        bt = get_controller()
        if bt:
            bt_path = bt.get_state().get("device")
            if bt_path:
                assigned_paths.add(bt_path)

        devices = []
        try:
            for path in list_devices():
                if path in assigned_paths:
                    continue
                try:
                    dev = InputDevice(path)
                    name = dev.name.lower()
                    dev.close()
                    if any(w in name for w in [
                        "gamepad", "joystick", "controller", "xinput",
                        "joypad", "game controller", "8bitdo", "zero 2",
                    ]):
                        devices.append({"path": path, "name": dev.name})
                except Exception:
                    continue
        except Exception:
            pass

        return devices

    # ── Internal ───────────────────────────────────────────

    def _on_player_event(self, player_id: str, action: str, event_type: str):
        cb = self._callbacks.get(action)
        if cb:
            try:
                cb(player_id, event_type)
            except Exception:
                pass

        for any_cb in self._any_callbacks:
            try:
                any_cb(player_id, action, event_type)
            except Exception:
                pass

        try:
            from face.server import emit_controller_event
            emit_controller_event({
                "type": "player_button", "player_id": player_id,
                "action": action, "event_type": event_type,
                "color": PLAYER_COLORS.get(player_id, "#888"),
                "label": PLAYER_LABELS.get(player_id, player_id),
            })
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────

_multi: Optional[MultiControllerManager] = None

def get_multi_controller() -> Optional[MultiControllerManager]:
    return _multi

def init_multi_controller(config: dict = None) -> MultiControllerManager:
    global _multi
    _multi = MultiControllerManager(config or {})
    return _multi
