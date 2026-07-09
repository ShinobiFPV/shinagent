"""
IMQ2 Game Companion Mode
========================
Manages the active game session (current game, genre, build/progress
notes, spoiler preference) and cross-session history. Pure state -- tool
logic lives in tools/game_companion.py, matching the integrations/ vs
tools/ split used by every other mode (masterchef, radio_dj, whiplash,
beavis_butthead).
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SESSION_DIR = Path(__file__).parent.parent / "cache" / "game_companion"
HISTORY_FILE = SESSION_DIR / "history.json"

# ── Genre definitions ─────────────────────────────────────

GENRES = {
    "rpg": {
        "name":        "RPG",
        "description": "Role-playing games",
        "q2_focus":    "builds, boss strategies, quest progression, lore",
    },
    "action_rpg": {
        "name":        "Action RPG",
        "description": "Action RPGs (Souls, Diablo, etc.)",
        "q2_focus":    "boss strategies, build optimization, gear farming",
    },
    "strategy": {
        "name":        "Strategy",
        "description": "Strategy games (4X, RTS, TBS)",
        "q2_focus":    "build orders, economy, counter-strategies",
    },
    "survival": {
        "name":        "Survival",
        "description": "Survival and crafting games",
        "q2_focus":    "crafting progression, base building, resource priorities",
    },
    "shooter": {
        "name":        "Shooter",
        "description": "FPS, TPS, battle royale",
        "q2_focus":    "loadout optimization, current meta, map callouts",
    },
    "puzzle": {
        "name":        "Puzzle/Adventure",
        "description": "Puzzle and adventure games",
        "q2_focus":    "graduated hints, secrets, collectibles",
    },
    "sports": {
        "name":        "Sports/Racing",
        "description": "Sports and racing games",
        "q2_focus":    "meta strategies, setups, skill development",
    },
    "other": {
        "name":        "Other",
        "description": "Any other game type",
        "q2_focus":    "general game assistance",
    },
}

# ── Known games database ──────────────────────────────────
# Pre-populated with popular games -- Q2 uses web search for anything not
# in this list, and for current-patch specifics regardless (this data is
# static identity/system info, not meta -- meta goes stale, this doesn't).

KNOWN_GAMES = {
    "elden ring": {
        "genre":       "action_rpg",
        "developer":   "FromSoftware",
        "year":        2022,
        "description": "Open world action RPG. Extremely challenging.",
        "key_systems": ["Rune leveling", "Ashes of War",
                        "Flask of Crimson/Cerulean Tears",
                        "Spirit Ashes summons",
                        "Sites of Grace fast travel"],
        "beginner_tips": [
            "Summon Spirit Ashes for tough bosses",
            "Explore Limgrave fully before moving on",
            "Don't fight Margit until level 25-30",
            "Torrent the horse is always available -- use it",
            "Golden Seeds increase flask charges",
        ],
        "wiki": "eldenring.wiki.fextralife.com",
    },
    "baldur's gate 3": {
        "genre":       "rpg",
        "developer":   "Larian Studios",
        "year":        2023,
        "description": "Turn-based RPG based on D&D 5e.",
        "key_systems": ["D&D 5e rules", "Advantage/Disadvantage",
                        "Long/Short Rest", "Camp supplies",
                        "Reactions", "Concentration spells"],
        "wiki": "bg3.wiki",
    },
    "valheim": {
        "genre":       "survival",
        "developer":   "Iron Gate",
        "year":        2021,
        "description": "Viking survival crafting game.",
        "key_systems": ["Biome progression", "Boss summoning",
                        "Forsaken powers", "Portal network",
                        "Skill leveling", "Food buffs"],
        "wiki": "valheim.fandom.com/wiki",
    },
    "cyberpunk 2077": {
        "genre":       "action_rpg",
        "developer":   "CD Projekt Red",
        "year":        2020,
        "description": "Open world action RPG in Night City.",
        "key_systems": ["Attribute system", "Perk trees",
                        "Cyberware", "Iconic weapons",
                        "Relic skill tree (Phantom Liberty)"],
        "wiki":        "cyberpunk.fandom.com/wiki",
    },
    "starfield": {
        "genre":       "rpg",
        "developer":   "Bethesda",
        "year":        2023,
        "description": "Space exploration RPG.",
        "key_systems": ["Background + Traits", "Skill trees",
                        "Ship building", "Outpost building",
                        "New Game+ Starborn powers"],
        "wiki":        "starfield.fandom.com/wiki",
    },
    "path of exile 2": {
        "genre":       "action_rpg",
        "developer":   "Grinding Gear Games",
        "year":        2024,
        "description": "Deep ARPG with extreme build complexity.",
        "key_systems": ["Passive skill tree", "Ascendancy classes",
                        "Gem socketing", "Atlas endgame",
                        "Currency crafting"],
        "wiki":        "www.poewiki.net",
    },
    "civilization vi": {
        "genre":       "strategy",
        "developer":   "Firaxis",
        "year":        2016,
        "description": "Turn-based 4X strategy game.",
        "key_systems": ["District system", "Eureka moments",
                        "City placement", "Religion",
                        "Governors", "Victory conditions"],
        "wiki":        "civilization.fandom.com/wiki",
    },
    "subnautica": {
        "genre":       "survival",
        "developer":   "Unknown Worlds",
        "year":        2018,
        "description": "Underwater alien planet survival game.",
        "key_systems": ["Depth progression", "Blueprint scanning",
                        "Base building", "Vehicle upgrades",
                        "Biome exploration order"],
        "wiki":        "subnautica.fandom.com/wiki",
    },
    "monster hunter wilds": {
        "genre":       "action_rpg",
        "developer":   "Capcom",
        "year":        2025,
        "description": "Co-op action RPG focused on hunting monsters.",
        "key_systems": ["Weapon types (14)", "Sharpness",
                        "Element/Status", "Armor skills",
                        "Palico/Seikret companions"],
        "wiki":        "monsterhunter.fandom.com/wiki",
    },
}


@dataclass
class GameSession:
    """Active game companion session. In-memory only, process lifetime --
    starting a new session for a different game replaces the old one
    (after archiving it to history), matching MasterChefSession/
    BBSession's "singleton, no mid-session persistence" pattern."""

    game_name:      str
    genre:          str   = "other"
    platform:       str   = ""
    started_at:     float = field(default_factory=time.time)

    progress_notes: list  = field(default_factory=list)
    current_area:   str   = ""
    character_info: str   = ""
    spoiler_level:  str   = "ask"  # ask/minimal/full

    stuck_on:       str   = ""
    tried:          list  = field(default_factory=list)

    game_data:      dict  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "game_name":      self.game_name,
            "genre":          self.genre,
            "platform":       self.platform,
            "started_at":     self.started_at,
            "progress_notes": self.progress_notes,
            "current_area":   self.current_area,
            "character_info": self.character_info,
            "spoiler_level":  self.spoiler_level,
            "stuck_on":       self.stuck_on,
            "tried":          self.tried,
        }

    def context_summary(self) -> str:
        """Context string injected into Q2's system prompt via the
        get_game_session tool -- makes every response aware of what game
        is active and where the user is."""
        lines = [
            f"ACTIVE GAME SESSION: {self.game_name}",
            f"Genre: {self.genre}",
        ]
        if self.platform:
            lines.append(f"Platform: {self.platform}")
        if self.character_info:
            lines.append(f"Character/Build: {self.character_info}")
        if self.current_area:
            lines.append(f"Current area: {self.current_area}")
        if self.progress_notes:
            lines.append("Progress notes:")
            for note in self.progress_notes[-5:]:
                lines.append(f"  - {note}")
        if self.stuck_on:
            lines.append(f"Currently stuck on: {self.stuck_on}")
        if self.tried:
            lines.append("Already tried:")
            for t in self.tried[-3:]:
                lines.append(f"  - {t}")
        if self.spoiler_level == "minimal":
            lines.append("SPOILER PREFERENCE: Minimal -- give hints not solutions, no story spoilers")
        elif self.spoiler_level == "full":
            lines.append("SPOILER PREFERENCE: Full OK -- user is fine with all spoilers")
        else:
            lines.append("SPOILER PREFERENCE: Not yet confirmed -- ask before revealing story/location content")
        return "\n".join(lines)


