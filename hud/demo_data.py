"""
ShinAgent HUD Demo Mode
=======================
Spoofed telemetry and state data for UI layout and development.
Run with: python hud/hud.py --demo

Self-contained -- no imports from the rest of the imq2 codebase, so this
works even without Q2 installed or reachable.
"""

import math
import time

# --module's CLI choices aren't the same set of strings as Q2's real
# personality-profile/module names (there is no 'freeroam' or 'retro'
# profile -- freeroam is a Forza driving_mode *within* the race_engineer
# profile, and Retro is a HUD-local tab with no active-profile concept at
# all). These two maps translate a --module pick to what a real /api/state
# response would actually say, so the module indicator and profile label
# behave exactly as they would live.
_MODULE_TO_INDICATOR_KEY = {
    "race_engineer": "race_engineer",
    "freeroam": "race_engineer",
    "first_officer": "first_officer",
    "ship_computer": "ship_computer",
    "f1_watchalong": "watchalong",
    "ufc_watchalong": "watchalong",
    "popup_video": "popup_video",
    "whiplash": "whiplash",
    "beavis_butthead": "beavis_butthead",
    "circuit_builder": "circuit_builder",
    "retro": "default",
}
_MODULE_TO_PROFILE_FILE = {
    "race_engineer": "profiles/race_engineer.yaml",
    "freeroam": "profiles/race_engineer.yaml",
    "first_officer": "profiles/first_officer.yaml",
    "ship_computer": "profiles/ship_computer.yaml",
    "f1_watchalong": "profiles/watchalong_live.yaml",
    "ufc_watchalong": "profiles/watchalong_live.yaml",
    "popup_video": "profiles/popup_video.yaml",
    "whiplash": "profiles/whiplash.yaml",
    "beavis_butthead": "profiles/beavis_butthead.yaml",
    "circuit_builder": "profiles/circuit_builder.yaml",
    "retro": "profiles/default.yaml",
}


# ── Animated demo state ───────────────────────────────────────
# Values that change over time to make the demo feel alive

class DemoAnimator:
    """Generates smoothly animated fake telemetry values."""

    def __init__(self):
        self._start = time.time()

    def _t(self):
        return time.time() - self._start

    def sine(self, min_val, max_val, period=5.0, offset=0.0):
        t = self._t()
        s = math.sin((t + offset) * 2 * math.pi / period)
        return min_val + (max_val - min_val) * (s * 0.5 + 0.5)

    def pulse(self, period=2.0, offset=0.0):
        """0.0 to 1.0 pulsing value."""
        return self.sine(0.0, 1.0, period, offset)

    def ramp(self, period=10.0):
        """Sawtooth 0.0 to 1.0."""
        return (self._t() % period) / period

    def rpm(self):
        """Fake RPM that revs and shifts."""
        phase = (self._t() % 6.0) / 6.0
        if phase < 0.7:
            return 3000 + phase / 0.7 * 4500
        else:
            return 3000 + (phase - 0.7) / 0.3 * 500

    def speed_kmh(self):
        return self.sine(80, 210, period=12.0)

    def drift_angle(self):
        """Occasionally non-zero drift angle."""
        t = self._t()
        if math.sin(t * 0.3) > 0.6:
            return self.sine(15, 55, period=3.0)
        return 0.0

    def tyre_temp(self, corner_offset=0.0):
        """Tyre temp that varies per corner."""
        base = self.sine(75, 105, period=8.0, offset=corner_offset)
        return round(base, 1)

    def tyre_wear(self, corner_offset=0.0):
        """Slowly decreasing tyre wear."""
        base = 0.85 - (self._t() % 120) / 120 * 0.35
        jitter = math.sin(self._t() * 0.5 + corner_offset) * 0.03
        return round(max(0.3, base + jitter), 3)

    def lap_time_ms(self, base_ms=94000):
        """Lap time that varies slightly."""
        return int(base_ms + self.sine(-1200, 1200, period=7.0))

    def race_position(self):
        """Position that changes occasionally."""
        t = self._t()
        if t % 20 < 3:
            return 2
        elif t % 30 < 5:
            return 3
        return 1

    def altitude(self):
        return self.sine(850, 1200, period=15.0)

    def heading(self):
        return (self._t() * 3.0) % 360

    def fuel_pct(self):
        return max(0.05, 0.85 - (self._t() % 90) / 90 * 0.75)

    def ed_fuel_main(self):
        return self.sine(6.0, 14.0, period=20.0)


_anim = DemoAnimator()


def get_demo_state(module: str = "race_engineer") -> dict:
    """
    Return a complete spoofed /api/state response. Called every poll cycle
    in demo mode. 'module' is normalized to a real module-indicator key and
    profile path -- see _MODULE_TO_INDICATOR_KEY/_MODULE_TO_PROFILE_FILE
    above for why that's not just the raw --module string.
    """
    t = time.time()
    speak_phase = math.sin(t * 0.4)
    think_phase = math.sin(t * 0.15)

    q2_state = {
        "speaking": speak_phase > 0.85,
        "listening": False,
        "thinking": think_phase > 0.92,
    }

    return {
        "ok": True,
        "connected": True,
        "demo_mode": True,
        "module": _MODULE_TO_INDICATOR_KEY.get(module, "default"),
        "profile": _MODULE_TO_PROFILE_FILE.get(module, "profiles/default.yaml"),
        "q2_state": q2_state,
        "llm_backend": "gemini",
        "face_style": 1,
        "telemetry": _get_telemetry(module),
    }


