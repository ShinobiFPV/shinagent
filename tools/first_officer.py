"""
IMQ2 First Officer Tools — Microsoft Flight Simulator
Provides Q2 with real-time MSFS telemetry access (via windows/msfs_bridge.py
-> integrations/msfs_telemetry.py) and first-officer-style status callouts.
Mirrors tools/race_engineer_ac.py's shape; kept separate since flight data
(autopilot, nav, fuel endurance, checklists) doesn't overlap with sim racing.
"""
import logging

log = logging.getLogger(__name__)

_NO_TELEMETRY = (
    "[first_officer] No telemetry — is MSFS running with windows/msfs_bridge.py "
    "active on the Windows box, sending to your-pi:8002?"
)


def _bool_word(v) -> str:
    return "ON" if v else "OFF"


def get_flight_status(fields: str = "summary") -> str:
    """
    Get current live MSFS telemetry.
    fields: 'summary' | 'position' | 'engines' | 'autopilot' | 'fuel' |
            'systems' | 'weather' | 'nav' | 'all'
    """
    try:
        from integrations.msfs_telemetry import get_snapshot, is_active, fmt_altitude, fmt_speed, fmt_heading

        if not is_active():
            return _NO_TELEMETRY
        d = get_snapshot()
        if not d:
            return "[first_officer] No data yet — give it a moment."

        title = d.get("aircraft_title", "Unknown")
        phase = d.get("flight_phase", "?")

        def summary() -> str:
            ap_bits = []
            if d.get("ap_master"):
                if d.get("ap_alt_lock"):
                    ap_bits.append("ALT")
                if d.get("ap_hdg_lock"):
                    ap_bits.append("HDG")
                if d.get("ap_ias_hold"):
                    ap_bits.append("IAS")
                if d.get("ap_vs_hold"):
                    ap_bits.append("VS")
                if d.get("ap_nav1_lock"):
                    ap_bits.append("NAV")
                if d.get("ap_approach_hold"):
                    ap_bits.append("APR")
            ap_str = "+".join(ap_bits) if ap_bits else ("ON" if d.get("ap_master") else "OFF")

            fuel_gal = d.get("fuel_qty_gal", 0.0)
            flow = d.get("eng1_fuel_flow_gph", 0.0) + d.get("eng2_fuel_flow_gph", 0.0)
            fuel_hrs = (fuel_gal / flow) if flow > 0.1 else 0.0

            return (
                f"{title}  Phase: {phase}  Alt {fmt_altitude(d.get('altitude_ft', 0))}  "
                f"IAS {fmt_speed(d.get('airspeed_ind_kt', 0))}  HDG {fmt_heading(d.get('heading_deg', 0))}  "
                f"VS {d.get('vertical_speed_fpm', 0):+.0f}fpm\n"
                f"AP: {ap_str}  Fuel {fuel_gal:.1f}gal"
                + (f" ({fuel_hrs:.1f}hr)" if fuel_hrs else "")
                + f"  Wind {d.get('wind_dir_deg', 0):.0f}/{d.get('wind_speed_kt', 0):.0f}kts"
            )

        if fields in ("summary",):
            return summary()

        if fields == "position":
            return (
                f"Lat {d.get('latitude', 0):.5f}  Lon {d.get('longitude', 0):.5f}\n"
                f"Alt {fmt_altitude(d.get('altitude_ft', 0))}  AGL {fmt_altitude(d.get('alt_agl_ft', 0))}\n"
                f"HDG {fmt_heading(d.get('heading_deg', 0))}  Pitch {d.get('pitch_deg', 0):+.1f}  Bank {d.get('bank_deg', 0):+.1f}\n"
                f"IAS {fmt_speed(d.get('airspeed_ind_kt', 0))}  TAS {fmt_speed(d.get('airspeed_true_kt', 0))}  "
                f"Mach {d.get('mach', 0):.2f}  GS {fmt_speed(d.get('ground_speed_kt', 0))}\n"
                f"VS {d.get('vertical_speed_fpm', 0):+.0f}fpm  On ground: {_bool_word(d.get('on_ground'))}"
            )

        if fields == "engines":
            return (
                f"Eng1: {_bool_word(d.get('eng1_combustion'))}  RPM {d.get('eng1_rpm', 0):.0f}  "
                f"Oil {d.get('eng1_oil_temp_c', 0):.0f}C  N1 {d.get('eng1_n1_pct', 0):.0f}%  "
                f"N2 {d.get('eng1_n2_pct', 0):.0f}%  Flow {d.get('eng1_fuel_flow_gph', 0):.1f}gph\n"
                f"Eng2: {_bool_word(d.get('eng2_combustion'))}  N1 {d.get('eng2_n1_pct', 0):.0f}%  "
                f"Flow {d.get('eng2_fuel_flow_gph', 0):.1f}gph"
            )

        if fields == "autopilot":
            return (
                f"AP master: {_bool_word(d.get('ap_master'))}\n"
                f"ALT hold: {_bool_word(d.get('ap_alt_lock'))} @ {fmt_altitude(d.get('ap_alt_var_ft', 0))}\n"
                f"HDG hold: {_bool_word(d.get('ap_hdg_lock'))} @ {fmt_heading(d.get('ap_hdg_var_deg', 0))}\n"
                f"IAS hold: {_bool_word(d.get('ap_ias_hold'))} @ {fmt_speed(d.get('ap_ias_var_kt', 0))}\n"
                f"VS hold: {_bool_word(d.get('ap_vs_hold'))} @ {d.get('ap_vs_var_fpm', 0):+.0f}fpm\n"
                f"NAV1: {_bool_word(d.get('ap_nav1_lock'))}  Approach: {_bool_word(d.get('ap_approach_hold'))}  "
                f"Glideslope: {_bool_word(d.get('ap_glideslope_hold'))}"
            )

        if fields == "fuel":
            fuel_gal = d.get("fuel_qty_gal", 0.0)
            cap_gal = d.get("fuel_capacity_gal", 0.0)
            pct = (fuel_gal / cap_gal * 100) if cap_gal > 0 else 0.0
            flow = d.get("eng1_fuel_flow_gph", 0.0) + d.get("eng2_fuel_flow_gph", 0.0)
            hrs = (fuel_gal / flow) if flow > 0.1 else 0.0
            return (
                f"Fuel: {fuel_gal:.1f}gal / {cap_gal:.1f}gal ({pct:.0f}%)  "
                f"{d.get('fuel_qty_lbs', 0):.0f}lbs\n"
                f"Flow: {flow:.1f}gph"
                + (f"  Endurance: {hrs:.1f}hr" if hrs else "")
            )

        if fields == "systems":
            return (
                f"Gear: {'DOWN' if d.get('gear_down') else 'UP'}  "
                f"Flaps: idx {d.get('flaps_index', 0):.0f} ({d.get('flaps_pct', 0) * 100:.0f}%)  "
                f"Parking brake: {_bool_word(d.get('parking_brake'))}\n"
                f"Strobe: {_bool_word(d.get('strobe_light'))}  Landing light: {_bool_word(d.get('landing_light'))}\n"
                f"Xpdr: {d.get('transponder_code', 0):.0f}  COM1 {d.get('com1_freq', 0):.3f}  "
                f"NAV1 {d.get('nav1_freq', 0):.2f}  NAV2 {d.get('nav2_freq', 0):.2f}"
            )

        if fields == "weather":
            return (
                f"Wind {d.get('wind_dir_deg', 0):.0f} at {d.get('wind_speed_kt', 0):.0f}kts  "
                f"OAT {d.get('oat_c', 0):.0f}C  Visibility {d.get('visibility_m', 0):.0f}m"
            )

        if fields == "nav":
            return (
                f"NAV1 {d.get('nav1_freq', 0):.2f}  OBS {fmt_heading(d.get('nav1_obs', 0))}  "
                f"NAV2 {d.get('nav2_freq', 0):.2f}\n"
                f"GPS GS {fmt_speed(d.get('gps_ground_speed_kt', 0))}  "
                f"Next WP: {d.get('gps_wp_ident') or '--'} "
                f"{d.get('gps_wp_distance_nm', 0):.1f}nm @ {fmt_heading(d.get('gps_wp_bearing_deg', 0))}  "
                f"ETE {d.get('gps_ete_s', 0) / 60:.1f}min"
            )

        if fields == "all":
            sections = [summary()]
            for f in ("position", "engines", "autopilot", "fuel", "systems", "weather", "nav"):
                sections.append(get_flight_status(fields=f))
            return "\n\n".join(sections)

        return f"Unknown fields value: {fields}"

    except Exception as e:
        log.error(f"get_flight_status error: {e}", exc_info=True)
        return f"[first_officer] Error: {e}"


