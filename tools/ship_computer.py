"""
IMQ2 Ship Computer Tools
Q2's Elite Dangerous COVAS (Computer Onboard Voice Assist System) mode —
real-time ship/session status from integrations/ed_telemetry.py, plus
INARA/EDSM galaxy lookups and paste-interpretation for the ED companion app.
"""
import logging
import re

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# get_ed_status
# ---------------------------------------------------------------------------

def get_ed_status(fields: str = "summary") -> str:
    """
    fields: 'summary' | 'location' | 'ship' | 'fuel' | 'cargo' | 'status' |
            'events' | 'session' | 'all'
    """
    try:
        from integrations.ed_telemetry import get_snapshot, is_active
        if not is_active():
            return "[get_ed_status] No ED telemetry — is windows/ed_bridge.py running and pointed at this machine?"

        s = get_snapshot()
        loc, fuel, status, stats = s["location"], s["fuel"], s["status"], s["session_stats"]

        def where() -> str:
            if loc["docked"] and loc["station"]:
                return f"docked at {loc['station']}"
            if loc["landed"] and loc["body"]:
                return f"landed on {loc['body']}"
            if loc["supercruise"]:
                return "in supercruise"
            if loc["body"]:
                return f"near {loc['body']}"
            return "in open space"

        def fuel_pct_str() -> str:
            if fuel.get("main") is not None and fuel.get("capacity"):
                return f" ({fuel['main'] / fuel['capacity'] * 100:.0f}%)"
            return ""

        if fields == "summary":
            shields = "up" if status.get("shields_up") else "down" if status.get("shields_up") is False else "unknown"
            return (
                f"{s.get('commander') or 'CMDR'} in {loc.get('system') or 'unknown system'}. "
                f"{s.get('ship') or 'Ship'} {where()}. "
                f"Fuel {fuel.get('main', '?')}T{fuel_pct_str()}. Shields {shields}. "
                f"{status.get('legal_state') or 'Legal status unknown'}. "
                f"{stats['jumps']} jumps, {stats['scans']} scans this session."
            )

        if fields == "location":
            return f"Currently in {loc.get('system') or 'unknown system'}. {where().capitalize()}."

        if fields == "ship":
            base = f"{s.get('ship') or 'Unknown ship'} ({s.get('ship_id') or '?'}), {s.get('commander') or 'CMDR unknown'}."
            if s.get("credits") is not None:
                base += f" Credits: {s['credits']:,}."
            return base

        if fields == "fuel":
            cap_str = f" of {fuel['capacity']}T" if fuel.get("capacity") else ""
            warn = " Fuel reserves low." if fuel.get("low") else ""
            return f"Main tank {fuel.get('main', '?')}T{cap_str}{fuel_pct_str()}. Reservoir {fuel.get('reservoir', '?')}T.{warn}"

        if fields == "cargo":
            return f"Cargo hold: {status.get('cargo', 0) or 0}T."

        if fields == "status":
            pips = status.get("pips")
            pips_str = f"{pips[0]}/{pips[1]}/{pips[2]}" if pips else "unknown"
            extras = ""
            if status.get("silent_running"):
                extras += " Silent running."
            if status.get("overheating"):
                extras += " Overheating."
            if status.get("in_danger"):
                extras += " In danger."
            if status.get("being_interdicted"):
                extras += " Being interdicted."
            return (
                f"Shields {'up' if status.get('shields_up') else 'down'}. "
                f"Hardpoints {'deployed' if status.get('hardpoints') else 'retracted'}. "
                f"Pips (SYS/ENG/WEP): {pips_str}. "
                f"Legal: {status.get('legal_state') or 'unknown'}. "
                f"Cargo: {status.get('cargo', 0) or 0}T.{extras}"
            )

        if fields == "events":
            events = s.get("recent_events", [])[:10]
            if not events:
                return "No recent events."
            return "\n".join(f"[{e['time']}] {e['text']}" for e in events)

        if fields == "session":
            return (
                f"Session: {stats['jumps']} jumps, {stats['scans']} scans, "
                f"{stats['bounties_collected']} bounties collected, "
                f"{stats['credits_earned']:,} Cr earned, {stats['deaths']} deaths."
            )

        if fields == "all":
            parts = [get_ed_status(f) for f in ("summary", "location", "fuel", "status", "session", "events")]
            return "\n\n".join(parts)

        return f"[get_ed_status] Unknown fields value: {fields}"
    except Exception as e:
        log.error(f"get_ed_status error: {e}", exc_info=True)
        return f"[get_ed_status] Error: {e}"


