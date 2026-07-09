"""
IMQ2 AC Bridge — Windows-side only
Reads Assetto Corsa's shared memory ("Local\\acpmf_physics",
"Local\\acpmf_graphics", "Local\\acpmf_static") and forwards a flattened
snapshot as UDP packets to your-pi's AC telemetry listener
(integrations/ac_telemetry.py) at ~10Hz.

This script never runs on your-pi — it lives in windows/, which deploy.ps1
excludes from the Pi sync. Run it manually (or via Task Scheduler) on the
Windows box while Assetto Corsa is running:

    python windows/ac_bridge.py

Field order/types are imported directly from integrations.ac_telemetry
(_AC_FORMAT / _AC_FIELDS) rather than duplicated here, so the bridge's
struct.pack() and the Pi listener's struct.unpack() can never drift apart.

Shared-memory attach strategy: this uses OpenFileMappingW (attach-only),
never CreateFileMapping. AC is always the creator of these sections; if we
used mmap.mmap()'s default Windows behaviour (which calls CreateFileMapping
and will happily create the section itself if AC hasn't started yet), we
could win the race and create it first with the wrong page protection,
silently blocking AC's own writes when it starts. Attach-only avoids that:
if AC isn't running yet, OpenFileMappingW just fails and we retry.
"""

import ctypes
import logging
import socket
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path

# Make the repo root importable so we can pull the canonical wire format
# from integrations/ac_telemetry.py instead of hand-duplicating it here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from integrations.ac_telemetry import _AC_FORMAT, _AC_FIELDS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ac_bridge")

TARGET_IP = "192.168.1.100"
TARGET_PORT = 8001
RATE_HZ = 10
RECONNECT_RETRY_S = 1.0
STALE_READS_BEFORE_RECONNECT = 50  # ~5s at 10Hz with an unchanging packetId

G_TO_MS2 = 9.80665  # AC's accG is already in G's; wire format expects m/s^2 (Forza convention)

FILE_MAP_READ = 0x0004

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenFileMappingW.restype = wintypes.HANDLE
_kernel32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.MapViewOfFile.restype = ctypes.c_void_p
_kernel32.MapViewOfFile.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t)
_kernel32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
_kernel32.UnmapViewOfFile.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL


# ── AC shared memory structs ────────────────────────────────────────────────
# Truncated after the last field we actually read — MapViewOfFile maps the
# whole section regardless, so trailing fields we don't declare are simply
# never touched. Layout (offsets) up to the declared point must stay exact.

class SPageFilePhysics(ctypes.Structure):
    _fields_ = [
        ("packetId", ctypes.c_int),
        ("gas", ctypes.c_float),
        ("brake", ctypes.c_float),
        ("fuel", ctypes.c_float),
        ("gear", ctypes.c_int),
        ("rpms", ctypes.c_int),
        ("steerAngle", ctypes.c_float),
        ("speedKmh", ctypes.c_float),
        ("velocity", ctypes.c_float * 3),
        ("accG", ctypes.c_float * 3),
        ("wheelSlip", ctypes.c_float * 4),
        ("wheelLoad", ctypes.c_float * 4),
        ("wheelsPressure", ctypes.c_float * 4),
        ("wheelAngularSpeed", ctypes.c_float * 4),
        ("tyreWear", ctypes.c_float * 4),
        ("tyreDirtyLevel", ctypes.c_float * 4),
        ("tyreCoreTemperature", ctypes.c_float * 4),
        ("camberRAD", ctypes.c_float * 4),
        ("suspensionTravel", ctypes.c_float * 4),
        ("drs", ctypes.c_float),
        ("tc", ctypes.c_float),
        ("heading", ctypes.c_float),
        ("pitch", ctypes.c_float),
        ("roll", ctypes.c_float),
        ("cgHeight", ctypes.c_float),
        ("carDamage", ctypes.c_float * 5),
        ("numberOfTyresOut", ctypes.c_int),
        ("pitLimiterOn", ctypes.c_int),
        ("abs", ctypes.c_float),
        ("kersCharge", ctypes.c_float),
        ("kersInput", ctypes.c_float),
        ("autoShifterOn", ctypes.c_int),
        ("rideHeight", ctypes.c_float * 2),
        ("turboBoost", ctypes.c_float),
        ("ballast", ctypes.c_float),
        ("airDensity", ctypes.c_float),
        ("airTemp", ctypes.c_float),
        ("roadTemp", ctypes.c_float),
        ("localAngularVel", ctypes.c_float * 3),
        ("finalFF", ctypes.c_float),
        ("performanceMeter", ctypes.c_float),
        ("engineBrake", ctypes.c_int),
        ("ersRecoveryLevel", ctypes.c_int),
        ("ersPowerLevel", ctypes.c_int),
        ("ersHeatCharging", ctypes.c_int),
        ("ersIsCharging", ctypes.c_int),
        ("kersCurrentKJ", ctypes.c_float),
        ("drsAvailable", ctypes.c_int),
        ("drsEnabled", ctypes.c_int),
        ("brakeTemp", ctypes.c_float * 4),
        ("clutch", ctypes.c_float),
        # remaining physics fields (tyreTempI/M/O, contact points, etc.)
        # exist in newer AC builds but aren't needed here — left undeclared.
    ]


