"""
IMQ2 Forza Telemetry Integration
Listens for Forza Horizon UDP packets on port 8000 and caches the latest
telemetry snapshot for Q2's race engineer tools.

Packet format: Forza Horizon 5/6 — 324 bytes
Auto-detects FH5 (311 bytes) vs FH6 (324 bytes) by packet size.
Set Data Out IP to 192.168.1.203, port 8000 in Forza settings.
"""

import logging
import socket
import struct
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# FH6 packet format — 324 bytes total
# FH5 is identical except missing CarGroup/SmashableVelDiff/SmashableMass
_FH6_FORMAT = (
    "<"   # little-endian
    "i"   # IsRaceOn
    "I"   # TimestampMS
    "fff" # EngineMaxRpm, EngineIdleRpm, CurrentEngineRpm
    "fff" # AccelerationX/Y/Z
    "fff" # VelocityX/Y/Z
    "fff" # AngularVelocityX/Y/Z
    "fff" # Yaw, Pitch, Roll
    "ffff"# NormalizedSuspensionTravel FL/FR/RL/RR
    "ffff"# TireSlipRatio FL/FR/RL/RR
    "ffff"# WheelRotationSpeed FL/FR/RL/RR
    "iiii"# WheelOnRumbleStrip FL/FR/RL/RR
    "iiii"# WheelInPuddle FL/FR/RL/RR
    "ffff"# SurfaceRumble FL/FR/RL/RR
    "ffff"# TireSlipAngle FL/FR/RL/RR
    "ffff"# TireCombinedSlip FL/FR/RL/RR
    "ffff"# SuspensionTravelMeters FL/FR/RL/RR
    "i"   # CarOrdinal
    "i"   # CarClass
    "i"   # CarPerformanceIndex
    "i"   # DrivetrainType
    "i"   # NumCylinders
    "I"   # CarGroup         (FH6 only)
    "f"   # SmashableVelDiff (FH6 only)
    "f"   # SmashableMass    (FH6 only)
    "fff" # PositionX/Y/Z
    "f"   # Speed
    "f"   # Power
    "f"   # Torque
    "ffff"# TireTemp FL/FR/RL/RR
    "f"   # Boost
    "f"   # Fuel
    "f"   # DistanceTraveled
    "f"   # BestLap
    "f"   # LastLap
    "f"   # CurrentLap
    "f"   # CurrentRaceTime
    "H"   # LapNumber
    "B"   # RacePosition
    "BBBB"# Accel, Brake, Clutch, HandBrake
    "B"   # Gear
    "b"   # Steer
    "b"   # NormalizedDrivingLine
    "b"   # NormalizedAIBrakeDifference
    "x"   # 1 padding byte at end of FH6 packet
)

_FH5_FORMAT = _FH6_FORMAT.replace("Iff", "", 1)  # remove CarGroup+SmashableVelDiff+SmashableMass

_FH6_SIZE = struct.calcsize(_FH6_FORMAT)  # 324
_FH5_SIZE = struct.calcsize(_FH5_FORMAT)  # 311

_FH6_FIELDS = [
    "is_race_on", "timestamp_ms",
    "engine_max_rpm", "engine_idle_rpm", "current_engine_rpm",
    "accel_x", "accel_y", "accel_z",
    "velocity_x", "velocity_y", "velocity_z",
    "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
    "yaw", "pitch", "roll",
    "susp_norm_fl", "susp_norm_fr", "susp_norm_rl", "susp_norm_rr",
    "tire_slip_ratio_fl", "tire_slip_ratio_fr", "tire_slip_ratio_rl", "tire_slip_ratio_rr",
    "wheel_rpm_fl", "wheel_rpm_fr", "wheel_rpm_rl", "wheel_rpm_rr",
    "rumble_strip_fl", "rumble_strip_fr", "rumble_strip_rl", "rumble_strip_rr",
    "puddle_fl", "puddle_fr", "puddle_rl", "puddle_rr",
    "surface_rumble_fl", "surface_rumble_fr", "surface_rumble_rl", "surface_rumble_rr",
    "tire_slip_angle_fl", "tire_slip_angle_fr", "tire_slip_angle_rl", "tire_slip_angle_rr",
    "tire_combined_slip_fl", "tire_combined_slip_fr", "tire_combined_slip_rl", "tire_combined_slip_rr",
    "susp_travel_fl", "susp_travel_fr", "susp_travel_rl", "susp_travel_rr",
    "car_ordinal", "car_class", "car_pi", "drivetrain_type", "num_cylinders",
    "car_group", "smashable_vel_diff", "smashable_mass",
    "pos_x", "pos_y", "pos_z",
    "speed", "power", "torque",
    "tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr",
    "boost", "fuel", "distance_traveled",
    "best_lap", "last_lap", "current_lap", "current_race_time",
    "lap_number", "race_position",
    "accel_input", "brake_input", "clutch_input", "handbrake_input",
    "gear", "steer", "norm_driving_line", "norm_ai_brake_diff",
]

_FH5_FIELDS = [f for f in _FH6_FIELDS if f not in ("car_group", "smashable_vel_diff", "smashable_mass")]

CAR_CLASSES = {0:"D", 1:"C", 2:"B", 3:"A", 4:"S1", 5:"S2", 6:"X", 7:"X"}
DRIVETRAIN  = {0:"FWD", 1:"RWD", 2:"AWD"}


def _parse_packet(data: bytes) -> Optional[dict]:
    size = len(data)
    if size == _FH6_SIZE:
        values = struct.unpack(_FH6_FORMAT, data)
        return dict(zip(_FH6_FIELDS, values))
    elif size == _FH5_SIZE:
        values = struct.unpack(_FH5_FORMAT, data)
        d = dict(zip(_FH5_FIELDS, values))
        d["car_group"] = 0
        d["smashable_vel_diff"] = 0.0
        d["smashable_mass"] = 0.0
        return d
    return None


class ForzaTelemetryListener:
    """
    Background UDP listener. Call start() once; latest telemetry is
    always available via snapshot(). Thread-safe.
    """

    def __init__(self, port: int = 8000):
        self._port     = port
        self._lock     = threading.Lock()
        self._latest   = None
        self._running  = False
        self._thread   = None
        self._packet_count = 0
        self._last_packet_time = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        log.info(f"Forza telemetry listener started on UDP port {self._port}")

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
            log.info(f"Forza UDP bound to 0.0.0.0:{self._port}")
        except Exception as e:
            log.error(f"Forza UDP bind failed: {e}")
            return

        while self._running:
            try:
                data, _ = sock.recvfrom(512)
                parsed = _parse_packet(data)
                if parsed:
                    with self._lock:
                        self._latest = parsed
                    self._last_packet_time = time.time()
                    self._packet_count += 1
                    if self._packet_count % 600 == 0:  # log every ~10s at 60fps
                        log.debug(f"Forza: speed={parsed.get('speed',0)*3.6:.0f}km/h fuel={parsed.get('fuel',0)*100:.0f}%")
            except socket.timeout:
                continue
            except Exception as e:
                log.debug(f"Forza UDP error: {e}")

        sock.close()


# Singleton
_listener: Optional[ForzaTelemetryListener] = None


def get_listener(port: int = 8000) -> ForzaTelemetryListener:
    global _listener
    if _listener is None:
        _listener = ForzaTelemetryListener(port=port)
        _listener.start()
    return _listener


def get_snapshot() -> Optional[dict]:
    return get_listener().snapshot()


def is_active() -> bool:
    return get_listener().is_active()
