"""
IMQ2 Forza Location System
===========================
Learns and recognises locations in Forza Horizon's open world by recording
X/Z coordinates at named landmarks.

Forza's PositionX/Y/Z are raw engine-space values, not real-world lat/lon,
so they can't be mapped against an existing map service. This builds a
combined landmark database from three sources:

  - "builtin"  -- a small starter set shipped with ShinAgent (BUILTIN_LANDMARKS
                  below), coordinates are approximate placeholders, not
                  verified against a live game -- see that constant's own
                  docstring.
  - "personal" -- landmarks the user records themselves while driving
                  ("mark this location as X"), stored in cache/fh6_landmarks.json.
  - community  -- landmarks imported from JSON map files shared by other
                  players, stored in data/fh6_maps/*.json (source name comes
                  from each file's own "source" field).

Usage:
  - "Q2, mark this location as Tokyo Drift Zone"
  - Q2 records the current X/Z position under that name
  - Next time you're near it, Q2 recognises and calls it out
  - Over many sessions this builds a personal map of the open world
  - "Q2, import location map <path>" merges in a community map file
  - "Q2, export my map as <name>" shares your personal landmarks back out
"""

import json
import math
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LANDMARKS_PATH = PROJECT_ROOT / "cache" / "fh6_landmarks.json"
COMMUNITY_MAPS_DIR = PROJECT_ROOT / "data" / "fh6_maps"

# Recognition radius in game units. Forza's PositionX/Y/Z are in metres,
# so this is ~150m -- a starting point, tune after testing on the real map.
# Individual landmarks may override this via their own "radius" field.
RECOGNITION_RADIUS = 150.0

# Minimum speed to trigger a location announcement, so parking at a
# landmark doesn't repeat the callout every poll cycle.
MIN_SPEED_KMH = 20.0

# ── Built-in starter landmarks ──────────────────────────────────────────
# Coordinates here are APPROXIMATE PLACEHOLDERS -- there is no way to
# verify real FH6 engine-space X/Z values without actually driving to each
# one in-game. Named after real Tokyo/Japan locations and touge culture
# that FH6's Japan map is inspired by, matching what the user would
# recognise -- but treat every (x, z) below as a rough starting guess to
# be corrected via "mark this location" once actually visited, exactly
# like the community-map workflow this module supports. Do not extend
# this list with more invented coordinates; extend via a real community
# map file (data/fh6_maps/) or in-game marking instead.
BUILTIN_LANDMARKS = [
    {"name": "Shibuya Crossing", "x": 1200.0, "z": -800.0, "y": 20.0,
     "region": "Tokyo City", "type": "landmark", "tags": ["iconic", "urban"],
     "notes": "The famous scramble crossing. One of the busiest pedestrian crossings in the world.",
     "callout": "Shibuya Crossing.", "source": "builtin"},
    {"name": "Tokyo Tower", "x": 1450.0, "z": -600.0, "y": 25.0,
     "region": "Tokyo City", "type": "landmark", "tags": ["iconic"],
     "notes": "Built in 1958, it was the tallest structure in Japan for decades.",
     "callout": "Tokyo Tower.", "source": "builtin"},
    {"name": "Ginkgo Avenue", "x": 1300.0, "z": -950.0, "y": 18.0,
     "region": "Tokyo City", "type": "landmark", "tags": ["scenic"],
     "notes": "Tree-lined avenue known for its autumn colours.",
     "callout": "Ginkgo Avenue.", "source": "builtin"},
    {"name": "C1 Loop", "x": 1100.0, "z": -700.0, "y": 22.0,
     "region": "Tokyo City", "type": "race", "tags": ["highway", "wangan"],
     "notes": "Elevated inner-city expressway loop, a real Tokyo street racing route.",
     "callout": "C1 Loop.", "source": "builtin"},
    {"name": "Daikoku PA", "x": 2100.0, "z": -1200.0, "y": 15.0,
     "region": "Tokyo City", "type": "parking", "tags": ["iconic", "car_culture"],
     "notes": ("Daikoku Parking Area -- highway rest stop on the Wangan line. Real Japanese "
               "car culture was born there. Midnight Club, Wangan Midnight, Initial D all "
               "reference it. If you're here at midnight in real life you'll see things "
               "that don't belong on a public road."),
     "callout": "Daikoku PA.", "source": "builtin"},
    {"name": "Mt. Haruna (Akina)", "x": -1500.0, "z": 7000.0, "y": 800.0,
     "region": "Hokubu", "type": "mountain", "tags": ["touge", "drift", "iconic"],
     "notes": ("The mountain that inspired Akina in Initial D. Fujiwara Takumi's home "
               "course. The real Mt. Haruna in Gunma has the same tight hairpin section."),
     "callout": "Mt. Haruna. Akina.", "source": "builtin", "radius": 400.0},
    {"name": "Akagi Downhill (Myogi)", "x": -2200.0, "z": 7500.0, "y": 900.0,
     "region": "Hokubu", "type": "mountain", "tags": ["touge"],
     "notes": "Inspired by Mt. Akagi, another Initial D location and the RedSuns' home turf.",
     "callout": "Akagi territory.", "source": "builtin", "radius": 350.0},
    {"name": "Mt. Fuji Viewpoint", "x": -3000.0, "z": 4200.0, "y": 1400.0,
     "region": "Takashiro", "type": "viewpoint", "tags": ["scenic", "iconic"],
     "notes": "A high vantage point with a view toward the map's Fuji-inspired peak.",
     "callout": "Fuji viewpoint.", "source": "builtin"},
    {"name": "Ito Coastal Road", "x": 3200.0, "z": 1100.0, "y": 5.0,
     "region": "Ito", "type": "coastal", "tags": ["scenic", "drift"],
     "notes": "Winding coastal road with ocean views on one side.",
     "callout": "Coastal road, Ito.", "source": "builtin"},
    {"name": "Ohtani Drift Zone", "x": 500.0, "z": 3400.0, "y": 60.0,
     "region": "Ohtani", "type": "drift_zone", "tags": ["drift"],
     "notes": "Open industrial area with wide corners popular for drifting.",
     "callout": "Ohtani drift zone.", "source": "builtin"},
]

