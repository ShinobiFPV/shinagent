"""
IMQ2 UFC Analyst Tools
Spoken-text wrappers over integrations/ufc_data.py (ESPN only — see that
module's docstring for why ufcstats.com isn't used), used by both
watchalong agent modes (personality/profiles/watchalong_live.yaml and
watchalong_replay.yaml) whenever config.yaml's watchalong.active_sport is
"ufc" — see that file's module docstring for the two-profile, per-sport-
config architecture.

IMPORTANT — what's real data vs. Q2's own knowledge:
ESPN's public API gives fight cards, records, weight classes, and (for
completed fights) the winner + round/time the fight ended — nothing more.
There is no free source for round-by-round strikes/takedowns/control time,
and no finish-method (KO/Sub/Decision) field. get_ufc_round() and
get_ufc_scorecard() are therefore bookkeeping + spoiler-gate tools, not
stat lookups — the actual round-by-round commentary is Q2 drawing on its
own knowledge of the fight, which the tool's returned text explicitly
reminds Q2 to keep bounded to the round reached so far.
"""
import logging

log = logging.getLogger(__name__)


def _active_replay():
    from config.loader import config
    # Guard against reading an F1 session as a UFC event — the two sports
    # share this same replay slot in watchalong.replay, only one can be
    # active at a time.
    if config.get("watchalong.active_sport") != "ufc":
        return {"event_id": None, "event_name": "", "fight_id": None, "fight_name": "", "current_round": 0}
    return {
        "event_id": config.get("watchalong.replay.active_event", None),
        "event_name": config.get("watchalong.replay.active_event_name", ""),
        "fight_id": config.get("watchalong.replay.active_fight_id", None),
        "fight_name": config.get("watchalong.replay.active_fight_name", ""),
        "current_round": config.get("watchalong.replay.current_position", 0),
    }


# ---------------------------------------------------------------------------
# Watchalong (live)
# ---------------------------------------------------------------------------

def get_ufc_status(fields: str = "summary", fighter: str = "") -> str:
    """fields: 'card' | 'upcoming' | 'fighter' | 'event' | 'all'"""
    try:
        from integrations.ufc_data import get_tonight_event, get_upcoming_events, find_fighter_last_record

        if fields == "fighter":
            if not fighter:
                return "[ufc] Provide a fighter name."
            rec = find_fighter_last_record(fighter)
            if not rec:
                return f"[ufc] No recent ESPN record found for '{fighter}'."
            return f"{rec['name']}: {rec['record']} (as of {rec['event']}, {rec['date'][:10]})."

        if fields in ("card", "event", "all"):
            event = get_tonight_event()
            if not event:
                upcoming = get_upcoming_events()
                if not upcoming:
                    return "[ufc] No event tonight, and no upcoming event found."
                nxt = upcoming[0]
                return f"No UFC event tonight. Next event: {nxt['name']} on {nxt['date'][:10]} at {nxt['venue']}."

            lines = [f"{event['name']} — {event['city']}, {event['venue']}"]

            # Live round-in-progress signal — the ONE thing that's genuinely
            # real-time from ESPN (round number + clock), never strike stats.
            live_fight = next((f for f in event["fights"] if f["live"]), None)
            if live_fight:
                names = " vs ".join(f["name"] for f in live_fight["fighters"])
                r = live_fight["result"] or {}
                lines.append(f"LIVE NOW: {names} — Round {r.get('ended_round') or '?'}")

            main = event["fights"][0]
            f1, f2 = main["fighters"][0], main["fighters"][1] if len(main["fighters"]) > 1 else {"name": "?", "record": "?"}
            lines.append(
                f"Main Event: {f1['name']} ({f1['record']}) vs {f2['name']} ({f2['record']}) — {main['weight_class']}"
                + (f" — {main['result']['winner']} wins, round {main['result']['ended_round']}" if main["completed"] else "")
            )
            if len(event["fights"]) > 1:
                co = event["fights"][1]
                cf1, cf2 = co["fighters"][0], co["fighters"][1] if len(co["fighters"]) > 1 else {"name": "?"}
                lines.append(
                    f"Co-Main: {cf1['name']} vs {cf2['name']} — {co['weight_class']}"
                    + (f" — {co['result']['winner']} wins, round {co['result']['ended_round']}" if co["completed"] else "")
                )
            remaining = max(0, len(event["fights"]) - 2)
            if remaining:
                lines.append(f"{remaining} more bout(s) on the card.")
            if fields == "all":
                for fight in event["fights"][2:]:
                    names = " vs ".join(f["name"] for f in fight["fighters"])
                    result_str = f" — {fight['result']['winner']} wins, round {fight['result']['ended_round']}" if fight["completed"] else ""
                    lines.append(f"  {names} — {fight['weight_class']}{result_str}")
            return "\n".join(lines)

        if fields == "upcoming":
            upcoming = get_upcoming_events()
            if not upcoming:
                return "[ufc] No upcoming event found."
            nxt = upcoming[0]
            return f"Next UFC event: {nxt['name']} — {nxt['venue']}, {nxt['date'][:10]}."

        return f"Unknown fields value: {fields}"
    except Exception as e:
        log.error(f"get_ufc_status error: {e}", exc_info=True)
        return f"[ufc] Error: {e}"