# ---------------------------------------------------------------------------
# search_galaxy — natural-language router to INARA/EDSM
# ---------------------------------------------------------------------------

def _format_dict(data: dict, max_items: int = 15) -> str:
    """
    Generic 'key: value' flattening for API payloads whose exact schema
    isn't worth hardcoding — same philosophy as FindProductTool in
    tools/registry.py: hand back real data and let the LLM turn it into
    natural language rather than guessing at brittle field-name parsing.
    """
    if not isinstance(data, dict):
        return str(data)[:1000]
    lines = []
    for k, v in list(data.items())[:max_items]:
        if isinstance(v, (dict, list)):
            v = str(v)[:200]
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def _format_edsm_system(result: dict) -> str:
    if not result.get("ok"):
        return f"[search_galaxy] {result.get('error')}"
    d = result["data"]
    info = d.get("information", {}) or {}
    coord = d.get("coord", {}) or {}
    parts = [f"System: {d.get('name')}"]
    if coord:
        parts.append(f"Coordinates: {coord.get('x')}, {coord.get('y')}, {coord.get('z')}")
    if d.get("requirePermit"):
        parts.append(f"Permit required: {d.get('permitName', 'yes')}")
    for label, key in (("Allegiance", "allegiance"), ("Government", "government"),
                        ("Faction", "faction"), ("Faction state", "factionState"),
                        ("Population", "population"), ("Security", "security"),
                        ("Economy", "economy"), ("Second economy", "secondEconomy")):
        if info.get(key) is not None:
            parts.append(f"{label}: {info[key]}")
    return "\n".join(parts)


def _search_nearest_economy(economy: str) -> str:
    try:
        from integrations import ed_edsm, ed_telemetry
        state = ed_telemetry.get_snapshot()
        system = state["location"].get("system")
        if not system:
            return "[search_galaxy] Current system unknown — can't compute nearest systems."
        here = ed_edsm.get_system(system)
        if not here.get("ok"):
            return f"[search_galaxy] Could not resolve coordinates for current system '{system}'."
        coord = here["data"].get("coord", {})
        if not coord:
            return f"[search_galaxy] No coordinate data for '{system}'."
        nearby = ed_edsm.get_nearest_systems(coord["x"], coord["y"], coord["z"], radius=50)
        if not nearby.get("ok"):
            return "[search_galaxy] Nearest-systems lookup failed."
        systems = nearby["systems"]
        if economy:
            economy_l = economy.lower()
            systems = [
                sy for sy in systems
                if economy_l in str(sy.get("information", {}).get("economy", "")).lower()
            ] or systems
        systems = sorted(systems, key=lambda sy: sy.get("distance", 9e9))[:5]
        if not systems:
            return f"[search_galaxy] No nearby systems found within 50ly of {system}."
        lines = [f"{sy['name']} — {sy.get('distance', '?')}ly, economy: {sy.get('information', {}).get('economy', 'unknown')}" for sy in systems]
        return "Nearest systems:\n" + "\n".join(lines)
    except Exception as e:
        return f"[search_galaxy] Error: {e}"


