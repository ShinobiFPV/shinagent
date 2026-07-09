"""
Circuit Builder Tools
======================
Q2 tools for designing circuits, generating wiring diagrams, writing
code, and managing projects.
"""

import json
import logging
import re
import uuid
from typing import Optional

from hud.circuit_builder.component_db import (
    COMPONENTS, get_component, search_components, list_by_category,
)
from hud.circuit_builder.circuit_model import (
    CircuitProject, ComponentInstance, Connection, list_projects,
)

log = logging.getLogger(__name__)


# -- Active session ----------------------------------------------------

_active_project: Optional[CircuitProject] = None


def get_active_project() -> Optional[CircuitProject]:
    return _active_project


def set_active_project(p: CircuitProject):
    global _active_project
    _active_project = p


# -- Formatting helpers --------------------------------------------------

def _format_voltage(voltage) -> str:
    """voltage may already be a descriptive string ("7-12V") or a bare
    number (5.0, 3.3) -- append a "V" unit only in the latter case, so a
    string like "7-12V" doesn't render as "7-12VV"."""
    if isinstance(voltage, str):
        return voltage
    return f"{voltage}V"


def _min_voltage(voltage) -> float:
    """Lowest voltage a component will actually run at, used for
    compatibility checks. voltage may be a bare number, a range string
    ("3.3-5V"), a single-value string ("4.8-6V (5V typical)"), or "any".
    Pulls the first number out of a string rather than string-matching
    for "5V" -- a component whose range starts at 3.3 (e.g. "3.3-5V")
    genuinely works on a 3.3V board and should never be flagged, which a
    naive "'5V' in voltage_string" substring check gets wrong for exactly
    that common case."""
    if isinstance(voltage, (int, float)):
        return float(voltage)
    if isinstance(voltage, str):
        if voltage.strip().lower() == "any":
            return 0.0
        match = re.search(r"\d+\.?\d*", voltage)
        if match:
            return float(match.group())
    return 0.0


# -- Tool functions ------------------------------------------------------

def search_components_tool(query: str, category: str = "") -> str:
    """Search the component database by name or description.
    category: board/sensor/actuator/passive/display/power/communication"""
    results = search_components(query, category or None)

    if not results:
        return (f"No components found for '{query}'. Available categories: board, sensor, "
                f"actuator, passive, display, power, communication")

    lines = [f"Found {len(results)} component(s):"]
    for c in results[:10]:
        lines.append(f"  [{c['id']}] {c['name']} -- {c['description']}")
        if c.get("notes"):
            lines.append(f"    * {c['notes'][0]}")

    return "\n".join(lines)


def get_component_detail(component_id: str) -> str:
    """Get full details about a specific component."""
    comp = get_component(component_id)
    if not comp:
        ids = [c["id"] for c in search_components(component_id)]
        if ids:
            return f"Component '{component_id}' not found. Did you mean: {', '.join(ids[:3])}?"
        return f"Component '{component_id}' not found."

    lines = [
        f"=== {comp['name']} ===",
        f"ID: {comp['id']}",
        f"Category: {comp['category']}",
        f"Description: {comp['description']}",
        f"Voltage: {_format_voltage(comp['voltage']) if 'voltage' in comp else 'N/A'}",
        f"Interface: {comp.get('interface', 'N/A')}",
    ]

    if comp.get("i2c_address"):
        lines.append(f"I2C Address: {comp['i2c_address']}")

    lines.append("\nPins:")
    for pin in comp.get("pins", []):
        v = f" ({_format_voltage(pin['voltage'])})" if "voltage" in pin else ""
        lines.append(f"  {pin['id']:8} {pin['name']}{v} -- {pin['type']}")

    if comp.get("notes"):
        lines.append("\nNotes:")
        for note in comp["notes"]:
            lines.append(f"  * {note}")

    if comp.get("libraries"):
        lines.append(f"\nLibraries: {', '.join(comp['libraries'])}")

    return "\n".join(lines)


def _enabled() -> bool:
    from config.loader import config
    return config.get("circuit_builder.enabled", True)


