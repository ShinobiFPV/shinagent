"""
IMQ2 Pop-Up Video data layer
Storage and lookup for Watchalong Pop-Up Video mode -- MTV Pop Up Video style
timestamped fact bubbles for films/TV, pre-generated before viewing and
delivered as the user calls out timestamps during playback.
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

POPUP_DIR = Path(__file__).parent.parent / "cache" / "popups"

POPUP_TYPES = ("FACT", "CAST", "MUSIC", "LOCATION", "TECH", "CORRECTION", "HISTORY", "EASTER_EGG")


def slugify(title: str, year: Optional[int] = None) -> str:
    base = title.lower().strip()
    base = re.sub(r"[^a-z0-9\s]", "", base)
    base = re.sub(r"\s+", "_", base).strip("_")
    if year:
        base = f"{base}_{year}"
    return base


def _fmt_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class PopUpSession:
    """All pop-ups for a single title (or a single TV episode)."""

    def __init__(self, title: str, year: Optional[int] = None, media_type: str = "movie",
                 episode: str = ""):
        self.title = title
        self.year = year
        self.media_type = media_type
        self.episode = episode
        self.popups: list[dict] = []
        self.metadata: dict = {}
        self.generated: bool = False
        self.current_ts: int = 0

    @property
    def slug(self) -> str:
        return slugify(self.title, self.year)

    def generate(self, llm_client, web_search_fn) -> int:
        """
        Research the title via web_search_fn and generate a full pop-up list
        via llm_client. llm_client.complete(messages, system, max_tokens) is
        the standard core.llm.LLMBackend interface. Returns count generated.
        """
        from tools.popup_video import _research_title, _build_popup_prompt, _parse_popup_json

        research = _research_title(self.title, self.year, web_search_fn)
        prompt = _build_popup_prompt(self.title, self.year, self.media_type, self.episode, research)

        response = llm_client.complete(
            messages=[{"role": "user", "content": "Generate the pop-ups now."}],
            system=prompt,
            max_tokens=4000,
        )
        popups = _parse_popup_json(response.text)

        for p in popups:
            p["timestamp_seconds"] = int(p.get("timestamp_seconds", 0))
            p["timestamp_display"] = p.get("timestamp_display") or _fmt_timestamp(p["timestamp_seconds"])
            p["type"] = p.get("type") if p.get("type") in POPUP_TYPES else "FACT"
            p["delivered"] = False
            p.setdefault("source", "llm_research")

        popups.sort(key=lambda p: p["timestamp_seconds"])
        self.popups = popups
        self.generated = True
        return len(self.popups)

    def get_popup_at(self, timestamp_seconds: int, window: int = 30) -> Optional[dict]:
        best = None
        best_dist = window + 1
        for p in self.popups:
            if p.get("delivered"):
                continue
            dist = abs(p["timestamp_seconds"] - timestamp_seconds)
            if dist <= window and dist < best_dist:
                best = p
                best_dist = dist
        if best is not None:
            best["delivered"] = True
        return best

    def get_popups_in_range(self, start_s: int, end_s: int) -> list:
        return [p for p in self.popups
                if not p.get("delivered") and start_s <= p["timestamp_seconds"] <= end_s]

    def get_upcoming(self, timestamp_seconds: int, count: int = 3) -> list:
        upcoming = [p for p in self.popups
                    if not p.get("delivered") and p["timestamp_seconds"] >= timestamp_seconds]
        return upcoming[:count]

    def reset_delivery(self):
        for p in self.popups:
            p["delivered"] = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "year": self.year,
            "media_type": self.media_type,
            "episode": self.episode,
            "popups": self.popups,
            "metadata": self.metadata,
            "generated": self.generated,
            "current_ts": self.current_ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PopUpSession":
        s = cls(
            title=data.get("title", ""),
            year=data.get("year"),
            media_type=data.get("media_type", "movie"),
            episode=data.get("episode", ""),
        )
        s.popups = data.get("popups", [])
        s.metadata = data.get("metadata", {})
        s.generated = data.get("generated", False)
        s.current_ts = data.get("current_ts", 0)
        return s


class PopUpLibrary:
    """Manages all saved pop-up sessions on disk under cache/popups/."""

    def __init__(self, directory: Path = POPUP_DIR):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def list_titles(self) -> list:
        titles = []
        for f in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                titles.append({
                    "slug": f.stem,
                    "title": data.get("title", f.stem),
                    "year": data.get("year"),
                    "popup_count": len(data.get("popups", [])),
                })
            except Exception as e:
                log.warning(f"popup_video: failed to read {f}: {e}")
        return titles

    def get(self, title_slug: str) -> Optional[PopUpSession]:
        path = self.directory / f"{title_slug}.json"
        if not path.exists():
            return None
        try:
            return PopUpSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning(f"popup_video: failed to load session {title_slug}: {e}")
            return None

    def save(self, session: PopUpSession):
        path = self.directory / f"{session.slug}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")

    def delete(self, title_slug: str):
        path = self.directory / f"{title_slug}.json"
        if path.exists():
            path.unlink()

    def search(self, query: str) -> list:
        query = query.lower().strip()
        return [t for t in self.list_titles() if query in t["title"].lower() or query in t["slug"]]