def search_galaxy(query: str) -> str:
    """Natural-language galaxy search — routes to INARA or EDSM based on the query shape."""
    try:
        q = query.strip()
        ql = q.lower().rstrip("?")
        from integrations import ed_inara, ed_edsm

        # "Where can I sell/buy void opals" / "price of gold"
        m = re.search(r"(?:sell|buy|price of)\s+(.+)$", ql)
        if m and any(k in ql for k in ("sell", "buy", "price")):
            commodity = m.group(1).strip()
            result = ed_inara.search_commodity(commodity)
            if not result.get("ok"):
                return f"[search_galaxy] {result.get('error')}"
            return f"Commodity data for {commodity}:\n" + _format_dict(result["data"])

        # "How do I get to <engineer>" / "route to engineer X"
        if "engineer" in ql or re.search(r"(?:get to|reach)\s+", ql) and "engineer" not in ql:
            m = re.search(r"(?:engineer|get to|reach)\s+(.+)$", ql)
            name = m.group(1).strip() if m else q
            result = ed_inara.search_engineer(name)
            if not result.get("ok"):
                return f"[search_galaxy] {result.get('error')}"
            return f"Engineer {result['engineer']}:\n{result['summary']}"

        # "What materials do I need for <blueprint/material>"
        if "material" in ql:
            m = re.search(r"(?:for|need)\s+(.+)$", ql)
            name = m.group(1).strip() if m else q
            result = ed_inara.search_material(name)
            if not result.get("ok"):
                return f"[search_galaxy] {result.get('error')}"
            return f"Material {result['material']}:\n{result['summary']}"

        # "What ships does <station> sell"
        if "ship" in ql and ("sell" in ql or "shipyard" in ql):
            m = re.search(r"does\s+(.+?)\s+sell", ql) or re.search(r"at\s+(.+)$", ql)
            station = m.group(1).strip() if m else q
            result = ed_inara.search_station(station)
            if not result.get("ok"):
                return f"[search_galaxy] {result.get('error')}"
            return f"Station {result['station']}:\n" + _format_dict(result["data"])

        # "What's the nearest <economy> system"
        m = re.search(r"nearest\s+(\w+)?\s*(?:economy\s+)?system", ql)
        if "nearest" in ql:
            economy = m.group(1) if m and m.group(1) not in (None, "economy", "the") else ""
            return _search_nearest_economy(economy)

        # Explicit "tell me about <station>" / station lookups try INARA first
        name = re.sub(r"^(tell me about|what'?s in|what is in|info on|what'?s)\s+", "", ql).strip()
        name = name or q

        station_result = ed_inara.search_station(name)
        if station_result.get("ok"):
            return f"Station {station_result['station']}:\n" + _format_dict(station_result["data"])

        # Fall back to EDSM system data
        system_result = ed_edsm.get_system(name)
        if system_result.get("ok"):
            return _format_edsm_system(system_result)

        return f"[search_galaxy] No data found for '{q}' on INARA or EDSM."
    except Exception as e:
        log.error(f"search_galaxy error: {e}", exc_info=True)
        return f"[search_galaxy] Error: {e}"


# ---------------------------------------------------------------------------
# process_paste
# ---------------------------------------------------------------------------