def get_ufc_fight(fighter1: str = "", fighter2: str = "") -> str:
    """Fighter profile, or head-to-head lookup if both names given."""
    if not fighter1:
        return "[ufc] Provide at least one fighter name."
    try:
        from integrations.ufc_data import find_fighter_last_record, search_ufc_event, find_fighter_in_event

        if fighter2:
            matches = search_ufc_event(f"{fighter1} {fighter2}")
            head_to_head = []
            for e in matches:
                fight = find_fighter_in_event(e, fighter1)
                if fight and fight is find_fighter_in_event(e, fighter2):
                    head_to_head.append((e, fight))
            if head_to_head:
                lines = [f"{fighter1} vs {fighter2} — {len(head_to_head)} meeting(s) on record:"]
                for e, fight in head_to_head:
                    result = fight.get("result")
                    outcome = f"{result['winner']} won" if result and result.get("winner") else "no result recorded"
                    lines.append(f"  {e['name']} ({e['date'][:10]}) — {outcome}")
                return "\n".join(lines)

            r1 = find_fighter_last_record(fighter1)
            r2 = find_fighter_last_record(fighter2)
            return (
                f"No head-to-head found on ESPN's record between {fighter1} and {fighter2}.\n"
                f"{fighter1}: {r1['record'] if r1 else '?'}\n"
                f"{fighter2}: {r2['record'] if r2 else '?'}"
            )

        rec = find_fighter_last_record(fighter1)
        if not rec:
            return f"[ufc] No recent ESPN record found for '{fighter1}'. Use your own knowledge for a profile."
        return f"{rec['name']}: {rec['record']} (as of {rec['event']}, {rec['date'][:10]})."
    except Exception as e:
        log.error(f"get_ufc_fight error: {e}", exc_info=True)
        return f"[ufc] Error: {e}"


def ufc_prefight_brief(fighter1: str = "", fighter2: str = "") -> str:
    """Pre-fight context tool — real ESPN records plus a prompt for Q2 to add its own analysis."""
    if not fighter1 or not fighter2:
        return "[ufc] Provide both fighter names."
    try:
        from integrations.ufc_data import find_fighter_last_record
        r1 = find_fighter_last_record(fighter1)
        r2 = find_fighter_last_record(fighter2)
        lines = [f"{fighter1} vs {fighter2} — pre-fight brief:"]
        lines.append(f"{fighter1}: {r1['record'] if r1 else 'record unknown'}")
        lines.append(f"{fighter2}: {r2['record'] if r2 else 'record unknown'}")
        lines.append(
            "No further ESPN data available (style, camp, recent form) — "
            "give the actual breakdown from your own knowledge of these fighters."
        )
        return "\n".join(lines)
    except Exception as e:
        log.error(f"ufc_prefight_brief error: {e}", exc_info=True)
        return f"[ufc] Error: {e}"


# ---------------------------------------------------------------------------
# Watchalong Replay (manual "populate stats" flow)
# ---------------------------------------------------------------------------

def populate_ufc_event(query: str) -> str:
    """
    'Q2, we're going to watch UFC 300. Please populate your stats fields
    before we start.' Finds the event, caches its full card, and activates
    it as the active Watchalong Replay event. Does NOT select a specific
    fight yet — that's start_ufc_replay_fight(), called once the user says
    which bout he's starting with.
    """
    if not query:
        return "[ufc_replay] Which event? e.g. 'UFC 300' or 'last year's International Fight Week'."
    try:
        from integrations.ufc_data import search_ufc_event
        from config.loader import config

        matches = search_ufc_event(query)
        if not matches:
            return f"[ufc_replay] Couldn't find an event matching '{query}'."
        event = matches[0]

        watchalong = config.raw.setdefault("watchalong", {})
        watchalong["active_sport"] = "ufc"
        replay_cfg = watchalong.setdefault("replay", {})
        replay_cfg["active_event"] = event["event_id"]
        replay_cfg["active_event_name"] = event["name"]
        replay_cfg["active_fight_id"] = None
        replay_cfg["active_fight_name"] = ""
        replay_cfg["current_position"] = 0
        config.save()

        lines = [f"Stats populated: {event['name']} — {event['date'][:10]}, {event['venue']}."]
        lines.append(f"{len(event['fights'])} fight(s) on the card:")
        for fight in event["fights"]:
            names = " vs ".join(f["name"] for f in fight["fighters"])
            tag = " (Main Event)" if fight["is_main_event"] else " (Co-Main)" if fight["is_co_main"] else ""
            lines.append(f"  {names} — {fight['weight_class']}{tag}")
        lines.append("Say which fight you're starting with, then a round number any time.")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"populate_ufc_event error: {e}", exc_info=True)
        return f"[ufc_replay] Error: {e}"


