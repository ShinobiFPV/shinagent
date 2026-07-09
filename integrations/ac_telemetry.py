"""
IMQ2 Assetto Corsa Telemetry Integration
Listens for AC telemetry UDP packets on port 8001 and caches the latest
snapshot for Q2's race engineer tools.

Unlike Forza, AC only exposes telemetry via local shared memory on the
Windows box actually running the sim — there is no native UDP broadcast.
windows/ac_bridge.py reads that shared memory and forwards it as UDP
packets to your-pi:8001 in the wire format defined below. This file is
the receiving half only; it does not talk to AC or shared memory directly.

_AC_FORMAT / _AC_FIELDS are the canonical wire format — windows/ac_bridge.py
imports these constants directly (rather than duplicating them) so the
bridge's struct.pack() and this file's struct.unpack() can never drift
apart. If you change the field set here, the bridge picks it up automatically;
just make sure build_packet() in ac_bridge.py still supplies every field name
listed below.
"""

import logging
import socket
import struct
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# AC telemetry packet format — little-endian, no padding
_AC_FORMAT = (
    "<"     # little-endian
    "ii"    # status, session_type
    "ffff"  # gas, brake, clutch, steerAngle
    "ii"    # gear, rpm
    "fff"   # maxRpm, speedKmh, fuel
    "fff"   # accelX, accelY, accelZ
    "ffff"  # tyreTemp FL/FR/RL/RR
    "ffff"  # tyrePressure FL/FR/RL/RR
    "ffff"  # tyreWear FL/FR/RL/RR
    "ffff"  # brakeTemp FL/FR/RL/RR
    "ffff"  # wheelSlip FL/FR/RL/RR
    "ffff"  # suspensionTravel FL/FR/RL/RR
    "ffff"  # tc, abs, turboBoost, ballast
    "fff"   # airTemp, roadTemp, surfaceGrip
    "fffff" # carDamage front/rear/left/right/centre
    "iiii"  # numberOfTyresOut, pitLimiterOn, isInPit, isInPitLane
    "ii"    # completedLaps, position
    "iii"   # currentLapMs, lastLapMs, bestLapMs
    "i"     # numberOfLaps
    "fff"   # normalizedCarPosition, distanceTraveled, sessionTimeLeft
    "i"     # flag
    "ii"    # mandatoryPitDone, missingMandatoryPits
    "ff"    # fuelEstimatedLaps, sessionClock
    "ii"    # pitWindowStart, pitWindowEnd (minutes, from ACC's static session info)
)

_AC_FIELDS = [
    "status", "session_type",
    "gas", "brake", "clutch", "steer_angle",
    "gear", "rpm",
    "max_rpm", "speed_kmh", "fuel",
    "accel_x", "accel_y", "accel_z",
    "tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr",
    "tyre_pressure_fl", "tyre_pressure_fr", "tyre_pressure_rl", "tyre_pressure_rr",
    "tyre_wear_fl", "tyre_wear_fr", "tyre_wear_rl", "tyre_wear_rr",
    "brake_temp_fl", "brake_temp_fr", "brake_temp_rl", "brake_temp_rr",
    "wheel_slip_fl", "wheel_slip_fr", "wheel_slip_rl", "wheel_slip_rr",
    "susp_travel_fl", "susp_travel_fr", "susp_travel_rl", "susp_travel_rr",
    "tc", "abs", "turbo_boost", "ballast",
    "air_temp", "road_temp", "surface_grip",
    "damage_front", "damage_rear", "damage_left", "damage_right", "damage_centre",
    "tyres_out", "pit_limiter_on", "is_in_pit", "is_in_pit_lane",
    "completed_laps", "position",
    "current_lap_ms", "last_lap_ms", "best_lap_ms",
    "number_of_laps",
    "normalized_car_position", "distance_traveled", "session_time_left",
    "flag",
    "mandatory_pit_done", "missing_mandatory_pits",
    "fuel_estimated_laps", "session_clock",
    "pit_window_start", "pit_window_end",
]

_AC_SIZE = struct.calcsize(_AC_FORMAT)

assert len(_AC_FIELDS) == _AC_FORMAT.count("i") + _AC_FORMAT.count("f"), \
    "AC telemetry field count doesn't match format string"

AC_STATUS = {0: "OFF", 1: "REPLAY", 2: "LIVE", 3: "PAUSE"}
AC_SESSION_TYPE = {
    0: "Practice", 1: "Qualifying", 2: "Race",
    3: "Hotlap", 4: "Time Attack", 5: "Drift", 6: "Drag",
}
AC_FLAG = {
    0: "None", 1: "Blue", 2: "Yellow", 3: "Black", 4: "White",
    5: "Checkered", 6: "Penalty", 7: "Green", 8: "Orange",
}


def _parse_packet(data: bytes) -> Optional[dict]:
    if len(data) != _AC_SIZE:
        return None
    values = struct.unpack(_AC_FORMAT, data)
    return dict(zip(_AC_FIELDS, values))


class ACTelemetryListener:
    """
    Background UDP listener for AC telemetry forwarded by
    windows/ac_bridge.py. Call start() once; latest telemetry is
    always available via snapshot(). Thread-safe.
    """

    def __init__(self, port: int = 8001):
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
        log.info(f"AC telemetry listener started on UDP port {self._port}")

    def stop(self):
        self._running = False

    def is_active(self) -> bool:
        """True if a packet arrived in the last 3 seconds."""
        return (time.time() - self._last_packet_time) < 3.0

    def snapshot(self) -> Optional[dict]:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def _listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind(("0.0.0.0", self._port))
            log.info(f"AC UDP bound to 0.0.0.0:{self._port}")
        except Exception as e:
            log.error(f"AC UDP bind failed: {e}")
            return

        while self._running:
            try:
                data, _ = sock.recvfrom(2048)
                parsed = _parse_packet(data)
                if parsed:
                    with self._lock:
                        self._latest = parsed
                    self._last_packet_time = time.time()
                    self._packet_count += 1
                    if self._packet_count % 600 == 0:  # log every ~10s at 60fps
                        log.debug(f"AC: speed={parsed.get('speed_kmh',0):.0f}km/h fuel={parsed.get('fuel',0):.1f}L")
                else:
                    log.debug(f"AC UDP packet size mismatch: got {len(data)} bytes, expected {_AC_SIZE}")
            except socket.timeout:
                continue
            except Exception as e:
                log.debug(f"AC UDP error: {e}")

        sock.close()


# Singleton
_listener: Optional[ACTelemetryListener] = None


def get_listener(port: int = 8001) -> ACTelemetryListener:
    global _listener
    if _listener is None:
        _listener = ACTelemetryListener(port=port)
        _listener.start()
    return _listener


def get_snapshot() -> Optional[dict]:
    return get_listener().snapshot()


def is_active() -> bool:
    return get_listener().is_active()


def last_packet_time() -> float:
    return get_listener()._last_packet_time