def first_officer_status(aircraft_type: str = None) -> str:
    """
    Concise spoken first-officer callout — the things a real FO would flag
    unprompted: altitude alerting, gear/flap checks, fuel state, bank angle,
    autopilot/engine faults, waypoint proximity, approach checklist, weather.

    Each category is gated by first_officer.alerts.<key> in config.yaml
    (Settings > First Officer > Proactive Callouts) so a disabled category
    never appears in the summary, matching tools/race_engineer_ac.py's
    alerts_cfg-gated pattern. aircraft_type defaults to
    first_officer.aircraft_type if not passed explicitly (the settings
    panel lets the user hint this manually while MSFS is offline; once
    connected the bridge's own aircraft_title is used elsewhere instead).
    """
    try:
        from config.loader import config
        from integrations.msfs_telemetry import get_snapshot, is_active
        if not is_active():
            return "[first_officer] Telemetry offline."
        d = get_snapshot()
        if not d:
            return "[first_officer] No data."

        if aircraft_type is None:
            aircraft_type = config.get("first_officer.aircraft_type", "auto")
        alerts_cfg = config.get("first_officer.alerts", {}) or {}
        min_alt_agl = config.get("first_officer.min_alt_agl", 50)

        phase = d.get("flight_phase", "?")
        agl = d.get("alt_agl_ft", 0.0)
        alerts = []

        # Altitude alerting — approaching AP altitude target
        if alerts_cfg.get("altitude", True) and d.get("ap_master") and d.get("ap_alt_lock"):
            target = d.get("ap_alt_var_ft", 0.0)
            current = d.get("altitude_ft", 0.0)
            remaining = abs(target - current)
            if 0 < remaining <= 1000:
                alerts.append(f"{remaining:.0f} to level off.")

        # Gear check on approach
        ias = d.get("airspeed_ind_kt", 0.0)
        if alerts_cfg.get("gear", True) and ias < 150 and agl < 3000 and not d.get("gear_down") and not d.get("on_ground"):
            alerts.append("Gear check.")

        # Fuel low
        if alerts_cfg.get("fuel", True):
            fuel_gal = d.get("fuel_qty_gal", 0.0)
            cap_gal = d.get("fuel_capacity_gal", 0.0)
            if cap_gal > 0 and fuel_gal / cap_gal < 0.25:
                flow = d.get("eng1_fuel_flow_gph", 0.0) + d.get("eng2_fuel_flow_gph", 0.0)
                hrs = (fuel_gal / flow) if flow > 0.1 else 0.0
                alerts.append(f"Fuel low -- {fuel_gal:.0f} gallons, {hrs:.1f} hours.")

        # Bank angle
        bank = abs(d.get("bank_deg", 0.0))
        if alerts_cfg.get("bank", True) and bank > 30:
            alerts.append("Bank angle.")

        # Autopilot disconnect (only worth calling out once airborne and configured to fly itself)
        if alerts_cfg.get("autopilot", True) and phase not in ("PARKED", "TAXI") and not d.get("ap_master"):
            alerts.append("Autopilot disconnected.")

        # Engine failure. Engine 2 is only checked once airborne with some
        # N1 reading on it — a single-engine aircraft's unused engine-2
        # fields otherwise sit at zero forever and would false-alarm.
        # Jets/turboprops report spool as N1%; piston GA reports RPM —
        # "auto" picks whichever reading is actually nonzero.
        if alerts_cfg.get("engine", True) and phase not in ("PARKED", "TAXI"):
            uses_n1 = aircraft_type in ("airliner", "turboprop") or (
                aircraft_type == "auto" and d.get("eng1_n1_pct", 0.0) > 0
            )
            if not d.get("eng1_combustion"):
                metric = f"N1 {d.get('eng1_n1_pct', 0.0):.0f}%" if uses_n1 else f"RPM {d.get('eng1_rpm', 0.0):.0f}"
                alerts.append(f"Engine 1 not running -- {metric}.")
            elif d.get("eng2_n1_pct", 0.0) > 0 and not d.get("eng2_combustion"):
                alerts.append("Engine 2 not running.")

        # Approaching waypoint
        wp_dist = d.get("gps_wp_distance_nm", 0.0)
        wp_name = d.get("gps_wp_ident")
        if alerts_cfg.get("waypoint", True) and wp_name and 0 < wp_dist < 2.0:
            alerts.append(f"Waypoint {wp_name} in {wp_dist:.1f}nm.")

        # Approach checklist reminder — suppressed below min_alt_agl so it
        # doesn't nag during the ground roll/taxi after landing.
        if alerts_cfg.get("approach", True) and phase == "APPROACH" and agl > min_alt_agl:
            gear_state = "down" if d.get("gear_down") else "up"
            flaps_pct = d.get("flaps_pct", 0.0) * 100
            wp = wp_name or "field"
            alerts.append(f"Approaching {wp}, gear {gear_state}, flaps {flaps_pct:.0f}%.")

        # Weather advisories. MSFS's wire format (windows/msfs_bridge.py)
        # only carries wind and visibility, not a turbulence simvar, so
        # that's what this covers for now.
        if alerts_cfg.get("weather", True):
            wind_kt = d.get("wind_speed_kt", 0.0)
            vis_m = d.get("visibility_m", 9999.0)
            if wind_kt > 25:
                alerts.append(f"Strong wind -- {wind_kt:.0f}kts from {d.get('wind_dir_deg', 0.0):.0f}.")
            elif 0 < vis_m < 4800:  # ~3 statute miles
                alerts.append(f"Visibility {vis_m / 1609:.1f} miles.")

        if alerts:
            return f"[{phase}] " + " ".join(alerts)
        return f"[{phase}] Nominal."

    except Exception as e:
        log.error(f"first_officer_status error: {e}", exc_info=True)
        return f"[first_officer] Error: {e}"


