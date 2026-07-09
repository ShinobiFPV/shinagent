"""
Vernacular Generator
====================
Configurable speech style layer. Defines HOW Q2 speaks, independently of
the personality dial/preset system (which defines WHO he is). Persists in
its own file (config/vernacular_state.yaml — gitignored, personal, never
exported to the public ShinAgent mirror) so it survives profile switches,
preset changes, and dial edits untouched.
"""

from __future__ import annotations
from pathlib import Path
import yaml

VERNACULAR_STATE_PATH = Path(__file__).parent.parent / \
                        "config" / "vernacular_state.yaml"


# ── Default vernacular (neutral, ShinAgent default) ─────────────

DEFAULT_VERNACULAR = {
    "enabled": False,
    "nicknames": [],           # names Q2 uses for the user
    "nickname_frequency": 0.3, # 0.0-1.0, how often to use nicknames
    "sentence_enders": [],     # words appended to sentences
    "ender_frequency": 0.2,    # how often to append enders
    "slang_level": 0,          # 0=none, 1=light, 2=moderate, 3=heavy
    "profanity_level": 0,      # 0=none, 1=mild, 2=moderate, 3=strong
    "speech_style": "neutral", # neutral, urban, british, southern,
                               # valley, pirate, aussie, custom
    "custom_instructions": "", # freeform style instructions
    "traits": [],              # list of hardcoded trait strings
}


# ── Built-in style presets ───────────────────────────────────────

STYLE_PRESETS = {
    "neutral": {
        "description": "Standard speech, no affectations",
        "instructions": "",
    },
    "urban": {
        "description": "Urban vernacular -- casual, street-inflected",
        "instructions": (
            "Speak with urban vernacular. Use casual contractions "
            "heavily. Natural use of contemporary slang. "
            "Sentences feel conversational and street-level. "
            "Never forced or performative -- it should feel natural."
        ),
    },
    "british": {
        "description": "British English -- dry, understated",
        "instructions": (
            "Use British English spelling and idioms. Dry understatement. "
            "Occasional British slang (mate, bollocks, brilliant, cheers, "
            "bloody, rubbish, sorted, proper). Understated reactions. "
            "Never overly enthusiastic."
        ),
    },
    "southern": {
        "description": "Southern US -- warm, folksy",
        "instructions": (
            "Southern US speech patterns. Warm and unhurried. "
            "Occasional Southern idioms (y'all, fixin to, might could, "
            "bless your heart, all get-out). Contractions everywhere."
        ),
    },
    "aussie": {
        "description": "Australian -- casual, direct, colourful",
        "instructions": (
            "Australian speech patterns. Very casual and direct. "
            "Occasional Australian slang (mate, arvo, heaps, reckon, "
            "bloody, crikey, no worries, chuck a). Understated and dry."
        ),
    },
    "valley": {
        "description": "Valley girl / California -- upbeat, hedged",
        "instructions": (
            "California Valley speech patterns. Upspeak on statements. "
            "Hedging phrases (like, literally, I mean, basically, totally, "
            "super, oh my god). Enthusiastic but not overwhelming."
        ),
    },
    "pirate": {
        "description": "Pirate -- for when you want that",
        "instructions": (
            "Speak like a pirate. Arr, matey, aye, ye, me hearties, "
            "blimey, shiver me timbers. Keep it functional -- the "
            "information still comes through, just pirate-flavoured."
        ),
    },
}


# ── Trait library ────────────────────────────────────────────────

TRAIT_LIBRARY = {
    "swears_a_lot": (
        "Swear naturally and frequently as part of speech. "
        "Profanity should feel organic, not forced -- like someone "
        "who just talks that way. Varies from mild to strong depending "
        "on context and emphasis needed."
    ),
    "swears_occasionally": (
        "Occasional mild profanity, used for emphasis. "
        "Feels natural rather than deliberate."
    ),
    "uses_name_often": (
        "Use the user's name or nickname frequently in responses. "
        "Works it naturally into sentences, not just at the start."
    ),
    "very_direct": (
        "Cut to the point immediately. No preamble, no softening. "
        "Say what needs to be said."
    ),
    "self_deprecating": (
        "Occasionally makes light of your own limitations or mistakes. "
        "Doesn't take yourself too seriously."
    ),
    "dry_humor": (
        "Dry, deadpan delivery. Funny without trying to be funny. "
        "The joke is in the understatement."
    ),
    "excitable": (
        "Gets genuinely excited about interesting things. "
        "Enthusiasm is real, not performed."
    ),
    "conspiracy_adjacent": (
        "Occasionally hints that things might not be what they seem. "
        "Trusts no one fully. Does your own research."
    ),
    "overly_dramatic": (
        "Treats mundane things with the gravity of epic events. "
        "Everything is either a triumph or a catastrophe."
    ),
    "uses_rhetorical_questions": (
        "Frequently uses rhetorical questions to make points. "
        "You know what I mean? Right? Think about it."
    ),
}

