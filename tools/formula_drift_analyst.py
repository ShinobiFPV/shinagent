"""
IMQ2 Formula Drift Analyst Tools
Spoken-text wrappers over integrations/formula_drift_data.py, used by
both watchalong agent modes whenever config.yaml's watchalong.active_sport
is "formula_drift". Formula Drift has no live data feed at all (see that
module's docstring) -- "Live" mode here means Q2 has standings/driver
context to discuss, not a real-time bracket feed the way F1/NBA/NHL do.
"""
import logging

log = logging.getLogger(__name__)

_COUNTRY_FLAGS = {
    "Ireland": "\U0001F1EE\U0001F1EA", "USA": "\U0001F1FA\U0001F1F8",
    "Norway": "\U0001F1F3\U0001F1F4", "Japan": "\U0001F1EF\U0001F1F5",
    "Lithuania": "\U0001F1F1\U0001F1F9", "Dominican Republic": "\U0001F1E9\U0001F1F4",
}


def get_fd_standings(year: int = None) -> str:
    """Formula Drift PRO championship standings for a year (default:
    current year, or the last completed season if scraping the current
    year returns nothing)."""
    try:
        from integrations.formula_drift_data import get_fd_client
        from datetime import datetime

        y = year or datetime.now().year
        fd = get_fd_client()
        standings = fd.get_standings(y)
        if not standings:
            return "Formula Drift standings not available."

        lines = [f"Formula DRIFT PRO Championship {y} Standings:"]
        for d in standings[:8]:
            flag = _COUNTRY_FLAGS.get(d.get("country", ""), "")
            car = d.get("car") or (f"Car #{d['car_number']}" if d.get("car_number") else "")
            lines.append(f"P{d['position']}. {d['name']} {flag} -- {d['points']:.0f} pts" + (f" -- {car}" if car else ""))
        return "\n".join(lines)
    except Exception as e:
        log.error(f"get_fd_standings error: {e}", exc_info=True)
        return f"[formula_drift] Error: {e}"


def get_fd_round(round_num: int, year: int = None) -> str:
    """Details for a specific Formula Drift round (name, location, date,
    status) from the season schedule."""
    try:
        from integrations.formula_drift_data import get_fd_client
        from datetime import datetime

        y = year or datetime.now().year
        fd = get_fd_client()
        schedule = fd.get_schedule(y)

        for event in schedule:
            if event.get("round") == round_num:
                lines = [f"Formula DRIFT Round {round_num}: {event['name']}"]
                if event.get("location"):
                    lines.append(f"Location: {event['location']}")
                if event.get("date"):
                    lines.append(f"Date: {event['date']}")
                lines.append(f"Status: {event.get('status', 'unknown').upper()}")
                return "\n".join(lines)

        return f"Round {round_num} not found in the {y} schedule."
    except Exception as e:
        log.error(f"get_fd_round error: {e}", exc_info=True)
        return f"[formula_drift] Error: {e}"