# ---------------------------------------------------------------------------
# Aircraft control — the write side, via windows/msfs_bridge.py's Flask
# control server (integrations/msfs_telemetry.py's MSFSController). Kept in
# this same module rather than a separate file since it's the natural
# counterpart to get_flight_status/first_officer_status above: same
# telemetry source, same aircraft, same profile. Deliberately never called
# from the proactive alert thread (main.py's RaceEngineerAlertThread) —
# control only happens on the pilot's explicit request, matching the
# purchasing tools' research-vs-execution split in spirit (read tools are
# always safe to call automatically; write tools are not).
# ---------------------------------------------------------------------------

def control_aircraft(command: str, value=None) -> str:
    """
    Main control dispatch — the LLM passes a structured command name (see
    tools/registry.py's ControlAircraftTool description for the full list)
    and an optional numeric value. No NLP happens here; Claude/GPT already
    resolved "climb to eight thousand" into ("set_autopilot_altitude", 8000)
    before this is called.
    """
    from integrations.msfs_telemetry import get_controller
    ctrl = get_controller()

    if not ctrl.is_reachable():
        return "[first_officer] MSFS bridge not reachable. Is msfs_bridge.py running?"

    result = ctrl.send_command(command, value)

    if result.get("ok"):
        return result.get("result", f"Command {command} executed.")
    return f"[first_officer] Command failed: {result.get('error', 'Unknown error')}"


