"""
IMQ2 Fact Extractor
After each conversational turn, a cheap/fast model call checks whether the
exchange contains a durable fact worth remembering forever (names, preferences,
relationships, recurring details) as opposed to incidental chat.

Extracted facts are stored with a `subject` key so that re-mentioning the same
fact later overwrites rather than duplicates — keeping the facts table small
and consistent even after thousands of conversations.
"""

import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_EXTRACTION_INSTRUCTIONS = """You extract durable facts from a single conversation turn.

A durable fact is something that should be remembered indefinitely: names of
people/pets, preferences, relationships, recurring projects, important dates,
locations, or stated instructions to remember something. Incidental chat,
small talk, one-off questions, and anything already obviously transient is
NOT a durable fact.

Respond ONLY with valid JSON, no preamble, no markdown fences. Format:
{"facts": [{"subject": "short_snake_case_key", "content": "Full natural sentence stating the fact.", "category": "personal|preference|project|other"}]}

If there are no durable facts in this turn, respond with: {"facts": []}

Conversation turn:
"""


def _build_extraction_prompt(user_text: str, agent_text: str) -> str:
    """
    Builds the extraction prompt via plain concatenation rather than str.format(),
    since the instructions contain literal JSON braces that would otherwise collide
    with format placeholders (this caused a KeyError: '"facts"' bug previously).
    """
    return (
        _EXTRACTION_INSTRUCTIONS
        + f"User: {user_text}\n"
        + f"Assistant: {agent_text}\n"
    )


class FactExtractor:
    def __init__(self):
        pass

    def extract(self, user_text: str, agent_text: str) -> list[dict]:
        """
        Returns a list of {"subject": ..., "content": ..., "category": ...} dicts.
        Returns [] on any failure — fact extraction should never break the main chat loop.
        Uses Claude Haiku directly regardless of active LLM backend — cheap, fast,
        and keeps fact extraction cost separate from main chat token budget.
        """
        try:
            import anthropic, os
            from config.loader import config
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                log.debug("Fact extraction skipped — ANTHROPIC_API_KEY not set")
                return []
            model = config.get("memory.fact_extraction.model", "claude-haiku-4-5-20251001")
            client = anthropic.Anthropic(api_key=api_key)
            prompt = _build_extraction_prompt(user_text, agent_text)
            resp = client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = (resp.content[0].text if resp.content else "").strip()
            cleaned = raw.replace("```json", "").replace("```", "").strip()

            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as je:
                log.warning(f"Fact extraction JSON parse failed: {je}. Raw response was: {raw!r}")
                return []

            facts = parsed.get("facts", [])

            # Basic validation — skip malformed entries rather than failing the whole batch
            valid = []
            for f in facts:
                if isinstance(f, dict) and f.get("subject") and f.get("content"):
                    valid.append({
                        "subject": str(f["subject"]),
                        "content": str(f["content"]),
                        "category": str(f.get("category", "general")),
                    })

            if not valid and facts:
                log.warning(f"Fact extraction parsed JSON but no entries passed validation. Raw facts: {facts!r}")

            return valid

        except Exception as e:
            log.warning(f"Fact extraction failed with exception: {type(e).__name__}: {e}")
            return []
