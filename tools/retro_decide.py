"""
IMQ2 Retro Gaming -- LLM decision endpoint
Used by the ShinAgent HUD's retro_ai.py (Player 2 AI) for its "LLM"/"hybrid"
modes. Deliberately bypasses IMQ2Agent.chat() entirely and calls the active
LLM backend directly with a small, purpose-built prompt -- same pattern as
tools/acc_setup_generator.py and tools/popup_video.py use for structured,
non-conversational output. Routing this through the full conversational
agent would be wrong on every axis that matters here: it would run the
whole tool-use loop and store every "frame" as an episodic memory turn
(polluting real conversation history with hundreds of button-decision
exchanges), and Q2's personality/vernacular injection would actively fight
the "respond with ONLY a JSON array" instruction, since the vernacular
layer specifically pushes toward conversational, voice-flavoured prose.
"""
import json
import logging
import re

log = logging.getLogger(__name__)


def _build_prompt(game: str, system: str, state: dict, aggression: float,
                   buttons: list, recent_actions: list) -> str:
    return f"""You are controlling Player 2 in a game of {game} on {system}.
Current game state (raw values read from game RAM): {json.dumps(state)}
Aggression level: {aggression:.1f} (0.0 = passive, 1.0 = aggressive)
Available buttons: {buttons}
Recent actions (most recent last): {recent_actions[-5:]}

Respond with ONLY a JSON array of button names to press this frame, or an
empty array to do nothing this frame. Example: ["RIGHT", "B"] or [].
Keep it to 1-2 buttons maximum. No commentary, no explanation -- the
response must be parseable as a JSON array and nothing else.
"""


def decide_retro_action(game: str, system: str, state: dict, aggression: float = 0.5,
                         buttons: list = None, recent_actions: list = None) -> dict:
    """
    One-shot, stateless LLM call deciding the next controller input for
    Q2's Player 2 AI. Returns {"ok": True, "buttons": [...]} or
    {"ok": False, "error": "..."} -- never raises.
    """
    try:
        from core.llm import get_llm_backend

        prompt = _build_prompt(game, system, state, aggression,
                                buttons or [], recent_actions or [])
        llm = get_llm_backend()
        response = llm.complete(
            messages=[{"role": "user", "content": "Decide the next input."}],
            system=prompt,
            max_tokens=60,
        )

        match = re.search(r"\[.*?\]", response.text, re.DOTALL)
        if not match:
            return {"ok": False, "error": "No JSON array in LLM response"}
        parsed = json.loads(match.group())
        if not isinstance(parsed, list):
            return {"ok": False, "error": "LLM response was not a JSON array"}
        return {"ok": True, "buttons": [str(b) for b in parsed]}
    except Exception as e:
        log.warning(f"decide_retro_action error: {e}")
        return {"ok": False, "error": str(e)}
