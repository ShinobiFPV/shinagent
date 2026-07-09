"""
IMQ2 Forza Telemetry Integration
Listens for Forza Horizon UDP packets on port 8000 and caches the latest
telemetry snapshot for Q2's race engineer tools.

Packet format: Forza Horizon 5/6 — 324 bytes
Auto-detects FH5 (311 bytes) vs FH6 (324 bytes) by packet size.
Set Data Out IP to 192.168.1.100, port 8000 in Forza settings.
"""

import logging
import math
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

# Built as its own explicit literal (mirroring _FH6_FORMAT minus the
# CarGroup/SmashableVelDiff/SmashableMass line) rather than derived via
# string manipulation on _FH6_FORMAT: a naive `.replace("Iff", "", 1)`
# matches the FIRST "Iff" substring in the concatenated format string,
# which falls at TimestampMS+EngineMaxRpm+EngineIdleRpm (position 2), not
# the intended CarGroup+SmashableVelDiff+SmashableMass block near the end
# — silently corrupting every FH5 packet's field alignment.
_FH5_FORMAT = (
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
    # No trailing padding byte here (unlike FH6) — dropping it is what
    # makes this land on the 311 bytes this module has always documented
    # (module docstring above, and the `# 311` comment on _FH5_SIZE below).
    # If real FH5 capture ever shows 312 bytes instead, add "x" back here
    # and drop this comment.
)

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


def _detect_driving_mode(snap: dict) -> str:
    """
    'race' | 'freeroam' | 'menu'. menu = IsRaceOn=0 (paused/in a menu).
    Within a live session (IsRaceOn=1): a scored race has LapNumber and/or
    RacePosition counting up, or -- for the standing-start instant before
    either of those tick over from 0 -- CurrentRaceTime already running;
    the >5s grace period on race_time is there so a momentary telemetry
    glitch at a race's very start doesn't get read as free roam.
    """
    if not snap.get("is_race_on"):
        return "menu"
    if snap.get("lap_number", 0) > 0 or snap.get("race_position", 0) > 0 or snap.get("current_race_time", 0.0) > 5.0:
        return "race"
    return "freeroam"


def _wheel4(snap: dict, *field_names: str) -> list:
    return [snap.get(f, 0) for f in field_names]


class DriftAnalyser:
    """
    Detects and scores drift events from rear-tyre slip + yaw rate.
    A drift is "on" while speed/yaw-rate/rear-slip/rear-angle all clear
    their thresholds together, and "ends" once any of them has failed for
    more than 0.5s straight (the grace window avoids treating a single
    noisy telemetry frame mid-drift as the end of it).
    """

    def __init__(self):
        self._drifting = False
        self._drift_start = None
        self._drift_peak_yaw = 0.0
        self._drift_angle = 0.0
        self._last_not_drift = None
        self._events = []  # recent completed drift events

    def update(self, snap: dict) -> dict:
        speed_ms = snap.get("speed", 0)
        yaw_rate = abs(snap.get("angular_velocity_y", 0))

        rear_slip_vals = snap.get("tire_combined_slip", [0, 0, 0, 0])
        rear_slip = (rear_slip_vals[2] + rear_slip_vals[3]) / 2 if len(rear_slip_vals) >= 4 else 0

        rear_angle_vals = snap.get("tire_slip_angle", [0, 0, 0, 0])
        rear_angle = abs((rear_angle_vals[2] + rear_angle_vals[3]) / 2) if len(rear_angle_vals) >= 4 else 0

        is_drifting = (
            speed_ms > 8.33      # > 30 km/h
            and yaw_rate > 0.5   # yaw rate threshold (rad/s)
            and rear_slip > 0.3  # rear tyres sliding
            and rear_angle > 0.087  # > 5 degrees, in radians
        )

        now = time.time()
        event = None

        if is_drifting:
            self._last_not_drift = None
            if not self._drifting:
                self._drifting = True
                self._drift_start = now
                self._drift_peak_yaw = yaw_rate
            else:
                self._drift_peak_yaw = max(self._drift_peak_yaw, yaw_rate)
                self._drift_angle = math.degrees(rear_angle)
        elif self._drifting:
            if self._last_not_drift is None:
                self._last_not_drift = now
            elif now - self._last_not_drift > 0.5:
                duration = now - self._drift_start
                event = {
                    "type": "drift_end",
                    "duration": round(duration, 2),
                    "peak_yaw": round(math.degrees(self._drift_peak_yaw), 1),
                    "angle": round(self._drift_angle, 1),
                    "score": self._score_drift(duration, self._drift_peak_yaw, self._drift_angle),
                }
                self._events.append(event)
                if len(self._events) > 20:
                    self._events.pop(0)
                self._drifting = False
                self._drift_start = None
                self._drift_peak_yaw = 0.0

        return {
            "drifting": self._drifting,
            "drift_duration": round(now - self._drift_start, 1) if self._drifting else 0,
            "drift_angle": round(self._drift_angle, 1),
            "peak_yaw_dps": round(math.degrees(self._drift_peak_yaw), 1),
            "recent_event": event,
            "recent_drifts": self._events[-5:],
        }

    def _score_drift(self, duration, peak_yaw_rads, angle_degrees) -> str:
        """'mild' | 'nice' | 'clean' | 'insane'. angle_degrees is already in
        degrees (self._drift_angle), unlike peak_yaw_rads which isn't."""
        yaw_dps = math.degrees(peak_yaw_rads)
        angle_d = angle_degrees

        if duration < 1.0 or yaw_dps < 30:
            return "mild"
        elif duration < 2.0 and yaw_dps < 60:
            return "nice"
        elif duration > 3.0 or yaw_dps > 90:
            return "insane"
        else:
            return "clean"