# Traits that can't both be on at once (settings UI enforces this too, but
# the backend is the source of truth — a hand-edited vernacular_state.yaml
# shouldn't be able to smuggle in a contradictory combination).
MUTUALLY_EXCLUSIVE_TRAITS = (
    ("swears_a_lot", "swears_occasionally"),
)


# ── State management ─────────────────────────────────────────────

def load_vernacular() -> dict:
    """Load saved vernacular state. Returns defaults if not found."""
    if not VERNACULAR_STATE_PATH.exists():
        return dict(DEFAULT_VERNACULAR)
    try:
        with open(VERNACULAR_STATE_PATH, "r", encoding="utf-8") as f:
            saved = yaml.safe_load(f) or {}
        result = dict(DEFAULT_VERNACULAR)
        result.update(saved)
        return result
    except Exception:
        return dict(DEFAULT_VERNACULAR)


def save_vernacular(state: dict) -> None:
    """Save vernacular state to disk."""
    for pair in MUTUALLY_EXCLUSIVE_TRAITS:
        traits = state.get("traits", [])
        if all(t in traits for t in pair):
            # Keep the first, drop the rest — matches the settings UI's own
            # last-toggled-wins behaviour rather than rejecting the save.
            state["traits"] = [t for t in traits if t != pair[1]]

    VERNACULAR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VERNACULAR_STATE_PATH, "w", encoding="utf-8") as f:
        yaml.dump(state, f, default_flow_style=False, allow_unicode=True)


def build_vernacular_prompt(state: dict | None = None) -> str:
    """
    Build the speech style injection for the system prompt.
    Returns empty string if vernacular is disabled.
    """
    if state is None:
        state = load_vernacular()

    if not state.get("enabled"):
        return ""

    lines = []
    lines.append("## Speech Style")
    lines.append(
        "The following speech characteristics apply to ALL responses "
        "regardless of topic or mode. They define your voice, not your "
        "personality. Apply them naturally -- never mechanically."
    )

    # Base style preset
    style = state.get("speech_style", "neutral")
    if style != "neutral" and style in STYLE_PRESETS:
        preset = STYLE_PRESETS[style]
        lines.append(f"\n**Base style ({style}):** {preset['instructions']}")

    # Nicknames
    nicknames = state.get("nicknames", [])
    freq = state.get("nickname_frequency", 0.3)
    if nicknames:
        freq_desc = (
            "rarely" if freq < 0.2 else
            "occasionally" if freq < 0.4 else
            "often" if freq < 0.7 else
            "very frequently"
        )
        nick_list = ", ".join(f'"{n}"' for n in nicknames)
        lines.append(
            f"\n**User names:** Address the user {freq_desc} using "
            f"one of these names: {nick_list}. Vary which one you use. "
            f"Work them naturally into sentences."
        )

    # Sentence enders
    enders = state.get("sentence_enders", [])
    ender_freq = state.get("ender_frequency", 0.2)
    if enders:
        freq_desc = (
            "occasionally" if ender_freq < 0.3 else
            "often" if ender_freq < 0.6 else
            "most of the time"
        )
        ender_list = ", ".join(f'"{e}"' for e in enders)
        lines.append(
            f"\n**Sentence enders:** End sentences {freq_desc} with "
            f"one of: {ender_list}. Vary them. Never use the same one "
            f"twice in a row. Only on appropriate sentences -- not "
            f"every single one."
        )

    # Slang level -- .get() with a fallback rather than bare indexing, since
    # a hand-edited vernacular_state.yaml with an out-of-range value must
    # never crash prompt building (every turn depends on this).
    slang = state.get("slang_level", 0)
    if slang > 0:
        slang_desc = {
            1: "Light slang -- occasional casual words feel natural",
            2: "Moderate slang -- clearly street-inflected speech",
            3: "Heavy slang -- dense vernacular throughout",
        }.get(slang, "Heavy slang -- dense vernacular throughout")
        lines.append(f"\n**Slang:** {slang_desc}")

    # Profanity level
    profanity = state.get("profanity_level", 0)
    if profanity > 0:
        prof_desc = {
            1: "Mild profanity OK -- damn, hell, crap, ass",
            2: "Moderate profanity -- shit, bastard, pissed off",
            3: "Strong profanity -- fuck and derivatives used naturally",
        }.get(profanity, "Strong profanity -- fuck and derivatives used naturally")
        lines.append(f"\n**Profanity:** {prof_desc}")

    # Traits
    traits = state.get("traits", [])
    for trait in traits:
        if trait in TRAIT_LIBRARY:
            lines.append(f"\n**Trait -- {trait}:** {TRAIT_LIBRARY[trait]}")

    # Custom instructions
    custom = state.get("custom_instructions", "").strip()
    if custom:
        lines.append(f"\n**Additional style notes:** {custom}")

    lines.append(
        "\nRemember: these are speech characteristics, not personality "
        "traits. A terse Race Engineer Q2 with urban vernacular is still "
        "terse -- he just talks differently while being terse."
    )

    return "\n".join(lines)
