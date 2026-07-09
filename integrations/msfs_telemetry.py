"""
IMQ2 MSFS Telemetry Integration
Listens for Microsoft Flight Simulator telemetry UDP packets on port 8002
and caches the latest snapshot for Q2's First Officer tools.

Like Assetto Corsa, MSFS's live data (SimConnect) is only reachable from
the Windows box actually running the sim — there is no native network
broadcast. windows/msfs_bridge.py reads SimConnect via the Python-SimConnect
package and forwards a flattened snapshot as JSON UDP packets to
your-pi:8002 at ~2Hz. This file is the receiving half only; it does not
talk to SimConnect directly.

Unlike ac_telemetry.py's fixed-width struct wire format, packets here are
plain JSON — SimConnect's field set (strings for waypoint idents/aircraft
title, mixed types) doesn't lend itself to a packed binary layout the way
AC's shared memory struct does, and 2Hz makes the extra bytes irrelevant.

FLIGHT_PHASES is the canonical phase name list — windows/msfs_bridge.py
imports it directly (rather than duplicating the strings) so the bridge's
phase detector and anything on this side matching against phase names
can't drift apart.
"""

import json
import logging
import socket
import threading
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

FLIGHT_PHASES = (
    "PARKED", "TAXI", "TAKEOFF", "CLIMB",
    "CRUISE", "DESCENT", "APPROACH", "LANDING",
)


def _parse_packet(data: bytes) -> Optional[dict]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class MSFSTelemetryListener:
    """
    Background UDP listener for MSFS telemetry forwarded by
    windows/msfs_bridge.py. Call start() once; latest telemetry is
    always available via snapshot(). Thread-safe.
    """

    def __init__(self, port: int = 8002):
        self._port = port
        self._lock = threading.Lock()
        self._latest = None
        self._running = False
        self._thread = None
        self._packet_count = 0
        self._last_packet_time = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        log.info(f"MSFS telemetry listener started on UDP port {self._port}")

    def stop(self):
        self._running = False

    def is_active(self) -> bool:
        """True if a packet arrived in the last 10 seconds (MSFS bridge runs at ~2Hz)."""
        return (time.time() - self._last_packet_time) < 10.0

    def snapshot(self) -> Optional[dict]:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def _listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind(("0.0.0.0", self._port))
            log.info(f"MSFS UDP bound to 0.0.0.0:{self._port}")
        except Exception as e:
            log.error(f"MSFS UDP bind failed: {e}")
            return

        while self._running:
            try:
                data, _ = sock.recvfrom(4096)
                parsed = _parse_packet(data)
                if parsed:
                    with self._lock:
                        self._latest = parsed
                    self._last_packet_time = time.time()
                    self._packet_count += 1
                    if self._packet_count % 20 == 0:  # log every ~10s at 2Hz
                        log.debug(
                            f"MSFS: phase={parsed.get('flight_phase')} "
                            f"alt={parsed.get('altitude_ft', 0):.0f}ft "
                            f"ias={parsed.get('airspeed_ind_kt', 0):.0f}kt"
                        )
                else:
                    log.debug("MSFS UDP packet failed to parse as JSON")
            except socket.timeout:
                continue
            except Exception as e:
                log.debug(f"MSFS UDP error: {e}")

        sock.close()


# Singleton
_listener: Optional[MSFSTelemetryListener] = None


def get_listener(port: int = 8002) -> MSFSTelemetryListener:
    global _listener
    if _listener is None:
        _listener = MSFSTelemetryListener(port=port)
        _listener.start()
    return _listener


def get_snapshot() -> Optional[dict]:
    return get_listener().snapshot()


def is_active() -> bool:
    return get_listener().is_active()


def last_packet_time() -> float:
    return get_listener()._last_packet_time


class MSFSController:
    """
    HTTP client for windows/msfs_bridge.py's control server — the write side
    of the bridge, complementing this module's UDP telemetry read side.
    Talks to the SAME Windows box as the UDP listener, just over HTTP to a
    different port (bridge_port, default 8091) since SimConnect writes need
    a request/response round-trip rather than a fire-and-forget broadcast.
    """

    def __init__(self, bridge_host: str = "192.168.1.101", bridge_port: int = 8091):
        self._base = f"http://{bridge_host}:{bridge_port}"
        self._session = requests.Session()

    def send_command(self, command: str, value=None) -> dict:
        try:
            r = self._session.post(
                f"{self._base}/control",
                json={"command": command, "value": value},
                timeout=3.0,
            )
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def is_reachable(self) -> bool:
        try:
            r = self._session.get(f"{self._base}/status", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False


# Singleton — mirrors get_listener()'s pattern above, but for the control
# (HTTP) side rather than the telemetry (UDP) side.
_controller: Optional[MSFSController] = None


def get_controller(host: str = "192.168.1.101", port: int = 8091) -> MSFSController:
    global _controller
    if _controller is None:
        from config.loader import config
        host = config.get("integrations.msfs_telemetry.bridge_host", host)
        port = config.get("integrations.msfs_telemetry.bridge_port", port)
        _controller = MSFSController(host, port)
    return _controller


def fmt_altitude(feet: float) -> str:
    return f"{feet:,.0f}ft"


def fmt_speed(knots: float) -> str:
    return f"{knots:.0f}kts"


def fmt_heading(degrees: float) -> str:
    return f"{degrees:03.0f}"