def design_circuit(
    project_description: str,
    board_id: str = "",
    components_have: list = None,
    language: str = "arduino_cpp",
) -> str:
    """Entry point for a new circuit design. Returns component-database
    context (available boards/sensors/actuators, and the chosen board's
    pinout if given) for Q2 to use while generating a complete circuit
    descriptor in its own next response -- this function does not itself
    call an LLM or save anything; create_project_from_json() is the tool
    that actually persists the circuit Q2 generates from this context."""
    if not _enabled():
        return "[design_circuit] Circuit Builder mode is disabled in config.yaml (circuit_builder.enabled)."

    comp_have = components_have or []

    board = get_component(board_id) if board_id else None
    board_context = ""
    if board:
        board_context = (
            f"\nSelected board: {board['name']} ({_format_voltage(board.get('voltage', 'N/A'))} logic)\n"
            f"Board pins: " + ", ".join(p["id"] for p in board.get("pins", [])[:10])
        )

    have_context = f"\nUser already has: {', '.join(comp_have)}" if comp_have else ""

    return (
        f"Project: {project_description}"
        f"{board_context}"
        f"{have_context}\n"
        f"Language: {language}\n"
        f"\nAvailable boards: " + ", ".join(c["id"] for c in list_by_category("board"))
        + f"\nAvailable sensors: " + ", ".join(c["id"] for c in list_by_category("sensor"))
        + f"\nAvailable actuators: " + ", ".join(c["id"] for c in list_by_category("actuator"))
    )


def create_project_from_json(circuit_json: str) -> str:
    """Parse circuit descriptor JSON from Q2 and save as a project.

    Expected JSON format:
    {
      "title": "Smart Door Sensor",
      "description": "ESP32 + PIR + LED notification system",
      "components": [
        {"instance_id": "U1", "component_id": "esp32_devkit", "label": "ESP32 DevKit",
         "x": 0.3, "y": 0.5, "notes": "Main controller"},
        {"instance_id": "PIR1", "component_id": "pir_motion", "label": "PIR Sensor",
         "x": 0.7, "y": 0.3}
      ],
      "connections": [
        {"from_instance": "U1", "from_pin": "GPIO4", "to_instance": "PIR1", "to_pin": "OUT",
         "wire_color": "yellow", "note": "Motion signal"},
        {"from_instance": "U1", "from_pin": "3V3", "to_instance": "PIR1", "to_pin": "VCC",
         "wire_color": "red"},
        {"from_instance": "U1", "from_pin": "GND", "to_instance": "PIR1", "to_pin": "GND",
         "wire_color": "black"}
      ],
      "code": "// Arduino code here...",
      "code_language": "arduino_cpp",
      "warnings": ["PIR needs 30 second warmup"],
      "build_steps": ["Connect PIR VCC to ESP32 3.3V", "Connect PIR GND to ESP32 GND",
                       "Connect PIR OUT to GPIO4"],
      "bom": [{"qty": 1, "part": "ESP32 DevKit V1", "notes": "Any 38-pin ESP32"},
              {"qty": 1, "part": "HC-SR501 PIR sensor"}],
      "libraries": ["No libraries needed -- PIR is digital"]
    }
    """
    try:
        clean = circuit_json.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1])

        data = json.loads(clean)
    except json.JSONDecodeError as e:
        return f"JSON parse error: {e}"

    project_id = str(uuid.uuid4())[:8]

    proj = CircuitProject(
        project_id=project_id,
        title=data.get("title", "Untitled Project"),
        description=data.get("description", ""),
        code=data.get("code", ""),
        code_language=data.get("code_language", "arduino_cpp"),
        warnings=data.get("warnings", []),
        build_steps=data.get("build_steps", []),
        bom=data.get("bom", []),
        libraries=data.get("libraries", []),
        power_notes=data.get("power_notes", []),
    )

    try:
        for c in data.get("components", []):
            proj.components.append(ComponentInstance(
                instance_id=c["instance_id"],
                component_id=c["component_id"],
                label=c.get("label", c["component_id"]),
                x=c.get("x", 0.5),
                y=c.get("y", 0.5),
                notes=c.get("notes", ""),
            ))

        for w in data.get("connections", []):
            proj.connections.append(Connection(
                from_instance=w["from_instance"],
                from_pin=w["from_pin"],
                to_instance=w["to_instance"],
                to_pin=w["to_pin"],
                wire_color=w.get("wire_color", "auto"),
                label=w.get("label", ""),
                note=w.get("note", ""),
            ))
    except KeyError as e:
        return f"Circuit JSON is missing a required field: {e}"

    proj.save()
    set_active_project(proj)

    return (f"Project '{proj.title}' created. ID: {project_id}. "
            f"{len(proj.components)} components, {len(proj.connections)} connections. "
            f"Diagram ready in HUD Circuit Builder tab.")


