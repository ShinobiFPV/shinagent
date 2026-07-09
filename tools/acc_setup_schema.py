"""
IMQ2 ACC Setup Schema
Reference structure for Assetto Corsa Competizione setup JSON files, plus
conservative SAFE_DEFAULTS used as the base for every generated setup and
as the fallback for any field that fails validation.

SETUP_SCHEMA is documentation, not a runtime validator — it exists so a
human (or the LLM prompt in tools/acc_setup_generator.py) can see the
expected shape at a glance. The actual enforcement is fill_and_validate(),
which walks SAFE_DEFAULTS and keeps the generated value only where it's
present and shaped correctly, falling back to the safe default otherwise.
"""

# Reference shape only (not evaluated for validation) — [int]*4 reads as
# "four ints: FL, FR, RL, RR" the same way ACC's own setup UI is laid out.
SETUP_SCHEMA = {
    "carName": str,
    "basicSetup": {
        "tyres": {
            "tyreCompound": int,       # 0=dry, 1=wet
            "tyrePressure": [int] * 4,  # FL,FR,RL,RR raw units (~49-65)
        },
        "alignment": {
            "camber": [int] * 4,      # FL,FR,RL,RR index (0=most negative)
            "toe": [int] * 4,         # FL,FR,RL,RR index
            "casterLF": int,          # index
            "casterRF": int,          # index
            "steerRatio": int,        # index
        },
        "electronics": {
            "tC1": int,                # TC main 0-11
            "tC2": int,                # TC cut 0-11
            "abs": int,                # ABS 0-11
            "eCUMap": int,             # Engine map 0-5
            "fuelMix": int,            # 0=lean, 1=auto, 2=rich
            "telemetryLaps": int,      # 0-240, doesn't affect handling
        },
        "strategy": {
            "fuel": int,               # litres 1-120
            "nPitStops": int,          # 0-3
            "tyreSet": int,            # 0-49
            "frontBrakePadCompound": int,  # 0-3
            "rearBrakePadCompound": int,
            "pitStrategy": [
                {
                    "fuelToAdd": int,
                    "tyres": {"tyreCompound": int, "tyrePressure": [int] * 4},
                    "tyreSet": int,
                    "frontBrakePadCompound": int,
                    "rearBrakePadCompound": int,
                }
            ],
        },
    },
    "advancedSetup": {
        "mechanicalBalance": {
            "aRBFront": int,           # 0-10
            "aRBRear": int,            # 0-10
            "wheelRate": [int] * 4,    # spring rate FL,FR,RL,RR (N/m)
            "bumpStopRateUp": [int] * 4,
            "bumpStopRateDn": [int] * 4,
            "arbSplitFront": int,
            "arbSplitRear": int,
            "bumpStopWindow": [int] * 4,
            "rodLength": [float] * 4,
            "singleJounce": [float] * 4,
            "wheelBase": [float] * 4,
        },
        "dampers": {
            "bumpSlow": [int] * 4,     # FL,FR,RL,RR
            "bumpFast": [int] * 4,
            "reboundSlow": [int] * 4,
            "reboundFast": [int] * 4,
        },
        "aeroBalance": {
            "rideHeight": [int, int],  # front, rear in mm
            "splitter": int,           # 0-10 (car dependent)
            "rearWing": int,           # 0-10 (car dependent)
            "brakeDuct": [int, int],   # front, rear 0-6
        },
        "drivetrain": {
            "preload": int,            # diff preload 0-100
        },
    },
}

# Conservative values valid for any GT3 car — the base every generated
# setup starts from, and the fallback for any field that doesn't validate.
SAFE_DEFAULTS = {
    "basicSetup": {
        "tyres": {"tyreCompound": 0, "tyrePressure": [57, 57, 54, 54]},
        "alignment": {
            "camber": [0, 0, 0, 0], "toe": [0, 0, 32, 32],
            "casterLF": 20, "casterRF": 20, "steerRatio": 11,
        },
        "electronics": {
            "tC1": 5, "tC2": 3, "abs": 5, "eCUMap": 0,
            "fuelMix": 0, "telemetryLaps": 0,
        },
        "strategy": {
            "fuel": 65, "nPitStops": 0, "tyreSet": 0,
            "frontBrakePadCompound": 1, "rearBrakePadCompound": 1,
            "pitStrategy": [],
        },
    },
    "advancedSetup": {
        "mechanicalBalance": {
            "aRBFront": 5, "aRBRear": 4,
            "wheelRate": [210000, 210000, 185000, 185000],
            "bumpStopRateUp": [300, 300, 300, 300],
            "bumpStopRateDn": [300, 300, 300, 300],
            "arbSplitFront": 0, "arbSplitRear": 0,
            "bumpStopWindow": [12, 12, 12, 12],
            "rodLength": [0.0] * 4, "singleJounce": [0.0] * 4, "wheelBase": [0.0] * 4,
        },
        "dampers": {
            "bumpSlow": [3, 3, 3, 3], "bumpFast": [2, 2, 2, 2],
            "reboundSlow": [5, 5, 5, 5], "reboundFast": [4, 4, 4, 4],
        },
        "aeroBalance": {
            "rideHeight": [58, 66], "splitter": 2,
            "rearWing": 4, "brakeDuct": [3, 3],
        },
        "drivetrain": {"preload": 28},
    },
}

# Extra range/enum checks beyond "right shape" — applied after the
# structural fill below. Keyed by dotted path from the setup root.
_RANGE_CHECKS = {
    "basicSetup.tyres.tyreCompound": lambda v: v in (0, 1),
    "basicSetup.strategy.fuel": lambda v: isinstance(v, (int, float)) and 1 <= v <= 120,
}


def _is_valid_leaf(value, default) -> bool:
    if isinstance(default, list):
        if not isinstance(value, list) or len(value) != len(default):
            return False
        # Every element must be numeric if the default's elements are numeric.
        if default and isinstance(default[0], (int, float)):
            return all(isinstance(v, (int, float)) for v in value)
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float))
    if isinstance(default, str):
        return isinstance(value, str)
    return True


def _fill(node, defaults, path: str) -> dict:
    result = {}
    for key, default_val in defaults.items():
        node_val = node.get(key) if isinstance(node, dict) else None
        key_path = f"{path}.{key}" if path else key
        if isinstance(default_val, dict):
            result[key] = _fill(node_val or {}, default_val, key_path)
        elif key == "pitStrategy":
            # Variable-length list of per-stop dicts — pass through if it's
            # a list, otherwise fall back to the (empty) default.
            result[key] = node_val if isinstance(node_val, list) else default_val
        else:
            if _is_valid_leaf(node_val, default_val):
                value = node_val
            else:
                value = default_val
            check = _RANGE_CHECKS.get(key_path)
            if check and not check(value):
                value = default_val
            result[key] = value
    return result


def fill_and_validate(setup: dict) -> dict:
    """
    Merge a generated setup dict against SAFE_DEFAULTS: any field that's
    missing, wrong-shaped, or out of range is replaced by its safe default.
    Everything else (car-specific tuning the LLM actually produced) passes
    through unchanged. Returns a new dict — never mutates the input.
    """
    setup = setup or {}
    result = _fill(setup, SAFE_DEFAULTS, "")
    result["carName"] = setup.get("carName") or ""
    return result
