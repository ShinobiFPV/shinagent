"""
IMQ2 Config Loader
Loads config.yaml and personality profiles. All modules import from here.
"""

import yaml
import os
from pathlib import Path
from typing import Any

# Resolve project root regardless of where the script is called from
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
PROFILES_DIR = PROJECT_ROOT / "personality" / "profiles"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class Config:
    """Singleton config object. Access via config.get('section.key')."""

    _instance = None
    _data: dict = {}
    _profile: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        with open(CONFIG_PATH, "r") as f:
            self._data = yaml.safe_load(f)

        profile_path = PROFILES_DIR / Path(self._data["agent"]["active_profile"]).name
        if profile_path.exists():
            with open(profile_path, "r") as f:
                self._profile = yaml.safe_load(f)

    def reload(self):
        """Hot-reload config and active profile at runtime."""
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

    def load_profile(self, profile_name: str) -> dict:
        """Load a named personality profile and set it as active."""
        path = PROFILES_DIR / f"{profile_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Profile '{profile_name}' not found at {path}")
        with open(path, "r") as f:
            self._profile = yaml.safe_load(f)
        self._data["agent"]["active_profile"] = f"profiles/{profile_name}.yaml"
        return self._profile

    @property
    def profile(self) -> dict:
        return self._profile

    @property
    def raw(self) -> dict:
        return self._data

    def save(self):
        """Write the current config back to config.yaml."""
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False, allow_unicode=True)

    def list_profiles(self) -> list[str]:
        return [p.stem for p in PROFILES_DIR.glob("*.yaml")]


# Module-level singleton — import this everywhere
config = Config()