def _get_telemetry(module: str) -> dict:
    a = _anim

    if module == "race_engineer":
        return _race_engineer_telemetry(a)
    elif module == "freeroam":
        return _freeroam_telemetry(a)
    elif module == "first_officer":
        return _first_officer_telemetry(a)
    elif module in ("ship_computer", "f1_watchalong", "ufc_watchalong", "whiplash", "beavis_butthead", "circuit_builder"):
        return None  # ED, F1/UFC watchalong, Whiplash, BB, and Circuit Builder each have their own state endpoint
    else:
        return _race_engineer_telemetry(a)  # default (also covers popup_video/retro)


def _race_engineer_telemetry(a: DemoAnimator) -> dict:
    rpm = a.rpm()
    rpm_max = 8500
    fuel = a.fuel_pct()
    pos = int(a.race_position())
    lap = int(3 + (time.time() % 60) / 60 * 5)

    return {
        "source": "forza",
        "speed_kmh": round(a.speed_kmh(), 1),
        "gear": int(a.sine(2, 6, period=6.0)),
        "rpm": int(rpm),
        "rpm_max": rpm_max,
        "fuel_pct": round(fuel * 100, 1),
        "fuel_laps": round(fuel * 12, 1),
        "position": pos,
        "lap": lap,
        "current_lap_ms": a.lap_time_ms(62000),
        "last_lap_ms": 93271,
        "best_lap_ms": 91834,
        "delta_lap_ms": int(a.sine(-800, 1200, period=9.0)),
        "tyre_compound": "SOFT",

        "tire_temp_fl": a.tyre_temp(0.0), "tire_temp_fr": a.tyre_temp(1.1),
        "tire_temp_rl": a.tyre_temp(2.2), "tire_temp_rr": a.tyre_temp(3.3),

        "tyre_wear_fl": a.tyre_wear(0.0), "tyre_wear_fr": a.tyre_wear(1.1),
        "tyre_wear_rl": a.tyre_wear(2.2), "tyre_wear_rr": a.tyre_wear(3.3),

        "driving_mode": "race",
        "race_summary": {
            "position": pos,
            "start_position": 3,
            "position_delta": 3 - pos,
            "lap": lap,
            "max_lap_seen": lap,
            "race_time": "14:23.4",
            "current_lap_time": _fmt_ms(a.lap_time_ms(62000)),
            "last_lap_time": "1:33.271",
            "best_lap_time": "1:31.834",
            "speed_kmh": round(a.speed_kmh(), 1),
            "is_leading": pos == 1,
            "positions_gained": max(0, 3 - pos),
            "positions_lost": max(0, pos - 3),
        },

        "drift": {
            "drifting": False, "drift_duration": 0, "drift_angle": 0,
            "peak_yaw_dps": 0, "recent_event": None, "recent_drifts": [],
        },
        "air": {"airborne": False, "air_duration": 0, "recent_event": None},
    }


def _freeroam_telemetry(a: DemoAnimator) -> dict:
    drift_angle = a.drift_angle()
    drifting = drift_angle > 5.0

    return {
        "source": "forza",
        "speed_kmh": round(a.speed_kmh(), 1),
        "gear": int(a.sine(2, 5, period=5.0)),
        "rpm": int(a.rpm()),
        "rpm_max": 7200,
        "fuel_pct": round(a.fuel_pct() * 100, 1),
        "position": 0,
        "lap": 0,
        "current_lap_ms": 0, "last_lap_ms": 0, "best_lap_ms": 0, "delta_lap_ms": 0,

        "pos_x": a.sine(-5000, 5000, period=30.0),
        "pos_y": a.sine(50, 200, period=20.0),
        "pos_z": a.sine(-8000, 8000, period=25.0),

        "tire_temp_fl": a.tyre_temp(0.0), "tire_temp_fr": a.tyre_temp(1.1),
        "tire_temp_rl": a.tyre_temp(2.2), "tire_temp_rr": a.tyre_temp(3.3),

        "driving_mode": "freeroam",
        "race_summary": None,

        "drift": {
            "drifting": drifting,
            "drift_duration": round(a.sine(0, 4, period=4.0), 1) if drifting else 0,
            "drift_angle": round(drift_angle, 1),
            "peak_yaw_dps": round(a.sine(30, 120, period=3.0), 1) if drifting else 0,
            "recent_event": None,
            "recent_drifts": [
                {"score": "clean", "duration": 3.2, "angle": 42.0, "peak_yaw": 87.0},
                {"score": "nice", "duration": 1.8, "angle": 31.0, "peak_yaw": 61.0},
                {"score": "insane", "duration": 5.1, "angle": 67.0, "peak_yaw": 134.0},
            ],
        },
        "air": {
            "airborne": a.pulse(period=15.0) > 0.92,
            "air_duration": round(a.sine(0, 2.5, period=3.0), 2),
            "recent_event": None,
        },
        "location": "Yokohama Docks",
    }