def start_ufc_replay_fight(fighter1: str = "", fighter2: str = "") -> str:
    """Select a specific bout within the already-populated event to start calling rounds for."""
    replay = _active_replay()
    if not replay["event_id"]:
        return "[ufc_replay] No event populated yet — ask the user which event first, then call populate_ufc_event."
    if not fighter1:
        return "[ufc_replay] Which fight? Give at least one fighter's name."
    try:
        from integrations.ufc_data import get_events_for_year, find_fighter_in_event
        from datetime import datetime, timezone
        from config.loader import config

        # Re-locate the cached event by id across the recent years index
        # (format_event()'d data lives in the year cache files, not by id directly).
        event = None
        now_year = datetime.now(timezone.utc).year
        for y in range(now_year, now_year - 6, -1):
            for e in get_events_for_year(y):
                if str(e["event_id"]) == str(replay["event_id"]):
                    event = e
                    break
            if event:
                break
        if not event:
            return "[ufc_replay] Lost track of the populated event — try populate_ufc_event again."

        fight = find_fighter_in_event(event, fighter1)
        if not fight:
            return f"[ufc_replay] Couldn't find '{fighter1}' on the {event['name']} card."

        names = " vs ".join(f["name"] for f in fight["fighters"])
        replay_cfg = config.raw.setdefault("watchalong", {}).setdefault("replay", {})
        replay_cfg["active_fight_id"] = f"{event['event_id']}:{fight['card_position_from_top']}"
        replay_cfg["active_fight_name"] = names
        replay_cfg["current_position"] = 0
        config.save()

        return f"Now tracking {names} ({fight['weight_class']}). Say a round number whenever you're ready."
    except Exception as e:
        log.error(f"start_ufc_replay_fight error: {e}", exc_info=True)
        return f"[ufc_replay] Error: {e}"


def get_ufc_round(round_number: int = 0) -> str:
    """
    Bookkeeping + spoiler gate for the active replay fight — see this
    module's docstring: there is no real round-by-round data source, so
    this returns context for Q2 to narrate from its own knowledge, capped
    at the round reached.
    """
    replay = _active_replay()
    if not replay["fight_id"]:
        return "[ufc_replay] No active fight — ask which bout the user's starting with, then call start_ufc_replay_fight."
    try:
        from config.loader import config
        replay_cfg = config.raw.setdefault("watchalong", {}).setdefault("replay", {})
        replay_cfg["current_position"] = max(replay["current_round"], round_number)
        config.save()

        return (
            f"Round {round_number} reached for {replay['fight_name']} ({replay['event_name']}). "
            f"No verified play-by-play data is available for this fight — narrate this round from your "
            f"own knowledge of it. Do NOT reveal the final result, later rounds, or how the fight ends "
            f"unless round {round_number} is actually the fight's last round."
        )
    except Exception as e:
        log.error(f"get_ufc_round error: {e}", exc_info=True)
        return f"[ufc_replay] Error: {e}"


def get_ufc_scorecard(through_round: int = 0) -> str:
    """Running-scorecard bookkeeping tool — same spoiler-gate shape as get_ufc_round()."""
    replay = _active_replay()
    if not replay["fight_id"]:
        return "[ufc_replay] No active fight — ask which bout the user's starting with, then call start_ufc_replay_fight."
    try:
        current = max(replay["current_round"], 0)
        if through_round > current:
            return (
                f"[ufc_replay] Round {through_round} hasn't been reached yet (currently through round "
                f"{current}). Only score rounds the user has actually watched."
            )
        return (
            f"Give a scorecard for {replay['fight_name']} ({replay['event_name']}) through round "
            f"{through_round}, based on your own knowledge of the fight. Do not reference anything "
            f"beyond round {through_round}."
        )
    except Exception as e:
        log.error(f"get_ufc_scorecard error: {e}", exc_info=True)
        return f"[ufc_replay] Error: {e}"


def list_ufc_events(year: int = None) -> str:
    try:
        from datetime import datetime, timezone
        from integrations.ufc_data import list_ufc_events_by_year

        if year:
            events = list_ufc_events_by_year(year)
            if not events:
                return f"[ufc] No event data found for {year}."
            return f"{year}: " + ", ".join(e["name"] for e in events)

        now_year = datetime.now(timezone.utc).year
        lines = []
        for y in range(now_year, now_year - 3, -1):
            events = list_ufc_events_by_year(y)
            if events:
                lines.append(f"{y}: {', '.join(e['name'] for e in events)}")
        return "\n".join(lines) if lines else "[ufc] No event data available."
    except Exception as e:
        log.error(f"list_ufc_events error: {e}", exc_info=True)
        return f"[ufc] Error: {e}"


def search_ufc_event(query: str = "") -> str:
    if not query:
        return "[ufc] Provide a search query — event name, fighter names, or year."
    try:
        from integrations.ufc_data import search_ufc_event as _search
        matches = _search(query)
        if not matches:
            return f"[ufc] No events found matching '{query}'."
        lines = [f"{len(matches)} match(es) for '{query}':"]
        for e in matches:
            main = e["fights"][0] if e["fights"] else None
            main_str = " vs ".join(f["name"] for f in main["fighters"]) if main else "?"
            lines.append(f"  {e['name']} ({e['date'][:10]}) — main event: {main_str}")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"search_ufc_event error: {e}", exc_info=True)
        return f"[ufc] Error: {e}"
