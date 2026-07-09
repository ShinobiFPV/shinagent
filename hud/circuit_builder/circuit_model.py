"""
Circuit Model
=============
Data model for a Q2-designed circuit.

Q2 generates a CircuitProject which contains:
  - Selected components with positions
  - Connections between pins
  - Code for the project
  - Bill of materials
  - Build instructions
"""

import json
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

PROJECTS_DIR = Path(__file__).parent.parent.parent / "cache" / "circuit_projects"


@dataclass
class ComponentInstance:
    """An instance of a component placed in a circuit."""
    instance_id: str    # unique ID in this circuit (e.g. "U1", "R1")
    component_id: str   # from COMPONENTS dict (e.g. "arduino_uno")
    label: str           # display label (e.g. "Arduino Uno")
    x: float = 0.0        # diagram position (0.0-1.0 normalized)
    y: float = 0.0
    notes: str = ""


@dataclass
class Connection:
    """A wire connection between two component pins."""
    from_instance: str
    from_pin: str
    to_instance: str
    to_pin: str
    wire_color: str = "auto"  # auto / red / black / yellow / green / blue / orange / white
    label: str = ""
    note: str = ""


@dataclass
class CircuitProject:
    """Complete circuit project with components, wiring, and code."""

    project_id: str
    title: str
    description: str
    created_at: float = field(default_factory=time.time)

    components: list = field(default_factory=list)   # ComponentInstance
    connections: list = field(default_factory=list)  # Connection

    code: str = ""
    code_language: str = "arduino_cpp"  # arduino_cpp / micropython / circuitpython

    power_notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    build_steps: list = field(default_factory=list)

    bom: list = field(default_factory=list)
    libraries: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "created_at": self.created_at,
            "components": [
                {
                    "instance_id": c.instance_id,
                    "component_id": c.component_id,
                    "label": c.label,
                    "x": c.x, "y": c.y,
                    "notes": c.notes,
                }
                for c in self.components
            ],
            "connections": [
                {
                    "from_instance": w.from_instance,
                    "from_pin": w.from_pin,
                    "to_instance": w.to_instance,
                    "to_pin": w.to_pin,
                    "wire_color": w.wire_color,
                    "label": w.label,
                    "note": w.note,
                }
                for w in self.connections
            ],
            "code": self.code,
            "code_language": self.code_language,
            "power_notes": self.power_notes,
            "warnings": self.warnings,
            "build_steps": self.build_steps,
            "bom": self.bom,
            "libraries": self.libraries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CircuitProject":
        proj = cls(
            project_id=data["project_id"],
            title=data["title"],
            description=data["description"],
            created_at=data.get("created_at", time.time()),
            code=data.get("code", ""),
            code_language=data.get("code_language", "arduino_cpp"),
            power_notes=data.get("power_notes", []),
            warnings=data.get("warnings", []),
            build_steps=data.get("build_steps", []),
            bom=data.get("bom", []),
            libraries=data.get("libraries", []),
        )
        for c in data.get("components", []):
            proj.components.append(ComponentInstance(**c))
        for w in data.get("connections", []):
            proj.connections.append(Connection(**w))
        return proj

    def save(self):
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        path = PROJECTS_DIR / f"{self.project_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, project_id: str) -> Optional["CircuitProject"]:
        # project_id comes from an HTTP path segment and is used to build a
        # filesystem path -- reject anything containing a path separator or
        # parent-dir reference so a request can't read/write outside
        # PROJECTS_DIR (e.g. "../../config/config.yaml").
        if not project_id or "/" in project_id or "\\" in project_id or ".." in project_id:
            return None
        path = PROJECTS_DIR / f"{project_id}.json"
        if path.exists():
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return None


def list_projects() -> list:
    """List all saved projects, most recently modified first."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for f in sorted(PROJECTS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            projects.append({
                "project_id": data["project_id"],
                "title": data["title"],
                "description": data["description"],
                "created_at": data.get("created_at", 0),
                "component_count": len(data.get("components", [])),
            })
        except Exception:
            pass
    return projects
