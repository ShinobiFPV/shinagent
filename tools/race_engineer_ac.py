"""
IMQ2 Race Engineer Tools — Assetto Corsa
Provides Q2 with real-time AC telemetry access and race engineering
snapshot summaries. Mirrors tools/race_engineer.py's Forza tools;
kept as a separate module since AC exposes a different field set
(tyre pressure/wear, brake temp, damage) via windows/ac_bridge.py.
"""
import logging
log = logging.getLogger(__name__)


def _fmt_lap(ms):
    if ms <= 0:
        return "--:--.---"
    m = ms // 60000
    s = (ms % 60000) / 1000
    return f"{m}:{s:06.3f}"


def _gear_label(g):
    if g == 0:
        return "R"
    if g == 1:
        return "N"
    return str(g - 1)


def get_ac_telemetry(fields: str = "summary") -> str:
    """
    Get current live telemetry. Auto-detects whether Assetto Corsa or
    Forza has an active listener (see tools/telemetry_source.py) and
    returns whichever is live; if both are live, the most recently
    updated source wins.
    fields: 'summary' | 'tyres' | 'engine' | 'dynamics' | 'all'
    """
    try:
        from tools.telemetry_source import active_source
        source = active_source()
        if source == "forza":
            from tools.race_engineer import get_race_telemetry
            return get_race_telemetry(fields=fields)
        if source is None:
            return "[race_engineer_ac] No telemetry — is AC running with ac_bridge.py active on the Windows box, sending to your-pi:8001, or Forza with Data Out enabled to 192.168.1.100:8000?"

        from integrations.ac_telemetry import get_snapshot, AC_SESSION_TYPE
        d = get_snapshot()
        if not d:
            return "[race_engineer_ac] No data yet — drive for a moment."

        speed_kmh = d["speed_kmh"]
        speed_mph = speed_kmh * 0.621371
        gear = _gear_label(d["gear"])
        rpm = d["rpm"]
        max_rpm = d["max_rpm"]
        fuel = d["fuel"]
        session = AC_SESSION_TYPE.get(d["session_type"], "?")
        lap = d["completed_laps"]
        pos = d["position"]
        current_lap = d["current_lap_ms"]
        last_lap = d["last_lap_ms"]
        best_lap = d["best_lap_ms"]

        tt = (d["tyre_temp_fl"], d["tyre_temp_fr"], d["tyre_temp_rl"], d["tyre_temp_rr"])
        tp = (d["tyre_pressure_fl"], d["tyre_pressure_fr"], d["tyre_pressure_rl"], d["tyre_pressure_rr"])
        tw = (d["tyre_wear_fl"], d["tyre_wear_fr"], d["tyre_wear_rl"], d["tyre_wear_rr"])
        ts = (d["wheel_slip_fl"], d["wheel_slip_fr"], d["wheel_slip_rl"], d["wheel_slip_rr"])

        if fields in ("summary", "all"):
            lines = [
                f"Speed: {speed_kmh:.0f} km/h ({speed_mph:.0f} mph)  Gear: {gear}  RPM: {rpm:.0f}/{max_rpm:.0f}",
                f"Fuel: {fuel:.1f} L  Session: {session}",
                f"Lap: {lap}  Pos: {pos}  Current: {_fmt_lap(current_lap)}",
                f"Best: {_fmt_lap(best_lap)}  Last: {_fmt_lap(last_lap)}",
                f"Tyres (C): FL {tt[0]:.0f}  FR {tt[1]:.0f}  RL {tt[2]:.0f}  RR {tt[3]:.0f}",
                f"Pressure (psi): FL {tp[0]:.1f}  FR {tp[1]:.1f}  RL {tp[2]:.1f}  RR {tp[3]:.1f}",
                f"Wear: FL {tw[0]:.0f}%  FR {tw[1]:.0f}%  RL {tw[2]:.0f}%  RR {tw[3]:.0f}%",
            ]
            return "\n".join(lines)

        elif fields == "tyres":
            bt = (d["brake_temp_fl"], d["brake_temp_fr"], d["brake_temp_rl"], d["brake_temp_rr"])
            return (
                f"Tyre temps (C):   FL {tt[0]:.0f}  FR {tt[1]:.0f}  RL {tt[2]:.0f}  RR {tt[3]:.0f}\n"
                f"Pressure (psi):   FL {tp[0]:.1f}  FR {tp[1]:.1f}  RL {tp[2]:.1f}  RR {tp[3]:.1f}\n"
                f"Wear (%):         FL {tw[0]:.0f}  FR {tw[1]:.0f}  RL {tw[2]:.0f}  RR {tw[3]:.0f}\n"
                f"Slip:             FL {ts[0]:.2f}  FR {ts[1]:.2f}  RL {ts[2]:.2f}  RR {ts[3]:.2f}\n"
                f"Brake temp (C):   FL {bt[0]:.0f}  FR {bt[1]:.0f}  RL {bt[2]:.0f}  RR {bt[3]:.0f}"
            )

        elif fields == "engine":
            return (
                f"RPM: {rpm:.0f} / {max_rpm:.0f}  Gear: {gear}\n"
                f"Turbo: {d['turbo_boost']:.2f} bar  Fuel: {fuel:.1f} L\n"
                f"TC: {d['tc']:.2f}  ABS: {d['abs']:.2f}  Ballast: {d['ballast']:.0f} kg"
            )

        elif fields == "dynamics":
            ax, ay, az = d["accel_x"], d["accel_y"], d["accel_z"]
            return (
                f"Speed: {speed_kmh:.0f} km/h  Gear: {gear}\n"
                f"Accel G: X {ax/9.81:.2f}  Y {ay/9.81:.2f}  Z {az/9.81:.2f}\n"
                f"Grip: {d['surface_grip']:.2f}  Road temp: {d['road_temp']:.0f}C  Air temp: {d['air_temp']:.0f}C"
            )

        return f"Unknown fields value: {fields}"

    except Exception as e:
        log.error(f"get_ac_telemetry error: {e}", exc_info=True)
        return f"[race_engineer_ac] Error: {e}"


