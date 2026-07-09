"""
IMQ2 Config Loader
Loads config.yaml and personality profiles. All modules import from here.

Personality persistence model:
  - personality/profiles/*.yaml are pure TEMPLATES — a profile's authored
    persona, dial_preset, and default dial_overrides. The settings UI never
    writes to these files.
  - config/personality_state.yaml is the single source of truth for what
    Q2 is CURRENTLY running with — which profile, and its current resolved
    dial_overrides. Written every time the active profile or its dials
    change; read on every startup/reload.
  - config/profile_state_<name>.yaml remembers each profile's own
    last-used dial state independently, so switching between profiles
    restores each one's customizations rather than reverting to the
    template defaults every time.

  config/vernacular_state.yaml (speech style — nicknames, sentence enders,
  slang/profanity level, hardcoded traits) is a SEPARATE, independently
  managed file — see personality/vernacular.py. It is deliberately NOT
  touched by save_personality_state()/load_personality_state() below:
  vernacular defines HOW Q2 speaks and persists across every profile/preset
  switch untouched, while this file's state defines WHO Q2 is per-profile.
"""

import datetime
import yaml
import os
from pathlib import Path
from typing import Any, Optional

# Resolve project root regardless of where the script is called from
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
PROFILES_DIR = PROJECT_ROOT / "personality" / "profiles"
PERSONALITY_STATE_PATH = PROJECT_ROOT / "config" / "personality_state.yaml"

# Keys that live inside a profile's dial_overrides dict but aren't 0-100
# numeric dials — kept alongside the numeric ones in dial_overrides at
# runtime (that's what personality/dials.py's PersonalityDials.from_dict
# expects), but broken out as separate top-level keys in the on-disk state
# files to match the hand-readable shape.
_NON_NUMERIC_DIAL_KEYS = ("probability_narration", "wellness_checkins")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _profile_state_path(profile_name: str) -> Path:
    return PROJECT_ROOT / "config" / f"profile_state_{profile_name}.yaml"