def get_project_code(project_id: str = "") -> str:
    """Get the code for a project."""
    proj = CircuitProject.load(project_id) if project_id else _active_project

    if not proj:
        return "No active project."
    if not proj.code:
        return "No code generated for this project yet."

    return f"// {proj.title}\n// {proj.description}\n\n{proj.code}"


def list_projects_tool() -> str:
    """List all saved circuit projects."""
    projects = list_projects()

    if not projects:
        return "No saved projects. Describe a project to me and I'll design it."

    lines = [f"Saved projects ({len(projects)}):"]
    for p in projects:
        lines.append(f"  [{p['project_id']}] {p['title']} -- {p['component_count']} components")
        if p["description"]:
            lines.append(f"    {p['description']}")

    return "\n".join(lines)


def load_project(project_id: str) -> str:
    """Load a saved project as the active project."""
    proj = CircuitProject.load(project_id)
    if not proj:
        return f"Project '{project_id}' not found."

    set_active_project(proj)
    return (f"Loaded '{proj.title}'. {len(proj.components)} components, "
            f"{len(proj.connections)} connections. Diagram visible in HUD Circuit Builder tab.")


def get_bom(project_id: str = "") -> str:
    """Get bill of materials for a project."""
    proj = CircuitProject.load(project_id) if project_id else _active_project
    if not proj:
        return "No active project."
    if not proj.bom:
        return "No bill of materials generated."

    lines = [f"Bill of Materials -- {proj.title}:", ""]
    for item in proj.bom:
        qty = item.get("qty", 1)
        part = item.get("part", "")
        notes = item.get("notes", "")
        line = f"  {qty}x  {part}"
        if notes:
            line += f"  ({notes})"
        lines.append(line)

    if proj.warnings:
        lines.append("\nWarnings:")
        for w in proj.warnings:
            lines.append(f"  * {w}")

    return "\n".join(lines)


def explain_build_steps(project_id: str = "") -> str:
    """Walk through the build steps for a project."""
    proj = CircuitProject.load(project_id) if project_id else _active_project
    if not proj:
        return "No active project."
    if not proj.build_steps:
        return "No build steps recorded for this project."

    lines = [f"Build steps -- {proj.title}:", ""]
    for i, step in enumerate(proj.build_steps, 1):
        lines.append(f"  {i:2}. {step}")

    if proj.warnings:
        lines.append("\nWarnings:")
        for w in proj.warnings:
            lines.append(f"     * {w}")

    if proj.libraries:
        lines.append("\nLibraries to install:")
        for lib in proj.libraries:
            lines.append(f"  - {lib}")

    return "\n".join(lines)


def check_compatibility(board_id: str, component_ids: list) -> str:
    """Check voltage compatibility between a board and components.
    Returns warnings about voltage mismatches, etc."""
    board = get_component(board_id)
    if not board:
        return f"Board '{board_id}' not found."

    board_voltage = _min_voltage(board.get("logic", board.get("voltage", 5.0)))
    lines = [f"Compatibility check: {board['name']}", ""]
    issues = []
    ok = []
    flagged_ids = set()

    for comp_id in component_ids:
        comp = get_component(comp_id)
        if not comp:
            lines.append(f"  [ ] {comp_id} -- not found in database")
            continue

        name = comp["name"]
        comp_min_voltage = _min_voltage(comp.get("voltage", 0.0))

        # A component needing at least comp_min_voltage genuinely can't
        # run on a lower-voltage board -- but 3.3-5V-range parts (min 3.3)
        # are perfectly fine on a 3.3V board, unlike a naive "contains 5V"
        # substring check would conclude.
        if comp_min_voltage > board_voltage:
            issues.append(f"  [!] {name} needs at least {_format_voltage(comp.get('voltage'))} -- "
                          f"{board['name']} is {_format_voltage(board.get('logic', board.get('voltage')))} logic. "
                          f"Use a level shifter or power it separately.")
            flagged_ids.add(comp_id)
        else:
            ok.append(f"  [x] {name}")

        # Component-specific gotchas from its own notes, only surfaced
        # once per component (not re-added if the voltage check above
        # already flagged it for the same underlying reason).
        if comp_id not in flagged_ids:
            for note in comp.get("notes", []):
                if "5v" in note.lower() and board_voltage < 5.0:
                    issues.append(f"  [!] {name}: {note}")
                    flagged_ids.add(comp_id)
                    break

    if ok:
        lines.append("Compatible:")
        lines.extend(ok)
    if issues:
        lines.append("\nPotential issues:")
        lines.extend(issues)
    if not issues:
        lines.append("\nNo compatibility issues found.")

    return "\n".join(lines)
