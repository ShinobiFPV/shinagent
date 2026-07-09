"""
Formula Drift Data Integration
================================
Scrapes formulad.com for standings and schedule data. No API key exists
for Formula Drift -- there is no public data feed at all, live or
historical, so this is scraping-only with a hard-coded fallback.

Verified against the real site (formulad.com, checked directly) rather
than guessed selectors:
  - Standings (/standings/{year}/pro) IS a real server-rendered <table>,
    not a JS-only shell. Its real columns are RANK, CAR #, DRIVER (name
    inside an <a>), then SB/ME (seeding/main-event) pairs per round, then
    a TOTAL cell carrying class="TotalText" -- NOT a fixed cells[1]/
    cells[2] position the way a naive guess would assume. See
    _parse_standings_row() for the corrected column mapping.
  - Schedule has no /schedule/{year} route at all (that 404s) -- the
    real path is just /schedule, which always shows the current/most
    recent season. It's also a much more fragile target: a Next.js app
    with deeply nested Tailwind utility classes and no stable semantic
    hooks, unlike the standings table. The schedule scraper here is
    best-effort and more likely to fall back to hard-coded data than
    the standings one.
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

FD_BASE = "https://www.formulad.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "formula_drift"


class FormulaDriftClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ShinAgent/1.0",
            "Accept": "text/html,application/xhtml+xml",
        })
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_html(self, url: str, cache_hours: int = 24) -> Optional[BeautifulSoup]:
        cache_key = url.replace("https://", "").replace("/", "_")
        cache_file = CACHE_DIR / f"{cache_key}.html"

        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < cache_hours:
                return BeautifulSoup(cache_file.read_text(encoding="utf-8"), "html.parser")

        try:
            r = self._session.get(url, timeout=10)
            r.raise_for_status()
            cache_file.write_text(r.text, encoding="utf-8")
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log.debug(f"formula_drift: fetch failed for {url}: {e}")
            if cache_file.exists():
                return BeautifulSoup(cache_file.read_text(encoding="utf-8"), "html.parser")
            return None

    def get_standings(self, year: int = None) -> list:
        """PRO championship standings for a year. Real table structure
        (verified): RANK / CAR # / DRIVER / (SB, ME) x8 rounds / TOTAL."""
        y = year or datetime.now().year
        url = f"{FD_BASE}/standings/{y}/pro"
        soup = self._get_html(url, cache_hours=6)
        if not soup:
            return _get_2025_standings_fallback()

        drivers = []
        # The page renders three tables (drivers, then two others that
        # look like manufacturer/tyre standings) -- the driver table is
        # the one with a RANK/CAR #/DRIVER header, not just "the first
        # table", since column count/order isn't guaranteed stable.
        for table in soup.select("table"):
            header_text = " ".join(th.get_text(strip=True).upper() for th in table.select("thead th"))
            if "DRIVER" in header_text and "RANK" in header_text:
                drivers = self._parse_standings_table(table, y)
                break

        return drivers or _get_2025_standings_fallback()

    @staticmethod
    def _parse_standings_table(table, year: int) -> list:
        drivers = []
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            try:
                pos_text = cells[0].get_text(strip=True)
                car_number = cells[1].get_text(strip=True)
                name_cell = cells[2]
                name_link = name_cell.find("a")
                name = (name_link.get_text(strip=True) if name_link else name_cell.get_text(strip=True))

                total_cell = row.select_one("td.TotalText") or cells[-2]
                points_text = total_cell.get_text(strip=True)
                points_clean = "".join(c for c in points_text if c.isdigit() or c == ".")

                if not name or not pos_text.isdigit():
                    continue

                drivers.append({
                    "position": int(pos_text),
                    "name": name,
                    "car_number": car_number,
                    "points": float(points_clean) if points_clean else 0,
                    "year": year,
                })
            except (ValueError, IndexError):
                continue
        drivers.sort(key=lambda d: d["position"])
        return drivers

    def get_schedule(self, year: int = None) -> list:
        """
        Event schedule. Best-effort: formulad.com's /schedule page is a
        Next.js app with no stable semantic classes (verified -- no
        .schedule-item/.event-item/article match anything real), so this
        looks for the one structural signal that IS stable: links shaped
        like /schedule/{year}/{event-slug}, paired with the nearest
        heading for a name. Falls back to hard-coded data readily, since
        this page's markup is more likely to drift than the standings
        table.
        """
        y = year or datetime.now().year
        soup = self._get_html(f"{FD_BASE}/schedule", cache_hours=24)
        if not soup:
            return _get_2025_schedule_fallback()

        events = []
        seen_slugs = set()
        for link in soup.select(f'a[href^="/schedule/{y}/"]'):
            href = link.get("href", "")
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen_slugs:
                continue

            card = link
            heading = None
            for _ in range(6):
                card = card.parent if card else None
                if card is None:
                    break
                heading = card.find(["h1", "h2", "h3"])
                if heading and heading.get_text(strip=True):
                    break
            heading_text = heading.get_text(strip=True) if heading else ""
            # Regular grid cards' nearest heading is often just a "City,
            # State, USA" location caption, not the event name -- the slug
            # ("las-vegas") reads better than that in the UI than a bare
            # comma-separated location string does, so prefer it whenever
            # the heading looks like a location rather than a title.
            looks_like_location = "," in heading_text and heading_text.upper().endswith("USA")
            name = slug.replace("-", " ").title() if (not heading_text or looks_like_location) else heading_text

            seen_slugs.add(slug)
            events.append({
                "round": len(events) + 1,
                "name": name,
                "location": heading_text if looks_like_location else "",
                "date": "",
                "status": "unknown",
                "slug": slug,
                "year": y,
            })

        return events or _get_2025_schedule_fallback()

    def get_driver_info(self, driver_name: str) -> dict:
        """
        Best-effort driver lookup. formulad.com/drivers has no semantic
        driver-card markup either (verified -- Tailwind grid utility
        classes only, no team/car text visible on the listing page
        itself), so this can only confirm whether the name is a real
        listed driver, not scrape car/team detail. Callers needing
        car/team should rely on the hard-coded fallback roster instead.
        """
        soup = self._get_html(f"{FD_BASE}/drivers", cache_hours=168)
        if not soup:
            return {"name": driver_name, "car": "Unknown", "team": "Unknown"}

        driver_lower = driver_name.lower()
        for link in soup.select('a[href*="/drivers/"]'):
            if driver_lower in link.get_text(strip=True).lower():
                return {"name": link.get_text(strip=True), "car": "Unknown", "team": "Unknown"}

        return {"name": driver_name, "car": "Unknown", "team": "Unknown"}


