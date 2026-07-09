# DEPRECATED — superseded by integrations/shinlink_bridge.py
# Kept to avoid breaking any Pi-side imports that may reference it.
# Safe to remove once confirmed nothing else imports this.

"""
IMQ2 RC Telemetry Reader
Extracted from ShinLink OS (~/shinlink_os.py, the user's existing ground
station app) as its own standalone module, so Q2's tools can read crawler
telemetry without importing the full 20,000+ line GUI application (which
pulls in tkinter and pygame at module level).

This class is copied verbatim from ShinLink OS's TelemetryReader — same
MAVLink parsing logic, same thread-safe public attributes — just lifted out
of the GUI file so it can be imported standalone. If ShinLink OS's own
telemetry logic changes in the future, this file should be re-synced from
the source of truth there, since this is intentionally a copy, not a shared
dependency (keeps Q2's process fully decoupled from ShinLink OS's codebase).
"""

import logging
import threading

log = logging.getLogger(__name__)


class TelemetryReader:
    """
    Reads MAVLink telemetry via MicoAir LR24 or any MAVLink radio.
    Connects over USB serial (CP2102), sends GCS heartbeat so the FC
    starts streaming, and parses attitude, GPS, battery, RC, and Lua
    NAMED_VALUE_FLOAT messages. Thread-safe — all values readable from
    any thread (originally designed for GUI polling, works identically
    for Q2's tool-call polling).
    """
    STREAM_RATES = {
        "ATTITUDE":       4,
        "GPS_RAW_INT":    2,
        "VFR_HUD":        4,
        "BATTERY_STATUS": 1,
        "SYS_STATUS":     1,
        "RC_CHANNELS":    2,
    }

    def __init__(self):
        self._lock       = threading.Lock()
        self._running    = False
        self._thread     = None
        self._conn       = None
        self._on_status  = None
        self._log_file   = None
        self._log_writer = None
        self.connected   = False
        # Attitude
        self.roll_deg    = 0.0;  self.pitch_deg  = 0.0;  self.yaw_deg   = 0.0
        self.rollspeed   = 0.0;  self.pitchspeed = 0.0;  self.yawspeed  = 0.0
        # GPS
        self.lat = 0.0;  self.lon = 0.0;  self.alt_m = 0.0
        self.gps_fix = 0;  self.satellites = 0;  self.hdop = 99.9
        # Flight
        self.groundspeed  = 0.0;  self.airspeed = 0.0
        self.climb_rate   = 0.0;  self.throttle_pct = 0
        # Battery
        self.batt_voltage = 0.0;  self.batt_current = 0.0;  self.batt_pct = -1
        # Status
        self.flight_mode  = "?";  self.armed = False;  self.vehicle_type = "?"
        self.rc_chan       = [0] * 18
        self.named_values  = {}
        self.statustext    = ""
        self.last_msg_time = 0.0

    def start(self, port, baud, on_status, do_log=False):
        if self._running: self.stop()
        self._on_status = on_status
        self._running   = True
        if do_log: self._open_log()
        self._thread = threading.Thread(
            target=self._reader_thread, args=(port, baud), daemon=True)
        self._thread.start()

    def stop(self):
        self._running  = False
        self.connected = False
        if self._conn:
            try: self._conn.close()
            except Exception: pass
            self._conn = None
        if self._log_file:
            try: self._log_file.close()
            except Exception: pass
            self._log_file = None

    def _open_log(self):
        import csv, datetime as _dt
        fname = f"telem_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self._log_file   = open(fname, "w", newline="")
        self._log_writer = csv.writer(self._log_file)
        self._log_writer.writerow([
            "time","roll","pitch","yaw","lat","lon","alt","fix","sats",
            "gspeed","climb","throttle","batt_v","batt_a","batt_pct",
            "armed","mode"])

    def _log_row(self):
        if not self._log_writer: return
        import time as _t
        try:
            self._log_writer.writerow([
                f"{_t.time():.3f}",
                f"{self.roll_deg:.2f}",f"{self.pitch_deg:.2f}",
                f"{self.yaw_deg:.2f}",f"{self.lat:.7f}",f"{self.lon:.7f}",
                f"{self.alt_m:.1f}",self.gps_fix,self.satellites,
                f"{self.groundspeed:.2f}",f"{self.climb_rate:.2f}",
                self.throttle_pct,f"{self.batt_voltage:.2f}",
                f"{self.batt_current:.2f}",self.batt_pct,
                int(self.armed),self.flight_mode])
        except Exception: pass

    def _reader_thread(self, port, baud):
        try:
            from pymavlink import mavutil
        except ImportError:
            self._on_status(
                "pymavlink not installed — run: pip install pymavlink",
                "orange")
            self._running = False
            return

        self._on_status(f"Telemetry: connecting {port} @ {baud}...", "orange")
        try:
            self._conn = mavutil.mavlink_connection(
                port, baud=baud, source_system=255)
        except Exception as e:
            self._on_status(f"Telemetry: {e}", "orange")
            self._running = False
            return

        self._on_status("Telemetry: waiting for heartbeat...", "orange")
        try:
            hb = self._conn.wait_heartbeat(timeout=10)
        except Exception as e:
            self._on_status(f"Telemetry: heartbeat error — {e}", "orange")
            self._running = False
            return

        if not hb:
            self._on_status("Telemetry: no heartbeat — check wiring/baud", "orange")
            self._running = False
            return

        self.connected = True
        self._on_status(
            f"Telemetry: connected  sys={self._conn.target_system}", "green")
        self._request_streams()

        import time as _t, math as _m
        last_hb = last_log = 0.0

        while self._running:
            now = _t.time()
            if now - last_hb >= 1.0:
                try:
                    self._conn.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                        0, 0, 0)
                except Exception: pass
                last_hb = now

            try:
                msg = self._conn.recv_match(blocking=True, timeout=0.5)
            except Exception:
                break
            if not msg: continue
            mt = msg.get_type()
            self.last_msg_time = now

            if   mt == "HEARTBEAT":        self._parse_hb(msg, mavutil)
            elif mt == "ATTITUDE":
                self.roll_deg    = _m.degrees(msg.roll)
                self.pitch_deg   = _m.degrees(msg.pitch)
                self.yaw_deg     = _m.degrees(msg.yaw)
                self.rollspeed   = _m.degrees(msg.rollspeed)
                self.pitchspeed  = _m.degrees(msg.pitchspeed)
                self.yawspeed    = _m.degrees(msg.yawspeed)
            elif mt == "GPS_RAW_INT":
                self.lat        = msg.lat / 1e7
                self.lon        = msg.lon / 1e7
                self.alt_m      = msg.alt / 1000.0
                self.gps_fix    = msg.fix_type
                self.satellites = msg.satellites_visible
                self.hdop       = msg.eph / 100.0 if msg.eph != 65535 else 99.9
            elif mt == "VFR_HUD":
                self.groundspeed  = msg.groundspeed
                self.airspeed     = msg.airspeed
                self.climb_rate   = msg.climb
                self.throttle_pct = msg.throttle
            elif mt == "BATTERY_STATUS":
                if msg.voltages and msg.voltages[0] != 65535:
                    self.batt_voltage = msg.voltages[0] / 1000.0
                self.batt_current = (msg.current_battery / 100.0
                                     if msg.current_battery != -1 else 0.0)
                self.batt_pct = msg.battery_remaining
            elif mt == "SYS_STATUS":
                if self.batt_voltage == 0.0:
                    self.batt_voltage = msg.voltage_battery / 1000.0
            elif mt == "RC_CHANNELS":
                for i in range(min(18, msg.chancount)):
                    v = getattr(msg, f"chan{i+1}_raw", 0)
                    self.rc_chan[i] = v
            elif mt == "NAMED_VALUE_FLOAT":
                self.named_values[msg.name.rstrip("\x00")] = msg.value
            elif mt == "STATUSTEXT":
                self.statustext = msg.text.rstrip("\x00")
                log.info(f"FC statustext: {self.statustext}")

            if self._log_writer and now - last_log >= 0.5:
                self._log_row(); last_log = now

        self.connected = False
        self._on_status("Telemetry: disconnected", "gray")

    def _parse_hb(self, msg, mavutil):
        self.armed = bool(
            msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        self.vehicle_type = {
            1:"Fixed Wing", 2:"Quadrotor", 3:"Coaxial",
            4:"Helicopter", 10:"Ground Rover", 11:"Boat",
            13:"Hexarotor", 14:"Octorotor", 15:"Tricopter",
        }.get(msg.type, f"Type {msg.type}")

    def _request_streams(self):
        try:
            from pymavlink import mavutil as _mu
            for name, rate in self.STREAM_RATES.items():
                mid = getattr(_mu.mavlink, f"MAVLINK_MSG_ID_{name}", None)
                if mid is None: continue
                self._conn.mav.command_long_send(
                    self._conn.target_system,
                    self._conn.target_component,
                    _mu.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0, float(mid), 1e6 / rate, 0, 0, 0, 0, 0)
        except Exception as e:
            log.warning(f"Stream request: {e}")
