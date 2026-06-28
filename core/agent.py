"""
IMQ2 Agent Core
The central loop: takes user input, builds context, calls LLM, dispatches tools,
writes memory, returns response. Voice or text — same core.
"""

import logging
import datetime
from typing import Optional

from config.loader import config
from core.llm import get_llm_backend, LLMBackend
from core.fact_extractor import FactExtractor
from memory.manager import MemoryManager
from tools.registry import ToolRegistry
from personality.builder import build_system_prompt

log = logging.getLogger(__name__)

# Safety cap on tool-use round-trips per single user turn. Prevents a
# pathological loop (model keeps calling tools indefinitely) from hanging
# or burning API calls unboundedly.
MAX_TOOL_ITERATIONS = 5


class IMQ2Agent:
    """
    The agent. Instantiate once and keep alive.
    Call agent.chat(user_input) to get a response string.
    """

    def __init__(self, llm_override: Optional[str] = None):
        log.info("Initialising IMQ2...")

        self.llm: LLMBackend = get_llm_backend(llm_override)
        self.memory = MemoryManager()
        self.tools = ToolRegistry()
        self._short_term: list[dict] = []          # sliding context window
        self._max_short_term = config.get("memory.short_term_turns", 20)

        self._fact_extraction_enabled = config.get("memory.fact_extraction.enabled", True)
        self._fact_extractor = FactExtractor() if self._fact_extraction_enabled else None

        self._episodic_cap = config.get("memory.episodic_cap", 5000)
        self._prune_check_interval = config.get("memory.prune_check_interval", 50)
        self._turn_count = 0

        # Which schema format this backend expects for tool definitions
        self._tool_schema_format = "openai" if "openai" in self.llm.name() else "claude"

        log.info(f"Q2 online. LLM: {self.llm.name()}")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(self, user_input: str) -> str:
        """Process one turn. Returns Q2's response as a string."""
        user_input = user_input.strip()
        if not user_input:
            return ""

        log.debug(f"User: {user_input}")

        # 1. Retrieve relevant long-term memories (semantic recall)
        recalled = self.memory.retrieve(user_input)

        # 2. Pull all durable facts unconditionally — these matter regardless
        #    of whether this turn's wording semantically matches the original.
        facts = self.memory.get_facts()

        # 3. Build system prompt (personality + facts + recalled episodic memories)
        system = build_system_prompt(recalled_memories=recalled, facts=facts)

        # 4. Append user turn to short-term context
        self._append_turn("user", user_input)

        # 5. Run the tool-use loop: the LLM decides on its own whether it
        #    needs a tool (weather, definitions, web search, etc.) to answer.
        #    No keyword matching — this is genuine agentic tool selection.
        reply, prompt_tokens, completion_tokens = self._run_tool_use_loop(system)

        # 6. Trim short-term context
        self._trim_context()

        # 7. Store interaction in episodic long-term memory
        self.memory.store(
            user_text=user_input,
            agent_text=reply,
            timestamp=datetime.datetime.utcnow().isoformat(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        # 8. Extract and upsert durable facts from this turn (best-effort, non-blocking failure)
        if self._fact_extraction_enabled:
            self._extract_and_store_facts(user_input, reply)

        # 9. Periodically check whether episodic memory needs pruning
        self._turn_count += 1
        if self._turn_count % self._prune_check_interval == 0:
            self._maybe_prune()

        log.debug(f"Q2: {reply}")
        return reply

    def switch_profile(self, profile_name: str):
        """Hot-swap Q2's personality profile at runtime."""
        config.load_profile(profile_name)
        log.info(f"Personality profile switched to: {profile_name}")

    def reset_short_term(self):
        """Clear the sliding context window (start a fresh conversation)."""
        self._short_term = []
        log.info("Short-term context cleared.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_tool_use_loop(self, system: str) -> tuple[str, int, int]:
        """
        Calls the LLM, and if it requests tool use, executes the tool(s) and
        feeds results back, repeating until the model produces a final text
        reply (or MAX_TOOL_ITERATIONS is hit as a safety bound).
        Returns (reply_text, total_prompt_tokens, total_completion_tokens).
        """
        tool_schemas = self.tools.get_granted_schemas(backend_format=self._tool_schema_format)
        total_prompt = 0
        total_completion = 0

        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                # Compress short-term context before sending to LLM.
                # Headroom reduces token count 60-95% with accuracy preserved.
                # Falls back to uncompressed if headroom isn't available or fails.
                try:
                    from headroom import compress
                    compressed_messages = compress(
                        self._short_term,
                        model=self.llm.name(),
                    )
                    log.debug(f"Headroom: {len(self._short_term)} -> {len(compressed_messages)} messages")
                except Exception as hr_err:
                    log.debug(f"Headroom compression skipped: {hr_err}")
                    compressed_messages = self._short_term

                response = self.llm.complete(
                    messages=compressed_messages,
                    system=system,
                    tools=tool_schemas if tool_schemas else None,
                )
            except Exception as e:
                log.error(f"LLM error: {e}")
                return "I ran into a problem reaching my language backend. Check the logs.", total_prompt, total_completion

            total_prompt     += response.prompt_tokens
            total_completion += response.completion_tokens

            if not response.wants_tool_use:
                # Normal text reply — append and we're done.
                self._append_turn("assistant", response.text)
                return response.text, total_prompt, total_completion

            # Model wants to use one or more tools. Echo its own message back
            # into context first (required by Claude's API), then execute
            # each requested tool and append the results.
            if response.raw_assistant_message:
                self._short_term.append(response.raw_assistant_message)

            for tool_call in response.tool_calls:
                log.info(f"Tool call: {tool_call.name}({tool_call.input})")
                result = self.tools.execute(tool_call.name, tool_call.input)
                log.info(f"Tool result: {result[:200]}")
                result_message = self.llm.build_tool_result_message(tool_call, result)
                self._short_term.append(result_message)

        # Hit the iteration cap without a final answer — fail gracefully
        # rather than looping forever or crashing.
        log.warning(f"Tool-use loop hit MAX_TOOL_ITERATIONS ({MAX_TOOL_ITERATIONS}) without a final reply.")
        fallback = "I got stuck trying to look that up — want to try rephrasing?"
        self._append_turn("assistant", fallback)
        return fallback, total_prompt, total_completion

    def _append_turn(self, role: str, content: str):
        self._short_term.append({"role": role, "content": content})

    def _trim_context(self):
        """Keep context within max_short_term turns (pairs of user+assistant)."""
        max_messages = self._max_short_term * 2
        if len(self._short_term) > max_messages:
            self._short_term = self._short_term[-max_messages:]

    def _extract_and_store_facts(self, user_text: str, agent_text: str):
        try:
            facts = self._fact_extractor.extract(user_text, agent_text)
            if not facts:
                log.debug("Fact extraction ran, found nothing durable in this turn.")
            for f in facts:
                self.memory.store_fact(
                    subject=f["subject"],
                    content=f["content"],
                    category=f["category"],
                )
                log.info(f"Fact remembered: [{f['subject']}] {f['content']}")
        except Exception as e:
            # Fact extraction must never break the main conversation loop
            log.warning(f"Fact extraction failed silently: {e}")

    def _maybe_prune(self):
        count = self.memory.episodic_count()
        if count > self._episodic_cap:
            log.info(f"Episodic memory at {count} entries, exceeds cap of {self._episodic_cap} — pruning.")
            self.memory.prune_episodic(keep_last_n=self._episodic_cap)