_LANDMARK_DEFAULTS = {"region": "", "type": "custom", "tags": [], "notes": "", "callout": ""}


class ForzaLocationSystem:
    """Manages a database of named locations in the Forza open world,
    combining built-in, personal, and imported community sources."""

    def __init__(self):
        self._personal = self._load_personal()
        self._imported: dict[str, list] = {}  # map_source_name -> [landmarks]
        self._loaded_files: list = []
        self._last_near = None    # name of the landmark we're currently near
        self._near_since = None   # when we entered its radius
        self._reload_imported()

    # -- Loading / persistence --------------------------------------------

    def _load_personal(self) -> list:
        if LANDMARKS_PATH.exists():
            try:
                data = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
                for lm in data:
                    lm.setdefault("source", "personal")
                return data
            except Exception:
                pass
        return []

    def _save_personal(self):
        LANDMARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        LANDMARKS_PATH.write_text(json.dumps(self._personal, indent=2), encoding="utf-8")

    def _reload_imported(self):
        """(Re)scan data/fh6_maps/*.json for community map files and load
        them into self._imported, keyed by each file's own "source" name."""
        self._imported = {}
        self._loaded_files = []
        if not COMMUNITY_MAPS_DIR.exists():
            return
        for path in sorted(COMMUNITY_MAPS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                source = data.get("source", path.stem)
                landmarks = data.get("landmarks", [])
                for lm in landmarks:
                    lm.setdefault("source", source)
                self._imported[source] = landmarks
                self._loaded_files.append(str(path))
            except Exception:
                continue

    def import_map_file(self, file_path: str) -> str:
        """Import a single community map JSON file (see data/fh6_maps/FORMAT.md)."""
        path = Path(file_path)
        if not path.exists():
            return f"File not found: {file_path}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return f"Could not parse {file_path} as JSON: {e}"

        landmarks = data.get("landmarks", [])
        if not landmarks:
            return f"No landmarks found in {file_path}."
        source = data.get("source", path.stem)
        for lm in landmarks:
            lm.setdefault("source", source)
        self._imported[source] = landmarks

        # Copy into data/fh6_maps/ if it isn't already there, so "reload"
        # picks it up on future runs without needing the original path again.
        COMMUNITY_MAPS_DIR.mkdir(parents=True, exist_ok=True)
        dest = COMMUNITY_MAPS_DIR / path.name
        if path.resolve() != dest.resolve():
            try:
                dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass

        return f"Imported {source}. {len(landmarks)} new landmarks loaded. Total landmarks now {self.total_count()}."

    def reload_all_sources(self) -> str:
        """Re-scan data/fh6_maps/ and reload the personal map from disk."""
        self._personal = self._load_personal()
        self._reload_imported()
        summary = self.import_summary()
        by_source = ", ".join(f"{k}({v})" for k, v in summary["by_source"].items())
        return f"Reloaded. {summary['total']} total landmarks from {len(summary['by_source'])} sources: {by_source}."

    def export_personal(self, name: str) -> str:
        """Export the user's own personal landmarks (not builtin/imported
        ones) to data/fh6_maps/{name}.json in the shareable community format."""
        if not self._personal:
            return "No personal locations to export yet -- mark some first."

        COMMUNITY_MAPS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-")) or "my_fh6_map"
        dest = COMMUNITY_MAPS_DIR / f"{safe_name}.json"

        payload = {
            "source": safe_name,
            "game": "fh6",
            "version": "1.0",
            "description": f"Personal FH6 map exported from ShinAgent ({len(self._personal)} landmarks)",
            "landmarks": [
                {
                    "name": lm["name"], "x": lm["x"], "z": lm["z"], "y": lm.get("y", 0),
                    "region": lm.get("region", ""), "type": lm.get("type", "custom"),
                    "tags": lm.get("tags", []), "notes": lm.get("notes", ""),
                    "callout": lm.get("callout", ""),
                }
                for lm in self._personal
            ],
        }
        dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return f"Exported {len(self._personal)} landmarks to data/fh6_maps/{safe_name}.json. Share that file with the community."

    # -- Combined view across all sources ----------------------------------

    def _all_landmarks(self) -> list:
        all_lm = list(BUILTIN_LANDMARKS) + list(self._personal)
        for landmarks in self._imported.values():
            all_lm.extend(landmarks)
        return all_lm

    def total_count(self) -> int:
        return len(self._all_landmarks())

    def import_summary(self) -> dict:
        all_lm = self._all_landmarks()
        by_source: dict[str, int] = {}
        regions = set()
        for lm in all_lm:
            src = lm.get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
            if lm.get("region"):
                regions.add(lm["region"])
        return {"total": len(all_lm), "by_source": by_source, "regions": sorted(regions)}

    # -- Mutation (personal only) -------------------------------------------

    def add_landmark(self, name: str, x: float, z: float, y: float = 0, notes: str = "") -> str:
        """Add or update a named PERSONAL landmark at the given coordinates."""
        for lm in self._personal:
            if lm["name"].lower() == name.lower():
                lm["x"] = x
                lm["z"] = z
                lm["y"] = y
                lm["updated"] = time.time()
                self._save_personal()
                return f"Updated location '{name}' at ({x:.0f}, {z:.0f})"

        self._personal.append({
            "name": name, "x": x, "z": z, "y": y, "notes": notes,
            "created": time.time(), "visits": 0, "source": "personal",
        })
        self._save_personal()
        return f"Location '{name}' saved. {len(self._personal)} personal locations in your map."

    def remove_landmark(self, name: str) -> str:
        """Removes from the PERSONAL map only -- builtin and imported
        landmarks aren't user-removable (re-import/reload to change those)."""
        before = len(self._personal)
        self._personal = [lm for lm in self._personal if lm["name"].lower() != name.lower()]
        if len(self._personal) < before:
            self._save_personal()
            return f"Removed '{name}' from your map."
        return f"Location '{name}' not found in your personal map."

    # -- Lookup / search ------------------------------------------------------

    def list_landmarks(self, source: str = "", region: str = "", ltype: str = "") -> list:
        """All landmarks across every source, optionally filtered. Returns
        a list of dicts (see tools/forza_openworld.py for the text-formatted
        voice-facing wrapper)."""
        results = self._all_landmarks()
        if source:
            results = [lm for lm in results if lm.get("source", "") == source]
        if region:
            results = [lm for lm in results if lm.get("region", "").lower() == region.lower()]
        if ltype:
            results = [lm for lm in results if lm.get("type", "") == ltype]
        return [{**_LANDMARK_DEFAULTS, **lm} for lm in results]

    def nearby(self, x: float, z: float, radius: float = 500.0) -> list:
        """All landmarks (any source) within radius of (x, z), sorted by distance."""
        results = []
        for lm in self._all_landmarks():
            dist = math.sqrt((x - lm["x"]) ** 2 + (z - lm["z"]) ** 2)
            if dist <= radius:
                results.append({**_LANDMARK_DEFAULTS, **lm, "distance": dist})
        results.sort(key=lambda lm: lm["distance"])
        return results

    def nearest(self, x: float, z: float) -> Optional[dict]:
        """Return the single nearest landmark (any source) plus its
        distance, or None if there are no landmarks at all."""
        all_lm = self._all_landmarks()
        if not all_lm:
            return None
        nearest, min_dist = None, float("inf")
        for lm in all_lm:
            dist = math.sqrt((x - lm["x"]) ** 2 + (z - lm["z"]) ** 2)
            if dist < min_dist:
                min_dist, nearest = dist, {**_LANDMARK_DEFAULTS, **lm, "distance": dist}
        return nearest

    def get_landmark(self, name: str) -> Optional[dict]:
        """Find one landmark by exact (case-insensitive) name, any source."""
        name_lower = name.strip().lower()
        for lm in self._all_landmarks():
            if lm["name"].lower() == name_lower:
                return {**_LANDMARK_DEFAULTS, **lm}
        return None

    def check_location(self, x: float, z: float, speed_kmh: float) -> Optional[str]:
        """
        Returns an announcement the first time we enter a known landmark's
        radius at speed, or None otherwise (including while sitting inside
        one below MIN_SPEED_KMH, or once already announced this visit).
        Uses the landmark's own "callout" text if set, otherwise falls back
        to a generic visit-count-based line. Personal landmarks' visit
        counts are tracked; builtin/imported ones are not (there's no
        per-user file to persist them into).
        """
        if speed_kmh < MIN_SPEED_KMH:
            return None

        nearest = self.nearest(x, z)
        if not nearest:
            return None

        dist, name = nearest["distance"], nearest["name"]
        radius = nearest.get("radius") or RECOGNITION_RADIUS

        if dist <= radius:
            if self._last_near != name:
                self._last_near = name
                self._near_since = time.time()

                visits = 0
                if nearest.get("source") == "personal":
                    for lm in self._personal:
                        if lm["name"] == name:
                            lm["visits"] = lm.get("visits", 0) + 1
                            visits = lm["visits"]
                    self._save_personal()

                if nearest.get("callout"):
                    return nearest["callout"]
                if visits <= 1:
                    return f"That's {name}."
                elif visits < 5:
                    return f"Back at {name}."
                else:
                    return f"{name} again."
        elif self._last_near == name:
            self._last_near = None
            self._near_since = None

        return None

    def get_nearby_description(self, x: float, z: float) -> str:
        """Used when the player asks 'where are we?'."""
        nearest = self.nearest(x, z)
        if not nearest:
            return "Unknown area -- no locations mapped nearby."

        dist, name = nearest["distance"], nearest["name"]
        radius = nearest.get("radius") or RECOGNITION_RADIUS
        if dist < radius:
            return f"We're at {name}."
        elif dist < radius * 3:
            return f"We're near {name} ({dist:.0f}m away)."
        elif dist < radius * 10:
            return f"Closest known location is {name} ({dist / 1000:.1f}km away)."
        else:
            return "We're in unmapped territory."


# Singleton
_location_system: Optional[ForzaLocationSystem] = None


def get_location_system() -> ForzaLocationSystem:
    global _location_system
    if _location_system is None:
        _location_system = ForzaLocationSystem()
    return _location_system
