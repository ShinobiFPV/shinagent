"""
IMQ2 ShinLink OS Bridge Integration
HTTP client for ShinLink OS's agent bridge (shinlink-os/ground/agent_bridge.py)
— a local JSON API exposing live RC vehicle telemetry/network-link status
(GET) and Tier 1 control actions (POST /command). Unlike Forza/AC/MSFS,
ShinLink OS runs as a separate process on this SAME Pi (your-pi), not a
different machine, so there's no UDP listener here — this is a plain
pull-based HTTP client, mirroring MSFSController's shape
(integrations/msfs_telemetry.py): GET for telemetry reads, POST /command
for writes, same send_command(command, value) -> dict contract.

Replaces the old approach (integrations/telemetry_reader.py, now deprecated)
which opened its own MAVLink serial connection, duplicating ShinLink OS's
TelemetryReader class verbatim — that risked two processes contending for
the same serial port whenever ShinLink OS's ground station app was also
running. All hardware access now lives in exactly one place: ShinLink OS.
"""

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8095
DEFAULT_TIMEOUT_S = 3.0


class ShinLinkBridge:
    """
    HTTP client for ShinLink OS's agent_bridge.py. Every call is a fresh
    request rather than a cached snapshot — a GET to a same-host bridge is
    cheap enough per tool call, and always current.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        self._base = f"http://{host}:{port}"
        self._timeout = timeout_s
        self._session = requests.Session()

    def get_telemetry(self) -> Optional[dict]:
        return self._get("/telemetry")

    def get_network(self) -> Optional[dict]:
        return self._get("/network")

    def is_reachable(self) -> bool:
        return self.get_telemetry() is not None

    def send_command(self, command: str, value=None) -> dict:
        """
        POSTs a Tier 1 action to /command. Mirrors MSFSController's
        send_command exactly: the bridge's 400/404 responses are still
        valid JSON bodies (not requests exceptions, since we never call
        raise_for_status() here), so {"ok": False, "error": ...} comes
        back the same way for bad input, a missing preset, or an
        operational failure — only a genuine connection problem (bridge
        not running) falls into the except branch below.
        """
        try:
            r = self._session.post(
                f"{self._base}/command",
                json={"command": command, "value": value},
                timeout=self._timeout,
            )
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _get(self, path: str) -> Optional[dict]:
        try:
            r = self._session.get(f"{self._base}{path}", timeout=self._timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.debug(f"ShinLink bridge {path} unreachable: {e}")
            return None


# Singleton — mirrors get_controller()'s pattern in integrations/msfs_telemetry.py
_bridge: Optional[ShinLinkBridge] = None


def get_bridge() -> ShinLinkBridge:
    global _bridge
    if _bridge is None:
        from config.loader import config
        host = config.get("integrations.shinlink_os.bridge_host", DEFAULT_HOST)
        port = config.get("integrations.shinlink_os.bridge_port", DEFAULT_PORT)
        timeout_s = config.get("integrations.shinlink_os.bridge_timeout_s", DEFAULT_TIMEOUT_S)
        _bridge = ShinLinkBridge(host, port, timeout_s)
    return _bridge


def get_telemetry_snapshot() -> Optional[dict]:
    return get_bridge().get_telemetry()


def get_network_snapshot() -> Optional[dict]:
    return get_bridge().get_network()


def is_active() -> bool:
    return get_bridge().is_reachable()
