"""
IMQ2 Race Engineer Tools
Provides Q2 with real-time Forza telemetry access and race engineering
snapshot summaries.
"""
import logging
log = logging.getLogger(__name__)


def get_race_telemetry(fields: str = "summary") -> str:
    """
    Get current live telemetry. Auto-detects whether Forza or Assetto
    Corsa has an active listener (see tools/telemetry_source.py) and
    returns whichever is live; if both are live, the most recently
    updated source wins.
    fields: 'summary' | 'tyres' | 'engine' | 'dynamics' | 'all'
    """
    try:
        from tools.telemetry_source import active_source
        source = active_source()
        if source == "ac":
            from tools.race_engineer_ac import get_ac_telemetry
            return get_ac_telemetry(fields=fields)
        if source is None:
            return "[race_engineer] No telemetry — is Forza running with Data Out enabled to 192.168.1.100:8000, or Assetto Corsa with ac_bridge.py sending to your-pi:8001?"

        from integrations.forza_telemetry import get_snapshot, CAR_CLASSES, DRIVETRAIN
        d = get_snapshot()
        if not d:
            return "[race_engineer] No data yet — drive for a moment."

        speed_kmh   = d["speed"] * 3.6
        speed_mph   = d["speed"] * 2.237
        fuel_pct    = d["fuel"] * 100
        power_hp    = d["power"] / 745.7
        torque_nm   = d["torque"]
        boost_psi   = d["boost"]
        gear        = d["gear"]
        rpm         = d["current_engine_rpm"]
        max_rpm     = d["engine_max_rpm"]
        car_class   = CAR_CLASSES.get(d["car_class"], "?")
        drivetrain  = DRIVETRAIN.get(d["drivetrain_type"], "?")
        lap         = d["lap_number"]
        pos         = d["race_position"]
        best_lap    = d["best_lap"]
        last_lap    = d["last_lap"]
        current_lap = d["current_lap"]
        race_time   = d["current_race_time"]

        tt = (d["tire_temp_fl"], d["tire_temp_fr"], d["tire_temp_rl"], d["tire_temp_rr"])
        ts = (d["tire_slip_ratio_fl"], d["tire_slip_ratio_fr"],
              d["tire_slip_ratio_rl"], d["tire_slip_ratio_rr"])

        def fmt_lap(s):
            if s <= 0: return "--:--.---"
            m = int(s // 60)
            return f"{m}:{s % 60:06.3f}"

        if fields in ("summary", "all"):
            lines = [
                f"Speed: {speed_kmh:.0f} km/h ({speed_mph:.0f} mph)  Gear: {gear}  RPM: {rpm:.0f}/{max_rpm:.0f}",
                f"Fuel: {fuel_pct:.1f}%  Boost: {boost_psi:.1f} psi",
                f"Lap: {lap}  Pos: {pos}  Race time: {fmt_lap(race_time)}",
                f"Best: {fmt_lap(best_lap)}  Last: {fmt_lap(last_lap)}  Current: {fmt_lap(current_lap)}",
                f"Tyres (C): FL {tt[0]:.0f}  FR {tt[1]:.0f}  RL {tt[2]:.0f}  RR {tt[3]:.0f}",
                f"Slip: FL {ts[0]:.2f}  FR {ts[1]:.2f}  RL {ts[2]:.2f}  RR {ts[3]:.2f}",
                f"Car: {car_class} class  {drivetrain}  PI {d['car_pi']}  {power_hp:.0f}hp  {torque_nm:.0f}Nm",
            ]
            return "\n".join(lines)

        elif fields == "tyres":
            susp = (d["susp_norm_fl"], d["susp_norm_fr"], d["susp_norm_rl"], d["susp_norm_rr"])
            return (
                f"Tyre temps (C): FL {tt[0]:.0f}  FR {tt[1]:.0f}  RL {tt[2]:.0f}  RR {tt[3]:.0f}\n"
                f"Slip ratio:     FL {ts[0]:.2f}  FR {ts[1]:.2f}  RL {ts[2]:.2f}  RR {ts[3]:.2f}\n"
                f"Suspension:     FL {susp[0]:.2f}  FR {susp[1]:.2f}  RL {susp[2]:.2f}  RR {susp[3]:.2f}"
            )

        elif fields == "engine":
            return (
                f"RPM: {rpm:.0f} / {max_rpm:.0f}  Gear: {gear}\n"
                f"Power: {power_hp:.0f} hp  Torque: {torque_nm:.0f} Nm\n"
                f"Boost: {boost_psi:.1f} psi  Fuel: {fuel_pct:.1f}%"
            )

        elif fields == "dynamics":
            ax, ay, az = d["accel_x"], d["accel_y"], d["accel_z"]
            return (
                f"Speed: {speed_kmh:.0f} km/h  Gear: {gear}\n"
                f"Accel G: X {ax/9.81:.2f}  Y {ay/9.81:.2f}  Z {az/9.81:.2f}\n"
                f"Yaw: {d['yaw']:.3f}  Pitch: {d['pitch']:.3f}  Roll: {d['roll']:.3f}"
            )

        return f"Unknown fields value: {fields}"

    except Exception as e:
        log.error(f"get_race_telemetry error: {e}", exc_info=True)
        return f"[race_engineer] Error: {e}"


def race_engineer_status() -> str:
    """
    Concise race engineer brief — what Q2 would proactively call out.
    Designed for spoken delivery: short, direct, actionable.
    """
    try:
        from tools.telemetry_source import active_source
        source = active_source()
        if source == "ac":
            from tools.race_engineer_ac import ac_race_engineer_status
            return ac_race_engineer_status()
        if source is None:
            return "[race_engineer] Telemetry offline."

        from integrations.forza_telemetry import get_snapshot
        d = get_snapshot()
        if not d:
            return "[race_engineer] No data."

        alerts = []

        # Fuel
        fuel_pct = d["fuel"] * 100
        if fuel_pct < 10:
            alerts.append(f"FUEL CRITICAL: {fuel_pct:.0f}% remaining.")
        elif fuel_pct < 25:
            alerts.append(f"Fuel low: {fuel_pct:.0f}%.")

        # Tyre temps
        tt = [d["tire_temp_fl"], d["tire_temp_fr"], d["tire_temp_rl"], d["tire_temp_rr"]]
        corners = ["FL", "FR", "RL", "RR"]
        cold = [(corners[i], tt[i]) for i in range(4) if tt[i] < 60]
        hot  = [(corners[i], tt[i]) for i in range(4) if tt[i] > 110]
        if cold:
            names = ", ".join(f"{c} ({t:.0f}C)" for c, t in cold)
            alerts.append(f"Tyres cold: {names}.")
        if hot:
            names = ", ".join(f"{c} ({t:.0f}C)" for c, t in hot)
            alerts.append(f"Tyres overheating: {names}.")

        # High slip
        ts = [d["tire_slip_ratio_fl"], d["tire_slip_ratio_fr"],
              d["tire_slip_ratio_rl"], d["tire_slip_ratio_rr"]]
        max_slip = max(abs(s) for s in ts)
        if max_slip > 0.8:
            alerts.append(f"High tyre slip: {max_slip:.2f} - losing grip.")

        # Lap summary
        def fmt_lap(s):
            if s <= 0: return "--"
            m = int(s // 60)
            return f"{m}:{s % 60:06.3f}"

        speed_kmh = d["speed"] * 3.6
        lap = d["lap_number"]
        pos = d["race_position"]
        current_lap = d["current_lap"]
        best_lap    = d["best_lap"]

        summary = f"Lap {lap}, P{pos}. Speed {speed_kmh:.0f} km/h. Current: {fmt_lap(current_lap)}"
        if best_lap > 0:
            delta = current_lap - best_lap if current_lap > 0 else 0
            if delta > 0:
                summary += f" (+{delta:.3f} vs best)."
            else:
                summary += f". Best: {fmt_lap(best_lap)}."

        if alerts:
            return summary + " ALERTS: " + " ".join(alerts)
        return summary + " All nominal."

    except Exception as e:
        return f"[race_engineer] Error: {e}"