def _first_officer_telemetry(a: DemoAnimator) -> dict:
    altitude = a.altitude()
    speed_kts = a.sine(180, 290, period=20.0)
    vs = a.sine(-800, 800, period=12.0)
    heading = a.heading()

    return {
        "source": "msfs",
        "altitude_ft": round(altitude),
        "airspeed_kts": round(speed_kts, 1),
        "heading_deg": round(heading, 1),
        "vertical_speed_fpm": round(vs),
        "pitch_deg": round(a.sine(-3, 8, period=10.0), 1),
        "bank_deg": round(a.sine(-15, 15, period=8.0), 1),
        "fuel_pct": round(a.fuel_pct() * 100 + 20, 1),
        "n1_pct": round(a.sine(72, 88, period=6.0), 1),
        "gear_down": altitude < 1000,
        "flaps": 0 if altitude > 3000 else 2,
        "autopilot_active": True,
        "ap_altitude": 8000,
        "ap_heading": 275,
        "ap_airspeed": 250,
        "phase": "CRUISE",
        "com1_mhz": 118.300,
        "nav1_mhz": 110.300,
        "sim_rate": 1.0,
    }


def get_demo_acc_setups() -> dict:
    """Spoofed ACC setup list."""
    return {
        "ok": True,
        "setups": [
            {
                "id": 1, "name": "Ferrari 296 Spa Sprint Dry",
                "car": "ferrari_296_gt3", "track": "spa",
                "session_type": "sprint", "weather": "dry", "ambient_temp": 22,
                "notes": "High downforce setup. Tyre pressures set for 22C "
                         "ambient targeting 27.6 PSI hot.",
                "created_at": "2026-07-01T14:23:00", "applied_at": "2026-07-01T14:25:00",
                "is_favourite": True,
            },
            {
                "id": 2, "name": "Lambo Monza Sprint Dry",
                "car": "lamborghini_huracan_gt3_evo2", "track": "monza",
                "session_type": "sprint", "weather": "dry", "ambient_temp": 28,
                "notes": "Low drag setup for Monza. Minimum wing.",
                "created_at": "2026-06-28T18:11:00", "applied_at": None,
                "is_favourite": False,
            },
            {
                "id": 3, "name": "Porsche 992 Nurburgring Endurance",
                "car": "porsche_992_gt3_r", "track": "nurburgring",
                "session_type": "endurance", "weather": "mixed", "ambient_temp": 16,
                "notes": "Conservative tyre wear setup. Wet tyre pressures in strategy.",
                "created_at": "2026-06-25T09:44:00", "applied_at": "2026-06-25T09:46:00",
                "is_favourite": False,
            },
        ],
    }


def get_demo_ed_state() -> dict:
    """Spoofed Elite Dangerous state."""
    a = _anim
    return {
        "active": True,
        "state": {
            "commander": "CMDR ShinobiFPV",
            "ship": "Krait Phantom",
            "ship_id": "KP-28T",
            "credits": 4823917,
            "location": {
                "system": "Shinrarta Dezhra", "station": "Jameson Memorial",
                "body": None, "docked": True, "landed": False, "supercruise": False,
            },
            "fuel": {
                "main": round(a.ed_fuel_main(), 1), "reservoir": 0.5,
                "capacity": 16.0, "low": False,
            },
            "status": {
                "shields_up": True, "hardpoints": False, "silent_running": False,
                "cargo_scoop": False, "overheating": False, "in_danger": False,
                "being_interdicted": False, "legal_state": "Clean",
                "pips": [2, 2, 2], "cargo": 16,
            },
            "recent_events": [
                {"time": "14:23", "type": "FSDJump", "text": "Jumped to Shinrarta Dezhra"},
                {"time": "14:18", "type": "Scan",
                 "text": "Scanned Earth-like World (2,450 ls) -- estimated 8.2M Cr"},
                {"time": "14:12", "type": "Bounty", "text": "Bounty collected: 45,000 Cr (Federation)"},
                {"time": "14:08", "type": "Docked", "text": "Docked at Jameson Memorial"},
                {"time": "13:55", "type": "FSDJump", "text": "Jumped to Sol"},
            ],
        },
        "last_q2_response": "Docking confirmed, Commander. Jameson Memorial services available.",
        "last_response_time": time.time() - 45,
    }


def get_demo_f1_state() -> dict:
    """Spoofed F1 watchalong status."""
    return {
        "active": True,
        "session_name": "Monaco Grand Prix -- Race",
        "race_control": [
            "GREEN LIGHT - TRACK CLEAR",
            "DRS ENABLED",
            "CAR 1 (VER) FASTEST LAP 1:14.260",
        ],
    }


def get_demo_ufc_state() -> dict:
    """Spoofed UFC watchalong status."""
    return {
        "active": True,
        "event_name": "UFC 300",
        "main_event": "Islam Makhachev vs Leon Edwards -- Lightweight",
    }