def ac_race_engineer_status() -> str:
    """
    Concise AC race engineer brief — what Q2 would proactively call out.
    Designed for spoken delivery: short, direct, actionable.
    """
    try:
        from tools.telemetry_source import active_source
        source = active_source()
        if source == "forza":
            from tools.race_engineer import race_engineer_status
            return race_engineer_status()
        if source is None:
            return "[race_engineer_ac] Telemetry offline."

        from integrations.ac_telemetry import get_snapshot, AC_FLAG
        d = get_snapshot()
        if not d:
            return "[race_engineer_ac] No data."

        alerts = []

        # Fuel — absolute litres remaining (AC's per-frame packet has no max-fuel field)
        fuel = d["fuel"]
        if fuel < 2:
            alerts.append(f"FUEL CRITICAL: {fuel:.1f}L remaining.")
        elif fuel < 5:
            alerts.append(f"Fuel low: {fuel:.1f}L.")

        # Tyre temps
        tt = [d["tyre_temp_fl"], d["tyre_temp_fr"], d["tyre_temp_rl"], d["tyre_temp_rr"]]
        corners = ["FL", "FR", "RL", "RR"]
        cold = [(corners[i], tt[i]) for i in range(4) if tt[i] < 60]
        hot = [(corners[i], tt[i]) for i in range(4) if tt[i] > 110]
        if cold:
            names = ", ".join(f"{c} ({t:.0f}C)" for c, t in cold)
            alerts.append(f"Tyres cold: {names}.")
        if hot:
            names = ", ".join(f"{c} ({t:.0f}C)" for c, t in hot)
            alerts.append(f"Tyres overheating: {names}.")

        # Tyre wear
        tw = [d["tyre_wear_fl"], d["tyre_wear_fr"], d["tyre_wear_rl"], d["tyre_wear_rr"]]
        worn = [(corners[i], tw[i]) for i in range(4) if tw[i] > 80]
        if worn:
            names = ", ".join(f"{c} ({w:.0f}%)" for c, w in worn)
            alerts.append(f"Tyres worn: {names}.")

        # High slip
        ts = [d["wheel_slip_fl"], d["wheel_slip_fr"], d["wheel_slip_rl"], d["wheel_slip_rr"]]
        max_slip = max(abs(s) for s in ts)
        if max_slip > 0.8:
            alerts.append(f"High tyre slip: {max_slip:.2f} - losing grip.")

        # Damage
        damage = [d["damage_front"], d["damage_rear"], d["damage_left"], d["damage_right"], d["damage_centre"]]
        if max(damage) > 0:
            alerts.append("Damage sustained.")

        # Flag
        flag = AC_FLAG.get(d["flag"], "?")
        if flag not in ("None", "?"):
            alerts.append(f"Flag: {flag}.")

        lap = d["completed_laps"]
        pos = d["position"]
        current_lap = d["current_lap_ms"]
        best_lap = d["best_lap_ms"]

        summary = f"Lap {lap}, P{pos}. Speed {d['speed_kmh']:.0f} km/h. Current: {_fmt_lap(current_lap)}"
        if best_lap > 0:
            delta_ms = current_lap - best_lap if current_lap > 0 else 0
            if delta_ms > 0:
                summary += f" (+{delta_ms/1000:.3f} vs best)."
            else:
                summary += f". Best: {_fmt_lap(best_lap)}."

        if alerts:
            return summary + " ALERTS: " + " ".join(alerts)
        return summary + " All nominal."

    except Exception as e:
        return f"[race_engineer_ac] Error: {e}"