class SPageFileGraphics(ctypes.Structure):
    _fields_ = [
        ("packetId", ctypes.c_int),
        ("status", ctypes.c_int),
        ("session", ctypes.c_int),
        ("currentTime", ctypes.c_wchar * 15),
        ("lastTime", ctypes.c_wchar * 15),
        ("bestTime", ctypes.c_wchar * 15),
        ("split", ctypes.c_wchar * 15),
        ("completedLaps", ctypes.c_int),
        ("position", ctypes.c_int),
        ("iCurrentTime", ctypes.c_int),
        ("iLastTime", ctypes.c_int),
        ("iBestTime", ctypes.c_int),
        ("sessionTimeLeft", ctypes.c_float),
        ("distanceTraveled", ctypes.c_float),
        ("isInPit", ctypes.c_int),
        ("currentSectorIndex", ctypes.c_int),
        ("lastSectorTime", ctypes.c_int),
        ("numberOfLaps", ctypes.c_int),
        ("tyreCompound", ctypes.c_wchar * 33),
        ("replayTimeMultiplier", ctypes.c_float),
        ("normalizedCarPosition", ctypes.c_float),
        ("activeCars", ctypes.c_int),
        ("carCoordinates", (ctypes.c_float * 3) * 60),
        ("carID", ctypes.c_int * 60),
        ("playerCarID", ctypes.c_int),
        ("penaltyTime", ctypes.c_float),
        ("flag", ctypes.c_int),
        ("penalty", ctypes.c_int),
        ("idealLineOn", ctypes.c_int),
        ("isInPitLane", ctypes.c_int),
        ("surfaceGrip", ctypes.c_float),
        # remaining graphics fields (wind, TC/ABS UI state, etc.) not needed.
    ]


class SPageFileStatic(ctypes.Structure):
    _fields_ = [
        ("smVersion", ctypes.c_wchar * 15),
        ("acVersion", ctypes.c_wchar * 15),
        ("numberOfSessions", ctypes.c_int),
        ("numCars", ctypes.c_int),
        ("carModel", ctypes.c_wchar * 33),
        ("track", ctypes.c_wchar * 33),
        ("playerName", ctypes.c_wchar * 33),
        ("playerSurname", ctypes.c_wchar * 33),
        ("playerNick", ctypes.c_wchar * 33),
        ("sectorCount", ctypes.c_int),
        ("maxTorque", ctypes.c_float),
        ("maxPower", ctypes.c_float),
        ("maxRpm", ctypes.c_int),
        ("maxFuel", ctypes.c_float),
        # remaining static fields (assists, DRS/ERS/KERS flags, etc.) not needed.
    ]


class SharedMemoryBlock:
    """Attach-only view onto a named AC shared-memory section."""

    def __init__(self, name: str, struct_type):
        self.name = name
        self.struct_type = struct_type
        self._handle = None
        self._addr = None

    def open(self) -> bool:
        handle = _kernel32.OpenFileMappingW(FILE_MAP_READ, False, self.name)
        if not handle:
            return False
        addr = _kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
        if not addr:
            _kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        self._addr = addr
        return True

    def close(self):
        if self._addr:
            _kernel32.UnmapViewOfFile(self._addr)
            self._addr = None
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def read(self):
        size = ctypes.sizeof(self.struct_type)
        buf = ctypes.string_at(self._addr, size)
        return self.struct_type.from_buffer_copy(buf)