class AirDetector:
    """
    Detects airtime via suspension travel: all 4 corners near full droop
    (normalized travel near 1.0) means the wheels are hanging with nothing
    to compress against, i.e. the car is off the ground.
    """

    def __init__(self):
        self._airborne = False
        self._air_start = None
        self._peak_speed_ms = 0.0

    def update(self, snap: dict) -> dict:
        susp = snap.get("normalized_suspension_travel", [0, 0, 0, 0])
        avg_susp = sum(susp) / 4 if susp else 0
        speed = snap.get("speed", 0)

        is_airborne = avg_susp > 0.85

        now = time.time()
        event = None

        if is_airborne:
            if not self._airborne:
                self._airborne = True
                self._air_start = now
                self._peak_speed_ms = speed
            else:
                self._peak_speed_ms = max(self._peak_speed_ms, speed)
        elif self._airborne:
            duration = now - self._air_start
            event = {
                "type": "landing",
                "airtime": round(duration, 2),
                "speed_kmh": round(self._peak_speed_ms * 3.6, 1),
            }
            self._airborne = False
            self._air_start = None

        return {
            "airborne": self._airborne,
            "air_duration": round(now - self._air_start, 2) if self._airborne else 0,
            "recent_event": event,
        }


class MomentDetector:
    """
    Catches other exciting moments that aren't drifts or jumps: speed
    milestones, near-spins (big yaw spike quickly corrected), and puddle
    hits.
    """

    def __init__(self):
        self._last_speed_milestone = 0
        self._spin_candidate_start = None

    def update(self, snap: dict) -> list:
        events = []
        speed_kmh = snap.get("speed", 0) * 3.6
        yaw_rate = abs(snap.get("angular_velocity_y", 0))

        milestones = [200, 250, 300, 350]
        for ms in milestones:
            if speed_kmh >= ms and self._last_speed_milestone < ms:
                self._last_speed_milestone = ms
                events.append({"type": "speed_milestone", "speed_kmh": ms})
        if speed_kmh < 100:
            self._last_speed_milestone = 0

        yaw_dps = math.degrees(yaw_rate)
        if yaw_dps > 150 and self._spin_candidate_start is None:
            self._spin_candidate_start = time.time()
        elif yaw_dps < 30 and self._spin_candidate_start is not None:
            duration = time.time() - self._spin_candidate_start
            if duration < 2.0:  # corrected within 2 seconds
                events.append({"type": "near_spin", "duration": round(duration, 1)})
            self._spin_candidate_start = None

        puddles = snap.get("wheel_in_puddle_depth", [0, 0, 0, 0])
        if puddles and max(puddles) > 0.3:
            events.append({"type": "puddle", "depth": round(max(puddles), 2)})

        return events


