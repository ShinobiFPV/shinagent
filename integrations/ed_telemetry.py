"""
IMQ2 Elite Dangerous Telemetry Integration
UDP JSON listener on port 8003, fed by windows/ed_bridge.py running on the
Windows gaming PC. Parses forwarded Elite Dangerous Player Journal events and
status.json snapshots into a running game-state cache for Q2's ship computer
tools (tools/ship_computer.py).

Wire protocol (JSON UDP packets, one per line from the bridge):
  {"type": "journal", "event": {...raw ED journal event dict...}}
  {"type": "status", "flags": {...}, "pips": [...], "fuel_main": ..., ...}
  {"type": "paste", "text": "..."}
"""

import copy
import json
import logging
import socket
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# Scan.PlanetClass values worth flagging in the recent-events feed.
_VALUABLE_CLASSES = {
    "Earthlike body": "ELW",
    "Water world": "WW",
    "Ammonia world": "AW",
    "High metal content body": "HMC",
}

_MAX_RECENT_EVENTS = 20


def _now_hms() -> str:
    return time.strftime("%H:%M")


class EDTelemetryListener:
    """
    Background UDP listener. Call start() once; latest game state is always
    available via snapshot(). Thread-safe.
    """

    def __init__(self, port: int = 8003):
        self._port = port
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_packet_time = 0.0
        self._state = self._fresh_state()

    @staticmethod
    def _fresh_state() -> dict:
        return {
            "commander": None,
            "ship": None,
            "ship_id": None,
            "credits": None,
            "location": {
                "system": None,
                "station": None,
                "body": None,
                "docked": False,
                "landed": False,
                "supercruise": False,
            },
            "fuel": {
                "main": None,
                "reservoir": None,
                "capacity": None,
                "low": False,
            },
            "status": {
                "shields_up": None,
                "hardpoints": None,
                "silent_running": None,
                "cargo_scoop": None,
                "overheating": None,
                "in_danger": None,
                "being_interdicted": None,
                "legal_state": None,
                "pips": None,
                "cargo": None,
            },
            "current_scan": None,
            "target": None,
            "last_paste": None,
            "recent_events": [],
            "session_stats": {
                "jumps": 0,
                "scans": 0,
                "bounties_collected": 0,
                "credits_earned": 0,
                "deaths": 0,
            },
            "last_updated": None,
        }

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True, name="EDTelemetry")
        self._thread.start()
        log.info(f"ED telemetry listener started on UDP port {self._port}")

    def stop(self):
        self._running = False

    def is_active(self) -> bool:
        """True if a packet arrived recently — status.json is forwarded every 2s while ED is running."""
        return (time.time() - self._last_packet_time) < 10.0

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def _add_event(self, event_type: str, text: str):
        self._state["recent_events"].insert(0, {"time": _now_hms(), "type": event_type, "text": text})
        del self._state["recent_events"][_MAX_RECENT_EVENTS:]

    def _listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind(("0.0.0.0", self._port))
            log.info(f"ED UDP bound to 0.0.0.0:{self._port}")
        except Exception as e:
            log.error(f"ED UDP bind failed: {e}")
            return

        while self._running:
            try:
                data, _ = sock.recvfrom(65536)
                packet = json.loads(data.decode("utf-8"))
                with self._lock:
                    self._dispatch(packet)
                # Written outside the lock, unlike _dispatch()'s state above —
                # harmless under CPython's GIL for a single float assignment,
                # but inconsistent with this class's own locking discipline.
                self._last_packet_time = time.time()
            except socket.timeout:
                continue
            except Exception as e:
                log.debug(f"ED UDP error: {e}")

        sock.close()

    def _dispatch(self, packet: dict):
        self._state["last_updated"] = time.time()
        ptype = packet.get("type")
        if ptype == "journal":
            self._handle_journal(packet.get("event", {}) or {})
        elif ptype == "status":
            self._handle_status(packet)
        elif ptype == "paste":
            self._handle_paste(packet.get("text", ""))

    # -- journal events ----------------------------------------------------

    def _handle_journal(self, ev: dict):
        name = ev.get("event")
        if not name:
            return
        handler = getattr(self, f"_ev_{name.lower()}", None)
        if handler:
            try:
                handler(ev)
            except Exception as e:
                log.debug(f"ED journal handler for {name} failed: {e}")
        else:
            # No bespoke formatting for this one yet — still surface it.
            self._add_event(name, name)

    def _ev_loadgame(self, ev):
        self._state["commander"] = f"CMDR {ev.get('Commander', 'Unknown')}"
        self._state["ship"] = ev.get("Ship_Localised") or ev.get("Ship")
        self._state["ship_id"] = ev.get("ShipIdent") or ev.get("ShipName")
        self._state["credits"] = ev.get("Credits")
        self._add_event("LoadGame", f"Game loaded — {self._state['commander']} in {self._state['ship']}")

    def _ev_location(self, ev):
        loc = self._state["location"]
        loc["system"] = ev.get("StarSystem")
        loc["station"] = ev.get("StationName")
        loc["body"] = ev.get("Body")
        loc["docked"] = bool(ev.get("Docked", False))
        loc["landed"] = bool(ev.get("Landed", False))
        loc["supercruise"] = False
        self._add_event("Location", f"Located in {loc['system']}")

    def _ev_fsdjump(self, ev):
        loc = self._state["location"]
        loc["system"] = ev.get("StarSystem")
        loc["station"] = None
        loc["body"] = None
        loc["docked"] = False
        loc["supercruise"] = True
        self._state["session_stats"]["jumps"] += 1
        self._state["current_scan"] = None
        self._add_event("FSDJump", f"Jumped to {loc['system']}")

    def _ev_supercruiseentry(self, ev):
        self._state["location"]["supercruise"] = True
        self._add_event("SupercruiseEntry", "Entered supercruise")

    def _ev_supercruiseexit(self, ev):
        self._state["location"]["supercruise"] = False
        self._state["location"]["body"] = ev.get("Body")
        self._add_event("SupercruiseExit", f"Dropped from supercruise near {ev.get('Body', 'unknown body')}")

    def _ev_docked(self, ev):
        loc = self._state["location"]
        loc["docked"] = True
        loc["station"] = ev.get("StationName")
        loc["system"] = ev.get("StarSystem", loc["system"])
        self._add_event("Docked", f"Docked at {loc['station']}")

    def _ev_undocked(self, ev):
        self._state["location"]["docked"] = False
        self._state["location"]["station"] = None
        self._add_event("Undocked", "Undocked")

    def _ev_dockingrequested(self, ev):
        self._add_event("DockingRequested", f"Docking requested at {ev.get('StationName', 'station')}")

    def _ev_dockinggranted(self, ev):
        self._add_event("DockingGranted", f"Docking granted — pad {ev.get('LandingPad', '?')}")

    def _ev_dockingdenied(self, ev):
        self._add_event("DockingDenied", f"Docking denied — {ev.get('Reason', 'unknown reason')}")

    def _ev_scan(self, ev):
        self._state["session_stats"]["scans"] += 1
        body = ev.get("BodyName", "unknown body")
        distance = ev.get("DistanceFromArrivalLS")
        planet_class = ev.get("PlanetClass") or ev.get("StarType")
        tag = _VALUABLE_CLASSES.get(planet_class)
        dist_str = f" ({distance:,.0f}ls)" if isinstance(distance, (int, float)) else ""
        text = f"Scanned {body}{dist_str}"
        if planet_class:
            text += f" — {planet_class}"
        if tag:
            text += f" [{tag}]"
        self._state["current_scan"] = {
            "body": body,
            "distance_ls": distance,
            "planet_class": planet_class,
            "valuable": tag,
            "landable": ev.get("Landable", False),
            "terraform_state": ev.get("TerraformState", ""),
        }
        self._add_event("Scan", text)

    def _ev_fssdiscoveryscan(self, ev):
        self._add_event("FSSDiscoveryScan", f"Discovery scan — {ev.get('BodyCount', 0)} bodies, {ev.get('NonBodyCount', 0)} signals")

    def _ev_fssbodysignals(self, ev):
        count = len(ev.get("Signals", []) or [])
        self._add_event("FSSBodySignals", f"{count} signal(s) on {ev.get('BodyName', 'body')}")

    def _ev_saasignalsfound(self, ev):
        count = len(ev.get("Signals", []) or [])
        self._add_event("SAASignalsFound", f"DSS scan — {count} signal(s) on {ev.get('BodyName', 'body')}")

    def _ev_missionaccepted(self, ev):
        self._add_event("MissionAccepted", f"Mission accepted — {ev.get('LocalisedName', ev.get('Name', 'mission'))}")

    def _ev_missioncompleted(self, ev):
        reward = ev.get("Reward", 0) or 0
        self._state["session_stats"]["credits_earned"] += reward
        self._add_event("MissionCompleted", f"Mission complete — {reward:,} Cr")

    def _ev_missionfailed(self, ev):
        self._add_event("MissionFailed", f"Mission failed — {ev.get('Name', 'mission')}")

    def _ev_bounty(self, ev):
        reward = ev.get("TotalReward", ev.get("Reward", 0)) or 0
        faction = ev.get("VictimFaction", "unknown faction")
        self._state["session_stats"]["bounties_collected"] += 1
        self._state["session_stats"]["credits_earned"] += reward
        self._add_event("Bounty", f"Bounty: {reward:,} Cr ({faction})")

    def _ev_shiptargeted(self, ev):
        if not ev.get("TargetLocked", True):
            self._state["target"] = None
            return
        self._state["target"] = {
            "ship": ev.get("Ship_Localised") or ev.get("Ship"),
            "pilot_name": ev.get("PilotName_Localised") or ev.get("PilotName"),
            "pilot_rank": ev.get("PilotRank"),
            "bounty": ev.get("Bounty"),
            "faction": ev.get("Faction"),
            "legal_status": ev.get("LegalStatus"),
        }

    def _ev_underattack(self, ev):
        self._state["status"]["in_danger"] = True
        self._add_event("UnderAttack", f"Under attack — {ev.get('Target', 'unknown target')}")

    def _ev_hulldamage(self, ev):
        health = (ev.get("Health", 0) or 0) * 100
        self._add_event("HullDamage", f"Hull damage — {health:.0f}% remaining")

    def _ev_shieldstate(self, ev):
        up = bool(ev.get("ShieldsUp", False))
        self._state["status"]["shields_up"] = up
        self._add_event("ShieldState", f"Shields {'up' if up else 'down'}")

    def _ev_fuelscoop(self, ev):
        self._state["fuel"]["main"] = ev.get("Total")
        self._add_event("FuelScoop", f"Scooped {ev.get('Scooped', 0):.1f}T fuel")

    def _ev_reservoirreplenished(self, ev):
        self._state["fuel"]["main"] = ev.get("FuelMain", self._state["fuel"]["main"])
        self._state["fuel"]["reservoir"] = ev.get("FuelReservoir", self._state["fuel"]["reservoir"])

    def _ev_cargoscoop(self, ev):
        self._add_event("CargoScoop", f"Scooped {ev.get('Type_Localised', ev.get('Type', 'cargo'))}")

    def _ev_marketbuy(self, ev):
        self._add_event("MarketBuy", f"Bought {ev.get('Count', 0)}x {ev.get('Type_Localised', ev.get('Type', ''))}")

    def _ev_marketsell(self, ev):
        count = ev.get("Count", 0) or 0
        sell = ev.get("SellPrice", 0) or 0
        paid = ev.get("AvgPricePaid", 0) or 0
        profit = count * (sell - paid)
        self._add_event("MarketSell", f"Sold {count}x {ev.get('Type_Localised', ev.get('Type', ''))} ({profit:+,.0f} Cr)")

    def _ev_engineercraft(self, ev):
        self._add_event("EngineerCraft", f"Crafted {ev.get('BlueprintName', 'blueprint')} at {ev.get('Engineer', 'engineer')}")

    def _ev_materialcollected(self, ev):
        self._add_event("MaterialCollected", f"Collected {ev.get('Count', 0)}x {ev.get('Name_Localised', ev.get('Name', 'material'))}")

    def _ev_receivetext(self, ev):
        sender = ev.get("From", "Unknown")
        message = ev.get("Message_Localised") or ev.get("Message", "")
        self._add_event("ReceiveText", f"{sender}: {message}")

    def _ev_sendtext(self, ev):
        self._add_event("SendText", f"Sent: {ev.get('Message', '')}")

    def _ev_died(self, ev):
        self._state["session_stats"]["deaths"] += 1
        killers = ev.get("Killers", []) or []
        killer_names = ", ".join(k.get("Name", "unknown") for k in killers) if killers else ev.get("KillerName", "unknown")
        self._add_event("Died", f"CMDR destroyed — killed by {killer_names}")

    def _ev_resurrect(self, ev):
        self._add_event("Resurrect", "Respawned")

    def _ev_shutdown(self, ev):
        self._add_event("Shutdown", "Game closed")

    # -- status.json --------------------------------------------------------

    def _handle_status(self, packet: dict):
        flags = packet.get("flags", {}) or {}
        status = self._state["status"]
        status["shields_up"] = flags.get("ShieldsUp")
        status["hardpoints"] = flags.get("HardpointsDeployed")
        status["silent_running"] = flags.get("SilentRunning")
        status["cargo_scoop"] = flags.get("CargoScoop")
        status["overheating"] = flags.get("Overheating")
        status["in_danger"] = flags.get("IsInDanger")
        status["being_interdicted"] = flags.get("BeingInterdicted")
        status["legal_state"] = packet.get("legal_state", status["legal_state"])
        status["pips"] = packet.get("pips", status["pips"])
        status["cargo"] = packet.get("cargo", status["cargo"])

        loc = self._state["location"]
        loc["docked"] = bool(flags.get("Docked", loc["docked"]))
        loc["landed"] = bool(flags.get("Landed", loc["landed"]))
        loc["supercruise"] = bool(flags.get("Supercruise", loc["supercruise"]))

        fuel = self._state["fuel"]
        fuel["main"] = packet.get("fuel_main", fuel["main"])
        fuel["reservoir"] = packet.get("fuel_reservoir", fuel["reservoir"])
        fuel["low"] = bool(flags.get("LowFuel", False))

    def _handle_paste(self, text: str):
        self._state["last_paste"] = text
        self._add_event("Paste", text[:80])


# Singleton, mirroring integrations/forza_telemetry.py's module-level API.
_listener: Optional[EDTelemetryListener] = None


def get_listener(port: int = 8003) -> EDTelemetryListener:
    global _listener
    if _listener is None:
        _listener = EDTelemetryListener(port=port)
        _listener.start()
    return _listener


def get_snapshot() -> dict:
    return get_listener().snapshot()


def is_active() -> bool:
    return get_listener().is_active()