def _summarise_market_listing(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "Market listing noted:\n" + "\n".join(lines[:12])


def _summarise_mission_text(text: str) -> str:
    reward_m = re.search(r"([\d,]+)\s*(?:cr|credits)", text, re.I)
    reward = reward_m.group(1) if reward_m else "unknown"
    dest_m = re.search(r"(?:to|destination:?)\s+([A-Z][\w\-' ]+)", text)
    dest = dest_m.group(1).strip() if dest_m else "unspecified destination"
    return f"Mission noted — destination {dest}, reward {reward} Cr."


def process_paste(text: str) -> str:
    """
    Interpret text copy-pasted from the Elite Dangerous game UI and return
    Q2's response — a system/station lookup, a parsed mission/market
    summary, a location note, or (for anything unrecognised) the raw text
    handed back for the LLM to interpret directly in this same turn.
    """
    try:
        t = text.strip()
        if not t:
            return "[process_ed_paste] Empty paste."

        # Surface coordinates, e.g. "12.3456, -67.8901" or "Lat: 12.3 Lon: -56.7"
        coord_m = re.search(r"(-?\d{1,3}\.\d+)\s*[,;]?\s*(-?\d{1,3}\.\d+)", t)
        if coord_m and len(t) < 100:
            lat, lon = coord_m.groups()
            return f"Noted surface coordinates: latitude {lat}, longitude {lon}."

        lower = t.lower()
        multiline = len(t.splitlines()) > 1

        # Market listing — commodity figures with credit values, usually tabular/multi-line
        if re.search(r"\d[\d,]*\s*(?:cr|credits)\b", lower) and (multiline or "market" in lower):
            return _summarise_market_listing(t)

        # Mission text
        if any(k in lower for k in ("reward", "deliver", "destination:", "mission")):
            return _summarise_mission_text(t)

        # NPC / comms message — short, addressed lines
        if multiline is False and len(t) < 300 and (":" in t or any(k in lower for k in ("commander", "cmdr"))):
            return f'Comms received: "{t}" — noted.'

        # Short single line with no obvious punctuation -> treat as a system/station name
        if not multiline and len(t) <= 60:
            return search_galaxy(t)

        # Unknown shape — hand the raw text back so Q2's own reasoning covers it this turn.
        return f"[process_ed_paste] Unrecognized paste, interpret directly: {t}"
    except Exception as e:
        log.error(f"process_paste error: {e}", exc_info=True)
        return f"[process_ed_paste] Error: {e}"


# ---------------------------------------------------------------------------
# ed_alert
# ---------------------------------------------------------------------------

def ed_alert() -> str:
    """Proactive alert check — returns '' if nothing needs reporting."""
    try:
        from integrations.ed_telemetry import get_snapshot, is_active
        if not is_active():
            return ""

        s = get_snapshot()
        fuel, status, scan = s["fuel"], s["status"], s.get("current_scan")
        alerts = []

        low_by_pct = fuel.get("main") is not None and fuel.get("capacity") and (fuel["main"] / fuel["capacity"]) < 0.25
        if fuel.get("low") or low_by_pct:
            main = fuel.get("main")
            alerts.append(f"Fuel reserves low.{f' {main:.1f} tonnes remaining.' if main is not None else ''}")

        if status.get("overheating"):
            alerts.append("Hull temperature critical.")

        if status.get("being_interdicted"):
            alerts.append("Interdiction detected. Evasive action recommended.")

        if status.get("in_danger"):
            alerts.append("Danger proximity detected.")

        if status.get("shields_up") is False:
            alerts.append("Shields offline.")

        if scan and scan.get("valuable") in ("ELW", "WW"):
            alerts.append(f"Terraformable world detected — {scan['body']}.")

        return " ".join(alerts)
    except Exception as e:
        log.debug(f"ed_alert error: {e}")
        return ""


# ---------------------------------------------------------------------------
# get_target_info
# ---------------------------------------------------------------------------

def get_target_info() -> str:
    try:
        from integrations.ed_telemetry import get_snapshot
        target = get_snapshot().get("target")
        if not target:
            return "No target currently locked."

        parts = [f"Target: {target.get('ship') or 'unknown vessel'}"]
        if target.get("pilot_rank"):
            parts[-1] += f" ({target['pilot_rank']})"
        if target.get("bounty"):
            parts.append(f"Bounty: {target['bounty']:,} Cr")
        if target.get("faction"):
            parts.append(f"Faction: {target['faction']}")
        if target.get("legal_status"):
            parts.append(f"Legal: {target['legal_status']}")
        return ". ".join(parts) + "."
    except Exception as e:
        return f"[get_target_info] Error: {e}"
