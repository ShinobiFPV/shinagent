"""
Controller WebSocket Server
============================
Accepts WebSocket connections from WiFi controllers -- currently designed
for a Flipper Zero with the WiFi dev board (ESP32-S2), bridging its UART
button events to Q2 over WiFi. Any connected WiFi controller is treated as
the Master Controller (see voice/multi_controller.py for the Flipper-vs-BT
priority logic).

Soft dependency: `websockets` is optional, same pattern as chromadb/
headroom elsewhere in this repo -- if it's not installed, start() logs a
warning and no-ops rather than crashing the rest of Q2.

Protocol (JSON over WebSocket):
  Client -> Server:
    {"type": "button", "btn": "ok",   "state": "press"}
    {"type": "button", "btn": "back", "state": "release"}
    {"type": "ping"}
    {"type": "identify", "device": "flipper", "name": "Shinobi"}

  Server -> Client:
    {"type": "pong"}
    {"type": "ack",  "btn": "ok"}

Button names from Flipper: ok, back, up, down, left, right

Client frames MUST be masked per RFC 6455 5.1 -- Python's `websockets`
server rejects unmasked frames outright (close code 1002, "incorrect
masking"), verified against this exact server during development. See
the ESP32 firmware's ws_send() for the masking implementation any client
here needs to match.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Optional, Callable, Dict, Set

log = logging.getLogger(__name__)

# Map Flipper button names to Q2 action names -- defaults, remappable via
# Settings > Controller > Players > Flipper Button Mapping.
FLIPPER_DEFAULT_MAPPING = {
    "ok":    "ptt",
    "back":  "cancel",
    "up":    "nav_up",
    "down":  "nav_down",
    "left":  "nav_left",
    "right": "nav_right",
}

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


class FlipperControllerServer:
    """
    WebSocket server that accepts Flipper Zero connections. Fires the same
    action-callback interface as voice.controller.ControllerManager
    (on_action/on_any, cb(event_type)) so voice/talk_button.py's
    ControllerBridge can wire it up identically to the BT controller.
    """

    DEFAULT_HOST = "0.0.0.0"
    DEFAULT_PORT = 8767

    def __init__(self, config: dict = None):
        self._config = config or {}
        flipper_cfg = self._config.get("flipper", {})
        self._host = self.DEFAULT_HOST
        self._port = int(flipper_cfg.get("port", self.DEFAULT_PORT))
        self._mapping = dict(FLIPPER_DEFAULT_MAPPING)
        self._mapping.update(flipper_cfg.get("mapping", {}))

        self._clients: Set = set()
        self._connected = False
        self._device_name = ""
        self._last_ping = 0.0
        self._running = False
        self._loop = None
        self._thread = None

        # Same callback interface as ControllerManager
        self._callbacks: Dict[str, Callable] = {}

    # ── Public ─────────────────────────────────────────────

    def on_action(self, action: str, cb: Callable):
        self._callbacks[action] = cb

    def on_any(self, cb: Callable):
        self._callbacks["__any__"] = cb

    @property
    def connected(self) -> bool:
        return self._connected and bool(self._clients)

    @property
    def device_name(self) -> str:
        return self._device_name

    def start(self):
        if not WEBSOCKETS_AVAILABLE:
            log.warning("websockets not installed -- Flipper controller disabled. pip install websockets")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info(f"Flipper controller server started on ws://{self._host}:{self._port}")

    def stop(self):
        self._running = False

    def get_state(self) -> dict:
        return {
            "connected": self.connected,
            "device_name": self._device_name,
            "port": self._port,
            "clients": len(self._clients),
            "last_ping": self._last_ping,
            "mapping": self._mapping,
        }

    def update_mapping(self, new_mapping: dict):
        self._mapping.update(new_mapping)

    def send_to_all(self, data: dict):
        """Send a message to all connected Flippers."""
        if not self._clients or self._loop is None:
            return
        msg = json.dumps(data)
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        except Exception:
            pass

    # ── Internal ───────────────────────────────────────────

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            log.error(f"Flipper WS server error: {e}")

    async def _serve(self):
        # websockets >= 10's serve() handler takes a single argument
        # (the connection) -- no more (websocket, path).
        async with websockets.serve(
            self._handle_client, self._host, self._port,
            ping_interval=20, ping_timeout=10,
        ):
            await asyncio.Future()  # run forever

    async def _broadcast(self, msg: str):
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def _handle_client(self, ws):
        self._clients.add(ws)
        self._connected = True
        self._last_ping = time.time()
        log.info(f"Flipper connected: {ws.remote_address}")
        self._dispatch("flipper_connected", "connect")

        try:
            await ws.send(json.dumps({"type": "welcome", "server": "Q2 Controller Server", "version": "1.0"}))
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                await self._handle_event(ws, evt)
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            if not self._clients:
                self._connected = False
                log.info("Flipper disconnected")
                self._dispatch("flipper_disconnected", "disconnect")

    async def _handle_event(self, ws, evt: dict):
        etype = evt.get("type", "")

        if etype == "ping":
            self._last_ping = time.time()
            await ws.send(json.dumps({"type": "pong"}))

        elif etype == "identify":
            self._device_name = evt.get("name", "Flipper Zero")
            log.info(f"Flipper identified as: {self._device_name}")
            await ws.send(json.dumps({"type": "ack", "message": f"Hello {self._device_name}!"}))

        elif etype == "button":
            btn = evt.get("btn", "").lower()
            state = evt.get("state", "press").lower()
            action = self._mapping.get(btn, "")
            if action:
                self._dispatch(action, state)
                await ws.send(json.dumps({"type": "ack", "btn": btn}))

    def _dispatch(self, action: str, event_type: str):
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

_flipper_server: Optional[FlipperControllerServer] = None

def get_flipper_server() -> Optional[FlipperControllerServer]:
    return _flipper_server

def init_flipper_server(config: dict = None) -> FlipperControllerServer:
    global _flipper_server
    _flipper_server = FlipperControllerServer(config or {})
    return _flipper_server