def get_demo_popup_state() -> dict:
    """Spoofed Pop-Up Video state."""
    return {
        "session": {"title": "Hackers", "year": 1995, "total_popups": 32, "delivered": 7},
        "recent_popup": {
            "timestamp_display": "7:00", "type": "MUSIC",
            "title": "Voodoo People -- The Prodigy",
            "body": "Liam Howlett composed this in 1994 for Music for the Jilted "
                    "Generation, which went to #1 in the UK. Iain Softley heard it "
                    "in London and knew immediately.",
        },
        "upcoming": [
            {"timestamp_display": "9:00", "title": "Cyberspace visuals not CGI"},
            {"timestamp_display": "12:00", "title": "Jolie and Miller fell in love"},
            {"timestamp_display": "15:00", "title": "Stuyvesant High School filming"},
        ],
    }


def get_demo_whiplash_state() -> dict:
    """Spoofed Whiplash state -- same shape as webapp/server.py's real
    /whiplash/state: an animated BPM (metronome actually running),
    realistic-looking timing stats, occasional MIDI hits, and a demo
    Clone Hero now-playing song."""
    a = _anim
    bpm = round(a.sine(95, 115, period=20.0))
    hit_pulse = a.pulse(period=1.5)
    return {
        "metronome": {"running": True, "bpm": bpm, "synced": True},
        "groove": {"active": True, "name": "Funky Drummer", "artist_credit": "Clyde Stubblefield -- James Brown, 1970"},
        "midi": {
            "available": True,
            "running": True,
            "port": "CME H4MIDI 1",
            "hit_count": 214,
            "last_hits": (
                [{"piece": "kick", "velocity": 102}, {"piece": "hihat_closed", "velocity": 68}]
                if hit_pulse > 0.5 else
                [{"piece": "snare", "velocity": 96}, {"piece": "hihat_closed", "velocity": 64}]
            ),
        },
        "timing_stats": {
            "count": 128,
            "avg_abs_deviation_ms": round(a.sine(4.0, 11.0, period=15.0), 1),
            "worst_deviation_ms": 22.0,
            "worst_piece": "snare",
            "pocket_count": 96,
            "pocket_pct": round(a.sine(70, 92, period=15.0)),
            "rushing_count": 9,
            "dragging_count": 14,
        },
        "clone_hero": {"artist": "Rush", "song": "Tom Sawyer"},
    }


def get_demo_bb_candidates() -> dict:
    """Spoofed Beavis and Butthead candidate list -- 20 of the real
    curated pool (self-contained copy so this file stays import-free
    from the rest of the codebase, per this module's docstring)."""
    candidates = [
        {"title": "Enter Sandman", "artist": "Metallica", "category": "metal_they_love"},
        {"title": "Walk", "artist": "Pantera", "category": "metal_they_love"},
        {"title": "Smells Like Teen Spirit", "artist": "Nirvana", "category": "metal_they_love"},
        {"title": "Bulls on Parade", "artist": "Rage Against the Machine", "category": "metal_they_love"},
        {"title": "MMMBop", "artist": "Hanson", "category": "pop_they_hate"},
        {"title": "Barbie Girl", "artist": "Aqua", "category": "pop_they_hate"},
        {"title": "Wannabe", "artist": "Spice Girls", "category": "pop_they_hate"},
        {"title": "Macarena", "artist": "Los Del Rio", "category": "pop_they_hate"},
        {"title": "Sabotage", "artist": "Beastie Boys", "category": "confused_by"},
        {"title": "Virtual Insanity", "artist": "Jamiroquai", "category": "confused_by"},
        {"title": "Take On Me", "artist": "a-ha", "category": "confused_by"},
        {"title": "Thriller", "artist": "Michael Jackson", "category": "confused_by"},
        {"title": "Friends in Low Places", "artist": "Garth Brooks", "category": "country_they_despise"},
        {"title": "Achy Breaky Heart", "artist": "Billy Ray Cyrus", "category": "country_they_despise"},
        {"title": "November Rain", "artist": "Guns N Roses", "category": "classic_rock_moments"},
        {"title": "Panama", "artist": "Van Halen", "category": "classic_rock_moments"},
        {"title": "Paradise City", "artist": "Guns N Roses", "category": "classic_rock_moments"},
        {"title": "Fight the Power", "artist": "Public Enemy", "category": "rap_mixed_reaction"},
        {"title": "Jump", "artist": "Kris Kross", "category": "rap_mixed_reaction"},
        {"title": "Ice Ice Baby", "artist": "Vanilla Ice", "category": "rap_mixed_reaction"},
    ]
    return {"ok": True, "candidates": candidates}


