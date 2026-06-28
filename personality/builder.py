"""
IMQ2 Personality Builder
Assembles the system prompt from the active profile + recalled memories.
Personality tuning uses the dial system (personality/dials.py, presets.py) —
each profile references a named preset plus optional inline overrides,
resolved into authored prose lines at prompt-build time.
"""

from config.loader import config
from personality.dials import PersonalityDials
from personality.presets import get_preset


def _resolve_profile_dials(profile: dict) -> PersonalityDials:
    """
    Load the profile's referenced preset, then apply any inline
    dial_overrides on top — lets a profile inherit a shared preset (e.g. the
    "Q2" preset) while tweaking individual dials just for that profile,
    without touching the shared preset everyone else uses.
    """
    preset_name = profile.get("dial_preset")
    if preset_name:
        base = get_preset(preset_name)
    else:
        base = PersonalityDials()  # all defaults if no preset specified

    overrides = profile.get("dial_overrides") or {}
    if overrides:
        merged = {**base.__dict__, **overrides}
        return PersonalityDials.from_dict(merged)
    return base


def build_system_prompt(recalled_memories: list[str] | None = None, facts: list[str] | None = None) -> str:
    profile = config.profile

    lines = []

    # Core identity block
    lines.append(f"You are {profile.get('full_name', 'IMQ2')}, known as {profile.get('name', 'Q2')}.")
    lines.append("")
    lines.append(profile.get("persona", "").strip())
    lines.append("")

    # Personality dials — resolved into authored prose lines describing
    # this persona's tendencies across warmth, sarcasm, honesty, spice, etc.
    dials = _resolve_profile_dials(profile)
    dial_prose = dials.resolve_prose()
    if dial_prose:
        lines.append("Your personality:")
        for p in dial_prose:
            lines.append(f"- {p}")
        lines.append("")

    # Voice-optimization is a structural/output-format requirement, not a
    # personality trait — it stays in effect regardless of dial values,
    # since Q2's responses are always spoken aloud, never read as text.
    if profile.get("voice_optimized", True):
        lines.append(
            "Keep responses voice-optimized — spoken, not written. No bullet lists, "
            "headers, or markdown formatting of any kind, since this is read aloud by "
            "text-to-speech. Favor shorter sentences over long written-style paragraphs."
        )
        lines.append("")

    # User context
    user_ctx = profile.get("user_context", "")
    if user_ctx:
        lines.append("Context about the user:")
        lines.append(user_ctx.strip())
        lines.append("")

    # Permissions summary
    perms = profile.get("permissions", {})
    denied = [k for k, v in perms.items() if not v]
    if denied:
        lines.append(
            f"You do not currently have permission to: {', '.join(denied)}. "
            "Acknowledge gracefully if these come up rather than attempting them."
        )
        lines.append("")

    # Durable facts — always included regardless of semantic similarity to the
    # current query, since a name or preference matters even if this turn's
    # wording doesn't closely match how it was originally stated.
    if facts:
        lines.append("Known facts about the user and ongoing context:")
        for f in facts:
            lines.append(f"- {f}")
        lines.append("")

    # Recalled long-term memories (semantic, episodic)
    if recalled_memories:
        lines.append("Relevant snippets from past conversations:")
        for m in recalled_memories:
            lines.append(f"- {m}")
        lines.append("")

    # Tool instructions — shared block first, then per-LLM override on top.
    # This lets GPT-4o get more explicit nudges than Claude without duplicating
    # the shared instructions across every profile.
    tool_instructions = profile.get("tool_instructions", "").strip()
    backend = config.get("llm.backend", "claude")
    llm_tool_instructions = profile.get(
        f"tool_instructions_{backend}", ""
    ).strip()
    combined_tool_instructions = "\n".join(
        filter(None, [tool_instructions, llm_tool_instructions])
    )
    if combined_tool_instructions:
        lines.append("Tool usage notes:")
        lines.append(combined_tool_instructions)
        lines.append("")

    lines.append("Current date/time (UTC): {datetime_placeholder}")

    import datetime
    prompt = "\n".join(lines)
    prompt = prompt.replace(
        "{datetime_placeholder}",
        datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    )

    return prompt