class ACSharedMemoryReader:
    def __init__(self):
        self.physics = SharedMemoryBlock("Local\\acpmf_physics", SPageFilePhysics)
        self.graphics = SharedMemoryBlock("Local\\acpmf_graphics", SPageFileGraphics)
        self.static = SharedMemoryBlock("Local\\acpmf_static", SPageFileStatic)
        self.connected = False
        self._last_packet_id = None
        self._stale_reads = 0

    def connect(self) -> bool:
        if self.physics.open() and self.graphics.open() and self.static.open():
            self.connected = True
            self._last_packet_id = None
            self._stale_reads = 0
            return True
        self.physics.close()
        self.graphics.close()
        self.static.close()
        return False

    def disconnect(self):
        self.physics.close()
        self.graphics.close()
        self.static.close()
        self.connected = False

    def read_snapshot(self):
        """Returns (physics, graphics, static) structs, or None if stale/AC exited."""
        p = self.physics.read()
        g = self.graphics.read()
        s = self.static.read()

        if p.packetId == self._last_packet_id:
            self._stale_reads += 1
            if self._stale_reads >= STALE_READS_BEFORE_RECONNECT:
                log.warning("AC packetId unchanged for %ds — assuming AC closed, reconnecting", STALE_READS_BEFORE_RECONNECT // RATE_HZ)
                return None
        else:
            self._stale_reads = 0
        self._last_packet_id = p.packetId

        return p, g, s


def build_packet(p, g, s) -> bytes:
    values = {
        "status": int(g.status),
        "session_type": int(g.session),
        "gas": float(p.gas),
        "brake": float(p.brake),
        "clutch": float(p.clutch),
        "steer_angle": float(p.steerAngle),
        "gear": int(p.gear),
        "rpm": int(p.rpms),
        "max_rpm": float(s.maxRpm),
        "speed_kmh": float(p.speedKmh),
        "fuel": float(p.fuel),
        "accel_x": float(p.accG[0]) * G_TO_MS2,
        "accel_y": float(p.accG[1]) * G_TO_MS2,
        "accel_z": float(p.accG[2]) * G_TO_MS2,
        "tyre_temp_fl": float(p.tyreCoreTemperature[0]),
        "tyre_temp_fr": float(p.tyreCoreTemperature[1]),
        "tyre_temp_rl": float(p.tyreCoreTemperature[2]),
        "tyre_temp_rr": float(p.tyreCoreTemperature[3]),
        "tyre_pressure_fl": float(p.wheelsPressure[0]),
        "tyre_pressure_fr": float(p.wheelsPressure[1]),
        "tyre_pressure_rl": float(p.wheelsPressure[2]),
        "tyre_pressure_rr": float(p.wheelsPressure[3]),
        # AC reports tyreWear as 100 (new) -> 0 (worn out); flip so the wire
        # format's "wear" reads as percent-worn, matching tools/race_engineer_ac.py's
        # >80 = "worn" alert threshold.
        "tyre_wear_fl": 100.0 - float(p.tyreWear[0]),
        "tyre_wear_fr": 100.0 - float(p.tyreWear[1]),
        "tyre_wear_rl": 100.0 - float(p.tyreWear[2]),
        "tyre_wear_rr": 100.0 - float(p.tyreWear[3]),
        "brake_temp_fl": float(p.brakeTemp[0]),
        "brake_temp_fr": float(p.brakeTemp[1]),
        "brake_temp_rl": float(p.brakeTemp[2]),
        "brake_temp_rr": float(p.brakeTemp[3]),
        "wheel_slip_fl": float(p.wheelSlip[0]),
        "wheel_slip_fr": float(p.wheelSlip[1]),
        "wheel_slip_rl": float(p.wheelSlip[2]),
        "wheel_slip_rr": float(p.wheelSlip[3]),
        "susp_travel_fl": float(p.suspensionTravel[0]),
        "susp_travel_fr": float(p.suspensionTravel[1]),
        "susp_travel_rl": float(p.suspensionTravel[2]),
        "susp_travel_rr": float(p.suspensionTravel[3]),
        "tc": float(p.tc),
        "abs": float(p.abs),
        "turbo_boost": float(p.turboBoost),
        "ballast": float(p.ballast),
        "air_temp": float(p.airTemp),
        "road_temp": float(p.roadTemp),
        "surface_grip": float(g.surfaceGrip),
        "damage_front": float(p.carDamage[0]),
        "damage_rear": float(p.carDamage[1]),
        "damage_left": float(p.carDamage[2]),
        "damage_right": float(p.carDamage[3]),
        "damage_centre": float(p.carDamage[4]),
        "tyres_out": int(p.numberOfTyresOut),
        "pit_limiter_on": int(p.pitLimiterOn),
        "is_in_pit": int(g.isInPit),
        "is_in_pit_lane": int(g.isInPitLane),
        "completed_laps": int(g.completedLaps),
        "position": int(g.position),
        "current_lap_ms": int(g.iCurrentTime),
        "last_lap_ms": int(g.iLastTime),
        "best_lap_ms": int(g.iBestTime),
        "number_of_laps": int(g.numberOfLaps),
        "normalized_car_position": float(g.normalizedCarPosition),
        "distance_traveled": float(g.distanceTraveled),
        "session_time_left": float(g.sessionTimeLeft),
        "flag": int(g.flag),
    }
    ordered = tuple(values[name] for name in _AC_FIELDS)
    return struct.pack(_AC_FORMAT, *ordered)


def main():
    reader = ACSharedMemoryReader()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log.info(f"AC bridge starting — target {TARGET_IP}:{TARGET_PORT} at {RATE_HZ}Hz")

    try:
        while True:
            if not reader.connected:
                if reader.connect():
                    log.info("Attached to AC shared memory")
                else:
                    time.sleep(RECONNECT_RETRY_S)
                    continue

            try:
                snapshot = reader.read_snapshot()
            except Exception as e:
                log.warning(f"AC shared memory read failed: {e}")
                reader.disconnect()
                continue

            if snapshot is None:
                reader.disconnect()
                continue

            p, g, s = snapshot
            try:
                packet = build_packet(p, g, s)
                sock.sendto(packet, (TARGET_IP, TARGET_PORT))
            except Exception as e:
                log.warning(f"AC packet build/send failed: {e}")

            time.sleep(1.0 / RATE_HZ)
    except KeyboardInterrupt:
        log.info("AC bridge stopping")
    finally:
        reader.disconnect()
        sock.close()


if __name__ == "__main__":
    main()