def get_demo_bb_session() -> dict:
    """Spoofed active Beavis and Butthead session -- mid-playlist."""
    selected = [
        {"title": "Walk", "artist": "Pantera", "category": "metal_they_love"},
        {"title": "Thriller", "artist": "Michael Jackson", "category": "confused_by"},
        {"title": "Barbie Girl", "artist": "Aqua", "category": "pop_they_hate"},
        {"title": "Panama", "artist": "Van Halen", "category": "classic_rock_moments"},
        {"title": "Ice Ice Baby", "artist": "Vanilla Ice", "category": "rap_mixed_reaction"},
    ]
    return {
        "active": True,
        "session_id": "demo",
        "nice_guy": False,
        "q2_is": "butthead",
        "current_video": selected[0],
        "current_idx": 0,
        "total": len(selected),
        "selected": selected,
    }


def get_demo_bb_replay_list() -> dict:
    """Spoofed replay list."""
    return {"list": [
        {"title": "Enter Sandman", "artist": "Metallica", "play_count": 4},
        {"title": "Panama", "artist": "Van Halen", "play_count": 2},
    ]}


def get_demo_circuit_project() -> dict:
    """Demo circuit project -- ESP32 with NeoPixels and PIR."""
    return {
        "project_id": "demo001",
        "title": "Smart NeoPixel Motion Lamp",
        "description": "ESP32 + PIR sensor + WS2812B NeoPixels. LEDs animate when motion detected.",
        "created_at": time.time(),
        "components": [
            {"instance_id": "U1", "component_id": "esp32_devkit", "label": "ESP32 DevKit",
             "x": 0.45, "y": 0.5, "notes": "Main controller"},
            {"instance_id": "PIR1", "component_id": "pir_motion", "label": "PIR Sensor",
             "x": 0.15, "y": 0.3, "notes": "Motion detection"},
            {"instance_id": "LED1", "component_id": "ws2812b", "label": "NeoPixel Strip (12x)",
             "x": 0.75, "y": 0.3, "notes": "12 LEDs"},
            {"instance_id": "R1", "component_id": "resistor", "label": "330 ohm",
             "x": 0.62, "y": 0.3, "notes": "Data line resistor"},
            {"instance_id": "C1", "component_id": "capacitor", "label": "1000uF",
             "x": 0.75, "y": 0.65, "notes": "Power decoupling"},
        ],
        "connections": [
            {"from_instance": "U1", "from_pin": "GPIO4", "to_instance": "PIR1", "to_pin": "OUT",
             "wire_color": "yellow", "note": "Motion signal"},
            {"from_instance": "U1", "from_pin": "3V3", "to_instance": "PIR1", "to_pin": "VCC",
             "wire_color": "red"},
            {"from_instance": "U1", "from_pin": "GND", "to_instance": "PIR1", "to_pin": "GND",
             "wire_color": "black"},
            {"from_instance": "U1", "from_pin": "GPIO14", "to_instance": "R1", "to_pin": "A",
             "wire_color": "green", "note": "NeoPixel data"},
            {"from_instance": "R1", "from_pin": "B", "to_instance": "LED1", "to_pin": "DIN",
             "wire_color": "green"},
            {"from_instance": "U1", "from_pin": "VIN", "to_instance": "LED1", "to_pin": "VCC",
             "wire_color": "red", "note": "5V from USB"},
            {"from_instance": "U1", "from_pin": "GND", "to_instance": "LED1", "to_pin": "GND",
             "wire_color": "black"},
            {"from_instance": "LED1", "from_pin": "VCC", "to_instance": "C1", "to_pin": "+",
             "wire_color": "red"},
            {"from_instance": "LED1", "from_pin": "GND", "to_instance": "C1", "to_pin": "-",
             "wire_color": "black"},
        ],
        "code": _DEMO_CIRCUIT_CODE,
        "code_language": "arduino_cpp",
        "warnings": [
            "PIR sensor needs 30 second warmup on first power",
            "NeoPixels at 5V -- connected to ESP32 VIN (5V USB)",
            "Add 330 ohm resistor on data line to protect first pixel",
            "Add 1000uF capacitor across NeoPixel VCC/GND",
            "More than 20 LEDs at full brightness needs external 5V supply",
        ],
        "build_steps": [
            "Connect PIR VCC to ESP32 3.3V (red wire)",
            "Connect PIR GND to ESP32 GND (black wire)",
            "Connect PIR OUT to ESP32 GPIO4 (yellow wire)",
            "Solder 330 ohm resistor to ESP32 GPIO14",
            "Connect resistor other end to NeoPixel DIN (green wire)",
            "Connect NeoPixel 5V to ESP32 VIN (red wire)",
            "Connect NeoPixel GND to ESP32 GND (black wire)",
            "Solder 1000uF capacitor across NeoPixel 5V/GND",
            "Install Adafruit NeoPixel library in Arduino IDE",
            "Upload code and test",
            "Wait 30 seconds for PIR to calibrate",
        ],
        "bom": [
            {"qty": 1, "part": "ESP32 DevKit V1", "notes": "Any 38-pin ESP32 board"},
            {"qty": 1, "part": "HC-SR501 PIR Motion Sensor"},
            {"qty": 1, "part": "WS2812B NeoPixel Strip 12 LEDs", "notes": "60 LED/m density"},
            {"qty": 1, "part": "330 ohm resistor"},
            {"qty": 1, "part": "1000uF 10V+ capacitor"},
            {"qty": 1, "part": "USB-C cable + 5V 2A adapter"},
            {"qty": 1, "part": "Jumper wires assorted"},
        ],
        "libraries": ["Adafruit NeoPixel (search 'NeoPixel' in Arduino Library Manager)"],
    }