class Config:
    """Singleton config object. Access via config.get('section.key')."""

    _instance = None
    _data: dict = {}
    _profile: dict = {}
    _profile_path: Path = None
    _active_profile_name: str = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

        profile_name = Path(self._data.get("agent", {}).get("active_profile", "profiles/q2_default.yaml")).stem
        try:
            self._activate_profile(profile_name, prefer_personality_state=True)
        except FileNotFoundError:
            # Matches the previous behaviour: a missing profile file must
            # never crash startup — just leave _profile empty.
            self._profile = {}
            self._profile_path = None
            self._active_profile_name = None

    def reload(self):
        """
        Hot-reload config and active profile at runtime — re-reads
        config.yaml (in case another process changed agent.active_profile)
        and config/personality_state.yaml (in case another process changed
        dial values), without restarting this process. This is what lets a
        settings change made in the main process reach a separate process
        (e.g. the webapp subprocess) via IMQ2Agent.reload_personality().
        """
        self._load()

    def get(self, dotpath: str, default: Any = None) -> Any:
        """Get a config value by dot-separated path. e.g. config.get('llm.claude.model')"""
        keys = dotpath.split(".")
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    # ------------------------------------------------------------------
    # Profile activation — shared by startup (_load) and explicit switches
    # (load_profile), differing only in whether personality_state.yaml is
    # consulted first.
    # ------------------------------------------------------------------

    def _read_profile_template(self, profile_name: str) -> dict:
        path = PROFILES_DIR / f"{profile_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Profile '{profile_name}' not found at {path}")
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _extract_overrides(self, state: dict) -> dict:
        """A state dict (personality_state.yaml or profile_state_*.yaml's
        shape) -> the flat dial_overrides mapping build_system_prompt()'s
        _resolve_profile_dials() expects (numeric dials + the two
        non-numeric switches all together)."""
        overrides = dict(state.get("dial_overrides", {}) or {})
        for key in _NON_NUMERIC_DIAL_KEYS:
            if key in state:
                overrides[key] = state[key]
        return overrides

    def _activate_profile(self, profile_name: str, prefer_personality_state: bool) -> dict:
        template = self._read_profile_template(profile_name)
        profile = dict(template)
        profile_path = f"profiles/{profile_name}.yaml"

        resolved_overrides = None
        if prefer_personality_state:
            pstate = self.load_personality_state()
            if pstate and pstate.get("active_profile") == profile_path:
                resolved_overrides = self._extract_overrides(pstate)

        if resolved_overrides is None:
            pfstate = self.load_profile_state(profile_name)
            if pfstate:
                resolved_overrides = self._extract_overrides(pfstate)

        if resolved_overrides is not None:
            profile["dial_overrides"] = resolved_overrides
        # else: keep the template's own authored dial_overrides — true
        # first run for this profile, nothing saved yet.

        self._profile = profile
        self._profile_path = PROFILES_DIR / f"{profile_name}.yaml"
        self._active_profile_name = profile_name
        self._data.setdefault("agent", {})["active_profile"] = profile_path

        self.save_personality_state()
        return self._profile

    def load_profile(self, profile_name: str) -> dict:
        """
        Hot-swap Q2's active personality profile. Preserves the OUTGOING
        profile's current dial state to config/profile_state_<name>.yaml
        first, so switching back later restores it, then activates the new
        profile using ITS OWN last-remembered state if one exists, else its
        template defaults. Never mutates the template YAML itself.
        """
        if self._active_profile_name and self._active_profile_name != profile_name:
            self._write_profile_state_file(self._active_profile_name, self._build_state_dict())
        return self._activate_profile(profile_name, prefer_personality_state=False)

    @property
    def profile(self) -> dict:
        return self._profile

    @property
    def raw(self) -> dict:
        return self._data

    def save(self):
        """Write the current config back to config.yaml."""
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, default_flow_style=False, allow_unicode=True)

    def list_profiles(self) -> list[str]:
        return [p.stem for p in PROFILES_DIR.glob("*.yaml")]

    # ------------------------------------------------------------------
    # Personality state persistence (config/personality_state.yaml +
    # config/profile_state_<name>.yaml) — see module docstring.
    # ------------------------------------------------------------------

    def _build_state_dict(self) -> dict:
        overrides = dict(self._profile.get("dial_overrides", {}) or {})
        numeric_overrides = {k: v for k, v in overrides.items() if k not in _NON_NUMERIC_DIAL_KEYS}
        return {
            "active_profile": self._data.get("agent", {}).get("active_profile", ""),
            "dial_overrides": numeric_overrides,
            "probability_narration": overrides.get("probability_narration", False),
            "wellness_checkins": overrides.get("wellness_checkins", "off"),
            "saved_at": datetime.datetime.now().isoformat(),
        }

    def save_personality_state(self, dials: dict = None, extras: dict = None):
        """
        Write the CURRENT active profile + resolved dial state to
        config/personality_state.yaml — the single source of truth for
        what Q2 is actually running with. Also keeps this profile's own
        config/profile_state_<name>.yaml in sync, so a later switch away
        and back restores it. If dials/extras are passed, they're applied
        to self._profile's dial_overrides first (the settings-panel save
        path); otherwise this just persists whatever's already in memory.

        Note: this never touches config/vernacular_state.yaml — that's a
        separate file managed independently by personality/vernacular.py's
        own load_vernacular()/save_vernacular(), since speech style (HOW Q2
        talks) is deliberately decoupled from personality state (WHO Q2 is
        per-profile) and must survive profile switches untouched.
        """
        if dials is not None:
            overrides = self._profile.setdefault("dial_overrides", {})
            overrides.update(dials)
        if extras:
            overrides = self._profile.setdefault("dial_overrides", {})
            overrides.update(extras)

        state = self._build_state_dict()

        # tts_voice is a GLOBAL preference, independent of the active
        # profile — recorded only in the top-level personality_state.yaml,
        # deliberately not passed into _write_profile_state_file() below.
        # config.yaml itself is still the single source of truth for the
        # live voice (see voice/pipeline.py's get_tts(), which always reads
        # it fresh); this is purely a recovery copy so the last-known voice
        # survives even a config.yaml that's missing or been reset — it's
        # never allowed to override a value config.yaml already has (see
        # load_personality_state()). Duplicating it into each profile's own
        # profile_state_<name>.yaml would be wrong: switching profiles would
        # then silently revert your voice to whatever it was last time that
        # profile's dials happened to be saved.
        global_state = dict(state)
        global_state["tts_voice"] = self.get("voice.deepgram_tts.model", "aura-2-zeus-en")
        with open(PERSONALITY_STATE_PATH, "w", encoding="utf-8") as f:
            yaml.dump(global_state, f, default_flow_style=False, allow_unicode=True)

        if self._active_profile_name:
            self._write_profile_state_file(self._active_profile_name, state)

    def load_personality_state(self) -> Optional[dict]:
        """
        Load saved personality state. Returns None if not found. Also
        restores tts_voice from it into the live config as a fallback —
        but ONLY when config.yaml itself doesn't already have a
        voice.deepgram_tts.model value, so a legitimate current config.yaml
        setting is never clobbered by an older backup.
        """
        if not PERSONALITY_STATE_PATH.exists():
            return None
        with open(PERSONALITY_STATE_PATH, "r", encoding="utf-8") as f:
            state = yaml.safe_load(f)
        if state and state.get("tts_voice") and not self.get("voice.deepgram_tts.model"):
            self._data.setdefault("voice", {}).setdefault("deepgram_tts", {})["model"] = state["tts_voice"]
        return state

    def _write_profile_state_file(self, profile_name: str, state: dict):
        # active_profile is redundant here — implied by the filename.
        to_write = {k: v for k, v in state.items() if k != "active_profile"}
        with open(_profile_state_path(profile_name), "w", encoding="utf-8") as f:
            yaml.dump(to_write, f, default_flow_style=False, allow_unicode=True)

    def load_profile_state(self, profile_name: str) -> Optional[dict]:
        path = _profile_state_path(profile_name)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def reset_profile_to_defaults(self, profile_name: str = None) -> dict:
        """
        Discard a profile's saved customizations (deletes its
        profile_state_<name>.yaml) and reload its originally-authored dial
        defaults from the template YAML. Defaults to the active profile.
        """
        profile_name = profile_name or self._active_profile_name
        if not profile_name:
            return self._profile

        state_path = _profile_state_path(profile_name)
        if state_path.exists():
            state_path.unlink()

        if profile_name == self._active_profile_name:
            template = self._read_profile_template(profile_name)
            self._profile["dial_overrides"] = dict(template.get("dial_overrides", {}))
            self.save_personality_state()
        return self._profile


# Module-level singleton — import this everywhere
config = Config()