def engage_autopilot_level_off(altitude_ft: int, heading_deg: int = None, airspeed_kts: int = None) -> str:
    """Compound command: set altitude (and optionally heading/airspeed), enable the matching holds, then turn the AP master on."""
    from integrations.msfs_telemetry import get_controller
    ctrl = get_controller()

    if not ctrl.is_reachable():
        return "[first_officer] MSFS bridge not reachable. Is msfs_bridge.py running?"

    results = [
        ctrl.send_command("set_autopilot_altitude", altitude_ft),
        ctrl.send_command("enable_altitude_hold"),
    ]
    if heading_deg is not None:
        results.append(ctrl.send_command("set_autopilot_heading", heading_deg))
        results.append(ctrl.send_command("enable_heading_hold"))
    if airspeed_kts is not None:
        results.append(ctrl.send_command("set_autopilot_airspeed", airspeed_kts))
    results.append(ctrl.send_command("autopilot_on"))

    if all(r.get("ok") for r in results):
        parts = [f"levelling at {altitude_ft:,} feet"]
        if heading_deg is not None:
            parts.append(f"heading {heading_deg:03d}")
        if airspeed_kts is not None:
            parts.append(f"{airspeed_kts}kts")
        return "Autopilot engaged, " + ", ".join(parts) + "."
    return "Autopilot engagement partially failed — check cockpit."


def execute_approach_checklist() -> str:
    """Run through the approach checklist: gear down, flaps approach, landing lights, strobes."""
    from integrations.msfs_telemetry import get_controller
    ctrl = get_controller()

    if not ctrl.is_reachable():
        return "[first_officer] MSFS bridge not reachable. Is msfs_bridge.py running?"

    items = []

    r = ctrl.send_command("gear_down")
    items.append(f"Gear: {'DOWN' if r.get('ok') else 'FAILED'}")

    r = ctrl.send_command("set_flaps", 2)
    items.append(f"Flaps: {'APPROACH' if r.get('ok') else 'FAILED'}")

    r = ctrl.send_command("landing_lights_on")
    items.append(f"Landing lights: {'ON' if r.get('ok') else 'FAILED'}")

    r = ctrl.send_command("strobes_toggle")
    items.append(f"Strobes: {'ON' if r.get('ok') else 'FAILED'}")

    return "Approach checklist: " + ", ".join(items)


def set_emergency_transponder() -> str:
    """Set transponder to 7700 emergency squawk."""
    return control_aircraft("set_transponder", 7700)


def set_guard_frequency() -> str:
    """Tune COM1 to 121.5 MHz guard frequency."""
    return control_aircraft("set_com1", 121.5)