def _get_2025_standings_fallback() -> list:
    """Hard-coded fallback PRO standings, used when scraping fails."""
    return [
        {"position": 1, "name": "James Deane", "points": 520, "country": "Ireland",
         "car": "Ford Mustang RTR Spec 5-FD", "team": "RTR Motorsports"},
        {"position": 2, "name": "Matt Field", "points": 380, "country": "USA",
         "car": "Corvette", "team": "Drift Cave Motorsports"},
        {"position": 3, "name": "Adam LZ", "points": 355, "country": "USA",
         "car": "BMW E36", "team": "LZMFG"},
        {"position": 4, "name": "Fredric Aasbo", "points": 355, "country": "Norway",
         "car": "Toyota GR Supra", "team": "Rockstar Energy Toyota Racing"},
        {"position": 5, "name": "Hiroya Minowa", "points": 320, "country": "Japan",
         "car": "Toyota GT86", "team": "Enjuku Racing / Cusco"},
        {"position": 6, "name": "Aurimas Bakchis", "points": 290, "country": "Lithuania",
         "car": "Nissan S14.9", "team": "Feal Suspension"},
        {"position": 7, "name": "Vaughn Gittin Jr", "points": 265, "country": "USA",
         "car": "Ford Mustang RTR", "team": "RTR Motorsports"},
        {"position": 8, "name": "Jhonnattan Castro", "points": 240, "country": "Dominican Republic",
         "car": "Toyota GR86", "team": "LTH / Mobil 1"},
    ]


def _get_2025_schedule_fallback() -> list:
    """Hard-coded fallback PRO schedule, used when scraping fails."""
    return [
        {"round": 1, "name": "Long Beach Street Course", "location": "Long Beach, CA",
         "date": "April 4-5, 2025", "status": "complete"},
        {"round": 2, "name": "Atlanta Motorama", "location": "Atlanta, GA",
         "date": "May 2-3, 2025", "status": "complete"},
        {"round": 3, "name": "Orlando", "location": "Orlando Speed World, FL",
         "date": "May 31, 2025", "status": "complete"},
        {"round": 4, "name": "New Jersey: The Gauntlet", "location": "Englishtown, NJ",
         "date": "June 2025", "status": "complete"},
        {"round": 5, "name": "St Louis Crossroads", "location": "Madison, IL",
         "date": "July 19, 2025", "status": "complete"},
        {"round": 6, "name": "Pacific Showdown", "location": "Monroe, WA",
         "date": "August 2025", "status": "complete"},
        {"round": 7, "name": "Utah Elevated", "location": "Grantsville, UT",
         "date": "September 2025", "status": "complete"},
        {"round": 8, "name": "Shoreline Showdown", "location": "Long Beach, CA",
         "date": "October 18, 2025", "status": "complete"},
    ]


# Singleton
_client: Optional[FormulaDriftClient] = None


def get_fd_client() -> FormulaDriftClient:
    global _client
    if _client is None:
        _client = FormulaDriftClient()
    return _client