_DEMO_CIRCUIT_CODE = """#include <Adafruit_NeoPixel.h>

#define PIR_PIN    4
#define PIXEL_PIN  14
#define NUM_PIXELS 12

Adafruit_NeoPixel strip(NUM_PIXELS, PIXEL_PIN, NEO_GRB + NEO_KHZ800);

bool motionActive = false;
unsigned long lastMotion = 0;
const int TIMEOUT_MS = 10000;  // 10s after motion

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);

  strip.begin();
  strip.setBrightness(80);
  strip.show();

  Serial.println("Smart NeoPixel Motion Lamp ready.");
  Serial.println("Waiting 30s for PIR warmup...");
  delay(30000);
  Serial.println("Ready!");
}

void loop() {
  bool motion = digitalRead(PIR_PIN) == HIGH;

  if (motion) {
    lastMotion = millis();
    if (!motionActive) {
      motionActive = true;
      Serial.println("Motion detected!");
    }
  }

  if (motionActive) {
    if (millis() - lastMotion > TIMEOUT_MS) {
      motionActive = false;
      fadeOff();
    } else {
      rainbowChase();
    }
  } else {
    breathe(0, 0, 50);  // slow blue breathing
  }
}

void rainbowChase() {
  static uint16_t hue = 0;
  for (int i = 0; i < NUM_PIXELS; i++) {
    strip.setPixelColor(i, strip.gamma32(
      strip.ColorHSV(hue + i * (uint16_t)(65536 / NUM_PIXELS))));
  }
  strip.show();
  hue += 256;
  delay(20);
}

void breathe(uint8_t r, uint8_t g, uint8_t b) {
  float breathAmt = (sin(millis() / 2000.0 * PI) + 1.0) / 2.0;
  strip.fill(strip.Color(r * breathAmt, g * breathAmt, b * breathAmt));
  strip.show();
}

void fadeOff() {
  for (int level = 80; level >= 0; level -= 2) {
    strip.setBrightness(level);
    strip.show();
    delay(20);
  }
  strip.fill(0);
  strip.show();
  strip.setBrightness(80);
}
"""


def get_demo_forza_locations() -> list:
    """Spoofed FH6 landmark list for the HUD's Locations tab."""
    return [
        {"name": "Shibuya Crossing", "region": "Tokyo City", "type": "landmark",
         "tags": ["iconic", "urban"], "notes": "The famous scramble crossing.",
         "source": "builtin", "visits": 0},
        {"name": "Daikoku PA", "region": "Tokyo City", "type": "parking",
         "tags": ["iconic", "car_culture"], "notes": "Highway rest stop, real Japanese car culture landmark.",
         "source": "builtin", "visits": 0},
        {"name": "Mt. Haruna (Akina)", "region": "Hokubu", "type": "mountain",
         "tags": ["touge", "drift", "iconic"], "notes": "The mountain that inspired Akina in Initial D.",
         "source": "builtin", "visits": 0},
        {"name": "My Drift Spot", "region": "Ohtani", "type": "drift_zone",
         "tags": [], "notes": "Marked while playing", "source": "personal", "visits": 4},
        {"name": "Community Touge Run", "region": "Hokubu", "type": "mountain",
         "tags": ["touge"], "notes": "Shared by another player.", "source": "initial_d_fan_map", "visits": 0},
    ]


def get_demo_forza_summary() -> dict:
    """Spoofed FH6 location summary for the HUD's Locations tab."""
    return {
        "total": 23,
        "by_source": {"builtin": 10, "personal": 4, "initial_d_fan_map": 9},
        "regions": ["Hokubu", "Ito", "Ohtani", "Takashiro", "Tokyo City"],
    }


def get_demo_forza_nearby() -> dict:
    """Spoofed nearby-landmarks response for the HUD's Locations tab."""
    return {
        "nearby": [
            {"name": "Daikoku PA", "region": "Tokyo City", "type": "parking",
             "source": "builtin", "distance": 180},
            {"name": "C1 Loop", "region": "Tokyo City", "type": "race",
             "source": "builtin", "distance": 340},
            {"name": "My Drift Spot", "region": "Ohtani", "type": "drift_zone",
             "source": "personal", "distance": 460},
        ]
    }


def get_demo_bridge_status() -> dict:
    """Spoofed bridge status for demo."""
    return {
        "platform_warning": False,
        "bridges": {
            "ac": {"name": "Assetto Corsa Bridge", "running": True, "pid": 12345,
                   "port": 8001, "icon": "AC", "color": "#ff4400", "script": "windows/ac_bridge.py"},
            "msfs": {"name": "MSFS Bridge", "running": False, "pid": None,
                     "port": 8002, "icon": "FS", "color": "#0088ff", "script": "windows/msfs_bridge.py"},
            "ed": {"name": "Elite Dangerous Bridge", "running": False, "pid": None,
                   "port": 8003, "icon": "ED", "color": "#ff8c00", "script": "windows/ed_bridge.py"},
            "acc_setup": {"name": "ACC Setup Manager", "running": True, "pid": 12346,
                          "port": 8092, "icon": "STP", "color": "#e8002a", "script": "windows/acc_setup_manager.py"},
        },
    }