class RaceStateTracker:
    """
    Tracks race-specific state across a single FH6 race: starting/current
    position, position-change events, and lap timing. Reset at the end of
    each race (see ForzaTelemetryListener._process_packet) so state never
    bleeds from one race into the next.

    NOTE on "laps remaining": FH6's telemetry has no total-lap-count field,
    only the current LapNumber. max_lap_seen below just tracks the highest
    lap number observed -- since it's updated from the *current* lap on
    every tick, it is trivially always equal to the current lap, so it
    cannot be used to compute "laps remaining" or detect a genuine final
    lap (by the time you'd know a lap was the last one, the race is
    already over). get_summary() intentionally does not expose a
    remaining-laps or final-lap signal for this reason -- same honesty
    policy as gap-to-leader, which FH6 also doesn't expose.
    """

    def __init__(self):
        self._start_position = None
        self._prev_position = None
        self._position_history = []
        self._race_start_time = None
        self._best_lap_seen = False
        self._max_lap_seen = 0
        self._last_alert_pos = None

    def reset(self):
        self.__init__()

    def update(self, snap: dict) -> list:
        """Process one race-mode telemetry frame. Returns any newly
        detected events (overtakes/position losses)."""
        events = []

        pos = snap.get("race_position", 0)
        lap = snap.get("lap_number", 0)
        race_time = snap.get("current_race_time", 0)
        best_lap = snap.get("best_lap", 0)

        if self._start_position is None and pos > 0:
            self._start_position = pos
            self._race_start_time = time.time()

        if lap > self._max_lap_seen:
            self._max_lap_seen = lap

        if self._prev_position is not None and pos > 0:
            if pos < self._prev_position:
                events.append({
                    "type": "overtake", "from_pos": self._prev_position,
                    "to_pos": pos, "is_lead": pos == 1,
                })
            elif pos > self._prev_position:
                events.append({
                    "type": "position_lost", "from_pos": self._prev_position, "to_pos": pos,
                })

        if best_lap > 0 and not self._best_lap_seen:
            self._best_lap_seen = True

        self._prev_position = pos
        self._position_history.append({"time": race_time, "pos": pos, "lap": lap})
        if len(self._position_history) > 300:
            self._position_history.pop(0)

        return events

    def get_summary(self, snap: dict) -> dict:
        """Race summary dict for Q2's tools/alerts to speak from."""
        pos = snap.get("race_position", 0)
        lap = snap.get("lap_number", 0)
        race_time = snap.get("current_race_time", 0)
        best_lap = snap.get("best_lap", 0)
        last_lap = snap.get("last_lap", 0)
        cur_lap = snap.get("current_lap", 0)
        speed_kmh = snap.get("speed", 0) * 3.6

        def fmt_time(t):
            if not t or t <= 0:
                return "--:--.---"
            m = int(t / 60)
            s = t % 60
            return f"{m}:{s:06.3f}"

        pos_delta = None
        if self._start_position and pos > 0:
            pos_delta = self._start_position - pos  # positive = gained, negative = lost

        return {
            "position": pos,
            "start_position": self._start_position,
            "position_delta": pos_delta,
            "lap": lap,
            "max_lap_seen": self._max_lap_seen,
            "race_time": fmt_time(race_time),
            "current_lap_time": fmt_time(cur_lap),
            "last_lap_time": fmt_time(last_lap),
            "best_lap_time": fmt_time(best_lap),
            "speed_kmh": round(speed_kmh, 1),
            "is_leading": pos == 1,
            "positions_gained": max(0, pos_delta) if pos_delta else 0,
            "positions_lost": max(0, -pos_delta) if pos_delta else 0,
        }


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
        self._drift_analyser  = DriftAnalyser()
        self._air_detector    = AirDetector()
        self._moment_detector = MomentDetector()
        self._race_tracker    = RaceStateTracker()
        self._pending_events  = []  # driving-excitement events not yet spoken
        self._prev_mode       = "freeroam"
        self._mode_changed_at = None
        self._race_start_snap = None  # snapshot at race start; reserved for future use
        self._first_packet    = True  # suppress a spurious mode-change event on the very
                                       # first packet if Q2 starts mid-race/mid-freeroam

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

    def pop_events(self) -> list:
        """Return and clear pending driving-excitement events (drift_end,
        landing, speed_milestone, near_spin, puddle) for the alert thread
        to speak. Thread-safe: _process_packet appends from the UDP
        listener thread, this is called from the alert thread."""
        with self._lock:
            events = list(self._pending_events)
            self._pending_events.clear()
        return events

    def _process_packet(self, snap: dict) -> dict:
        """Enrich a freshly-parsed packet with driving_mode and the
        drift/air/moment analysers' state, and queue any newly-completed
        events for pop_events(). Adds list-shaped convenience keys
        (tire_combined_slip, tire_slip_angle, normalized_suspension_travel,
        wheel_in_puddle_depth) alongside the existing flat per-corner
        fields (tire_combined_slip_fl etc.) -- existing consumers
        (tools/race_engineer.py) read the flat names, the new analysers
        read the grouped ones."""
        snap["tire_combined_slip"] = _wheel4(
            snap, "tire_combined_slip_fl", "tire_combined_slip_fr", "tire_combined_slip_rl", "tire_combined_slip_rr")
        snap["tire_slip_angle"] = _wheel4(
            snap, "tire_slip_angle_fl", "tire_slip_angle_fr", "tire_slip_angle_rl", "tire_slip_angle_rr")
        snap["normalized_suspension_travel"] = _wheel4(
            snap, "susp_norm_fl", "susp_norm_fr", "susp_norm_rl", "susp_norm_rr")
        snap["wheel_in_puddle_depth"] = _wheel4(
            snap, "puddle_fl", "puddle_fr", "puddle_rl", "puddle_rr")

        snap["driving_mode"] = _detect_driving_mode(snap)
        drift_state = self._drift_analyser.update(snap)
        air_state = self._air_detector.update(snap)
        moments = self._moment_detector.update(snap)

        new_events = []
        if drift_state["recent_event"]:
            new_events.append(drift_state["recent_event"])
        if air_state["recent_event"]:
            new_events.append(air_state["recent_event"])
        new_events.extend(moments)

        snap["drift"] = drift_state
        snap["air"] = air_state

        current_mode = snap["driving_mode"]
        if self._first_packet:
            # Don't fire a spurious race_started/race_ended on the very
            # first packet just because the game was already mid-race or
            # mid-freeroam before this listener started.
            self._first_packet = False
            self._prev_mode = current_mode
            snap["mode_changed"] = False
        elif current_mode != self._prev_mode:
            snap["mode_changed"] = True
            snap["prev_mode"] = self._prev_mode
            snap["new_mode"] = current_mode

            if current_mode == "race":
                self._race_start_snap = dict(snap)
                self._mode_changed_at = time.time()
                new_events.append({"type": "race_started", "position": snap.get("race_position", 0)})
            elif current_mode == "freeroam" and self._prev_mode == "race":
                new_events.append({"type": "race_ended"})

            self._prev_mode = current_mode
        else:
            snap["mode_changed"] = False

        if current_mode == "race":
            race_events = self._race_tracker.update(snap)
            new_events.extend(race_events)
            snap["race_summary"] = self._race_tracker.get_summary(snap)
        else:
            if snap.get("mode_changed") and snap.get("prev_mode") == "race":
                self._race_tracker.reset()
            snap["race_summary"] = None

        if new_events:
            with self._lock:
                self._pending_events.extend(new_events)
                if len(self._pending_events) > 10:
                    self._pending_events = self._pending_events[-10:]

        return snap

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
                    parsed = self._process_packet(parsed)
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


def last_packet_time() -> float:
    return get_listener()._last_packet_time