_session: Optional[GameSession] = None


def get_session() -> Optional[GameSession]:
    return _session


def set_session(s: GameSession):
    global _session
    _session = s


def clear_session():
    global _session
    _session = None


class GameHistory:
    """Persistent history of past game sessions -- stored as JSON in the
    gitignored cache/ directory (runtime-generated state, not a shipped
    asset -- same convention as beavis_butthead.py's BBHistory)."""

    def __init__(self):
        self._history = self._load()

    def _load(self) -> list:
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(self._history, indent=2), encoding="utf-8")

    def record(self, session: GameSession, max_entries: int = 20):
        self._history.insert(0, {**session.to_dict(), "ended_at": time.time()})
        self._history = self._history[:max_entries]
        self._save()

    def recent(self, limit: int = 20) -> list:
        return self._history[:limit]

    def find_last(self, game_name: str) -> Optional[dict]:
        name_lower = game_name.strip().lower()
        for entry in self._history:
            if entry.get("game_name", "").strip().lower() == name_lower:
                return entry
        return None


_history_store: Optional[GameHistory] = None


def get_history() -> GameHistory:
    global _history_store
    if _history_store is None:
        _history_store = GameHistory()
    return _history_store


def identify_game(name: str) -> dict:
    """Find game in known database or return a genre-guessed template.
    Case-insensitive, exact then substring match."""
    name_lower = name.lower().strip()

    if name_lower in KNOWN_GAMES:
        return KNOWN_GAMES[name_lower]

    for key, data in KNOWN_GAMES.items():
        if name_lower in key or key in name_lower:
            return data

    return {
        "genre":   _guess_genre(name_lower),
        "unknown": True,
    }


def _guess_genre(name: str) -> str:
    """Guess genre from game name keywords, for games not in KNOWN_GAMES."""
    name = name.lower()
    if any(w in name for w in
           ["souls", "elden", "diablo", "path of", "grim dawn"]):
        return "action_rpg"
    if any(w in name for w in
           ["civilization", "civ", "total war", "starcraft",
            "age of", "crusader kings"]):
        return "strategy"
    if any(w in name for w in
           ["minecraft", "valheim", "subnautica", "raft",
            "the forest", "sons of"]):
        return "survival"
    if any(w in name for w in
           ["warzone", "battlefield", "halo", "destiny",
            "apex", "fortnite", "call of duty"]):
        return "shooter"
    return "rpg"  # default