def get_demo_games() -> dict:
    """Spoofed detected games."""
    return {"games": [{"process": "acc.exe", "name": "Assetto Corsa Competizione", "bridge": "ac", "pid": 9876}]}


def get_demo_retro_status() -> dict:
    return {
        "platform_warning": False,
        "retroarch_found": True,
        "retroarch_path": r"C:\RetroArch-Win64\retroarch.exe",
        "roms_found": True,
        "rom_folder": r"C:\ROMs",
        "p2_available": True,
        "ra_connected": True,
        "ra_status": "PLAYING",
        "current_game": "Street Fighter II",
        "current_system": "snes",
    }


def get_demo_retro_games() -> dict:
    games = [
        {"name": "Street Fighter II", "system": "snes", "path": r"C:\ROMs\snes\Street Fighter II.sfc"},
        {"name": "Super Mario Bros", "system": "nes", "path": r"C:\ROMs\nes\Super Mario Bros.nes"},
        {"name": "Sonic the Hedgehog", "system": "genesis", "path": r"C:\ROMs\genesis\Sonic the Hedgehog.md"},
    ]
    return {"games": games, "count": len(games)}


def get_demo_stats(sport: str) -> dict:
    """Spoofed data for the HUD Stats Hub tab, one shape per sport
    matching exactly what hud_server.py's real /api/stats/<sport>
    returns (see that route's shape for f1/ufc/nba/nhl, and
    integrations/formula_drift_data.py + xgames_data.py for FD/XG)."""
    a = _anim

    if sport == "formula_drift":
        return {
            "standings": [
                {"position": 1, "name": "James Deane", "points": 300, "car": "Ford Mustang RTR Spec 5-FD", "country": "Ireland"},
                {"position": 2, "name": "Matt Field", "points": 210, "car": "Corvette", "country": "USA"},
                {"position": 3, "name": "Fredric Aasbo", "points": 200, "car": "Toyota GR Supra", "country": "Norway"},
                {"position": 4, "name": "Hiroya Minowa", "points": 190, "car": "Toyota GT86", "country": "Japan"},
                {"position": 5, "name": "Adam LZ", "points": 190, "car": "BMW E36", "country": "USA"},
            ],
            "schedule": [
                {"round": 1, "name": "Midwest Mayhem", "location": "Indianapolis, IN", "date": "April 2026", "status": "complete"},
                {"round": 2, "name": "Seattle", "location": "Seattle, WA", "date": "May 2026", "status": "complete"},
                {"round": 3, "name": "Las Vegas", "location": "Las Vegas, NV", "date": "June 2026", "status": "upcoming"},
            ],
        }

    elif sport == "xgames":
        return {
            "results": [
                {"event": "Men's Snowboard SuperPipe", "gold": "Scotty James", "score": "96.00", "discipline": "snowboard"},
                {"event": "Women's Snowboard SuperPipe", "gold": "Chloe Kim", "score": "95.00", "discipline": "snowboard"},
                {"event": "Men's Ski SuperPipe", "gold": "Birk Ruud", "score": "94.00", "discipline": "ski"},
                {"event": "Men's Snowboard Slopestyle", "gold": "Red Gerard", "score": "89.50", "discipline": "snowboard"},
                {"event": "Women's Snowboard Slopestyle", "gold": "Zoi Sadowski-Synnott", "score": "91.00", "discipline": "snowboard"},
                {"event": "Men's Skateboard Street", "gold": "Nyjah Huston", "score": "93.50", "discipline": "skateboard"},
                {"event": "Women's Ski SuperPipe", "gold": "Eileen Gu", "score": "95.66", "discipline": "ski"},
                {"event": "Men's BMX Park", "gold": "Courage Adams", "score": "91.00", "discipline": "bmx"},
            ]
        }

    elif sport == "f1":
        return {
            "race_status": {
                "connected": True,
                "event_name": "British Grand Prix",
                "period_str": f"Lap {int(a.sine(20, 50, 15.0))} / 52",
                "time": "1:32.4",
            },
            "standings": [
                {"name": "Max Verstappen", "team": "Red Bull", "points": 301},
                {"name": "Lando Norris", "team": "McLaren", "points": 247},
                {"name": "Charles Leclerc", "team": "Ferrari", "points": 235},
                {"name": "Carlos Sainz", "team": "Ferrari", "points": 210},
                {"name": "Lewis Hamilton", "team": "Mercedes", "points": 192},
            ],
        }

    elif sport == "ufc":
        return {
            "event": {"name": "UFC 315", "venue": "T-Mobile Arena", "date": "July 12, 2026"},
            "current_fight": {
                "fighter1": "Islam Makhachev", "record1": "28-1",
                "fighter2": "Dustin Poirier", "record2": "30-8",
                "weight_class": "Lightweight Championship",
            },
        }

    elif sport == "nba":
        hs, as_ = int(a.sine(85, 115, 12.0)), int(a.sine(80, 110, 11.0))
        return {
            "game_status": {
                "active": True, "period_str": "3rd Quarter", "time": "4:32",
                "home_team": "Boston Celtics", "home_abbr": "BOS", "home_score": hs,
                "away_team": "Miami Heat", "away_abbr": "MIA", "away_score": as_,
                "leading": "Boston Celtics" if hs > as_ else "Miami Heat" if as_ > hs else "Tied",
                "margin": abs(hs - as_),
            }
        }

    elif sport == "nhl":
        hs, as_ = int(a.sine(1, 4, 8.0)), int(a.sine(0, 3, 9.0))
        return {
            "game_status": {
                "active": True, "period_str": "2nd Period", "clock": "11:24", "in_intermission": False,
                "home_team": "Toronto Maple Leafs", "home_abbr": "TOR", "home_score": hs,
                "away_team": "Montreal Canadiens", "away_abbr": "MTL", "away_score": as_,
                "leading": "Toronto Maple Leafs" if hs > as_ else "Montreal Canadiens" if as_ > hs else "Tied",
                "margin": abs(hs - as_),
            },
            "recent_goals": [
                {"team": "Toronto", "period_str": "1st Period", "time": "8:23"},
                {"team": "Montreal", "period_str": "2nd Period", "time": "3:41"},
            ],
        }

    elif sport == "nfl":
        hs, as_ = int(a.sine(7, 28, 10.0)), int(a.sine(3, 24, 9.0))
        return {
            "game_status": {
                "active": True, "period_str": "3rd Quarter", "clock": "8:42",
                "home_team": "Kansas City Chiefs", "home_abbr": "KC", "home_score": hs,
                "away_team": "Buffalo Bills", "away_abbr": "BUF", "away_score": as_,
                "leading": "Kansas City Chiefs" if hs > as_ else "Buffalo Bills" if as_ > hs else "Tied",
                "margin": abs(hs - as_),
                "down_distance": "2nd & 7", "possession": "KC", "yard_line": 34,
            },
            "recent_drives": [
                {"team": "KC", "plays": 8, "yards": 67, "result": "Touchdown"},
                {"team": "BUF", "plays": 5, "yards": 22, "result": "Punt"},
                {"team": "KC", "plays": 11, "yards": 52, "result": "Field Goal"},
            ],
        }

    elif sport == "mlb":
        hs, as_ = int(a.sine(1, 5, 8.0)), int(a.sine(0, 4, 9.0))
        return {
            "game_status": {
                "active": True, "inning": 6, "inning_half": "bottom", "inning_str": "Bottom 6",
                "balls": int(a.sine(0, 3, 1.5)), "strikes": int(a.sine(0, 2, 1.2)), "outs": int(a.sine(0, 2, 2.0)),
                "count": f"{int(a.sine(0,3,1.5))}-{int(a.sine(0,2,1.2))}",
                "home_team": "Toronto Blue Jays", "home_abbr": "TOR", "home_score": hs, "home_hits": int(a.sine(4, 9, 11.0)),
                "away_team": "New York Yankees", "away_abbr": "NYY", "away_score": as_, "away_hits": int(a.sine(2, 7, 13.0)),
                "leading": "Toronto Blue Jays" if hs > as_ else "New York Yankees" if as_ > hs else "Tied",
                "margin": abs(hs - as_),
                "batter": "Vladimir Guerrero Jr.", "pitcher": "Gerrit Cole",
                "bases": [a.pulse(12.0) > 0.5, a.pulse(8.0) > 0.6, False],
            },
        }

    return {}


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as M:SS.mmm"""
    if not ms or ms <= 0:
        return "--:--.---"
    m = int(ms / 60000)
    s = (ms % 60000) / 1000
    return f"{m}:{s:06.3f}"


def get_demo_game_session() -> dict:
    """Spoofed active Game Companion session -- mid-Elden-Ring."""
    return {
        "active": True,
        "game_name": "Elden Ring",
        "genre": "action_rpg",
        "platform": "PC",
        "character_info": "Faith/Strength build, Level 68",
        "current_area": "Altus Plateau",
        "stuck_on": "Godfrey, First Elden Lord",
        "spoiler_level": "minimal",
        "progress_notes": [
            "Defeated Godrick the Grafted",
            "Defeated Rennala, Queen of the Full Moon",
            "Completed Volcano Manor questline",
            "Found the Altus Plateau via Grand Lift of Dectus",
        ],
        "tried": [
            "Summon Nepheli Loux",
            "Flask of Wondrous Physick with Stonebarb Cracked Tear",
        ],
    }


def get_demo_game_history() -> dict:
    """Spoofed recent Game Companion sessions."""
    return {"history": [
        {"game_name": "Elden Ring", "genre": "action_rpg"},
        {"game_name": "Baldur's Gate 3", "genre": "rpg"},
        {"game_name": "Valheim", "genre": "survival"},
    ]}
