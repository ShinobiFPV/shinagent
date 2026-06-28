"""
IMQ2 Google Calendar Integration
Check upcoming events and create new ones.
Uses the primary calendar for iamkewtoo@gmail.com.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


def get_upcoming_events(days: int = 7, max_results: int = 10) -> list[dict]:
    """
    Return upcoming events within the next N days.
    Each event: title, start, end, location, description, url.
    """
    from integrations.google_services import get_google_service
    cal = get_google_service("calendar")

    now   = datetime.now(timezone.utc)
    until = now + timedelta(days=days)

    resp = cal.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=until.isoformat(),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for item in resp.get("items", []):
        start = item.get("start", {})
        end   = item.get("end", {})
        events.append({
            "title":       item.get("summary", "(no title)"),
            "start":       start.get("dateTime") or start.get("date", ""),
            "end":         end.get("dateTime") or end.get("date", ""),
            "location":    item.get("location", ""),
            "description": item.get("description", ""),
            "url":         item.get("htmlLink", ""),
        })

    log.info(f"Calendar: {len(events)} events in next {days} days")
    return events


def create_event(title: str, start_dt: str, end_dt: Optional[str] = None,
                 description: str = "", location: str = "",
                 duration_minutes: int = 60) -> dict:
    """
    Create a calendar event.
    start_dt: ISO 8601 datetime string, e.g. '2026-07-01T14:00:00-04:00'
    end_dt:   optional; if omitted, defaults to start + duration_minutes.
    Returns dict with id, url, title.
    """
    from integrations.google_services import get_google_service
    cal = get_google_service("calendar")

    # Parse start
    start = datetime.fromisoformat(start_dt)
    end   = datetime.fromisoformat(end_dt) if end_dt else start + timedelta(minutes=duration_minutes)

    # Format with timezone
    tz_str   = str(start.tzinfo) if start.tzinfo else "America/Toronto"
    start_str = start.isoformat()
    end_str   = end.isoformat()

    body = {
        "summary":     title,
        "description": description,
        "location":    location,
        "start":       {"dateTime": start_str, "timeZone": tz_str},
        "end":         {"dateTime": end_str,   "timeZone": tz_str},
    }

    event = cal.events().insert(calendarId="primary", body=body).execute()
    url   = event.get("htmlLink", "")
    log.info(f"Created calendar event '{title}' at {start_str}")
    return {"id": event["id"], "url": url, "title": title,
            "start": start_str, "end": end_str}


def format_events_summary(events: list[dict]) -> str:
    """Format a list of events as a readable summary for Q2 to speak."""
    if not events:
        return "No upcoming events."

    lines = []
    for e in events:
        start = e["start"]
        # Parse and format nicely
        try:
            dt = datetime.fromisoformat(start)
            formatted = dt.strftime("%A %B %-d at %-I:%M %p")
        except Exception:
            formatted = start

        line = f"{e['title']} — {formatted}"
        if e["location"]:
            line += f" at {e['location']}"
        lines.append(line)

    return "\n".join(lines)
