"""
IMQ2 LLM Backend
Swappable backend: Claude | OpenAI | Ollama
All backends expose the same interface: complete(messages, system, tools) -> LLMResponse
"""

import os
import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from config.loader import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared response type
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool invocation the model wants to make."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """
    Unified response shape across backends. `text` may be empty if the model
    only wants to call tools this turn. `tool_calls` is empty for a normal
    text-only reply. `raw_assistant_message` carries whatever backend-specific
    message structure is needed to continue the conversation (e.g. Claude
    requires echoing back its own tool_use blocks before tool_result blocks).
    """
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_assistant_message: Optional[dict] = None

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def wants_tool_use(self) -> bool:
        return len(self.tool_calls) > 0


# ---------------------------------------------------------------------------
# Shared message builders
# ---------------------------------------------------------------------------

def build_vision_message(image_bytes: bytes, media_type: str, text: str) -> dict:
    """
    Build a single user message containing an image + text prompt, in the
    canonical (Anthropic-style) content-block format used across this file.
    Each backend's complete() translates these blocks into its own wire
    format as needed — mirrors how tool schemas are defined once in
    Anthropic's input_schema format and translated per backend (_to_openai_tool).
    """
    return {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image_bytes).decode(),
                },
            },
            {"type": "text", "text": text},
        ],
    }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class LLMBackend(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Send messages and return an LLMResponse. If `tools` is provided and
        the model decides to use one, response.tool_calls will be populated
        and response.text may be empty — the caller is responsible for
        running the tool(s) and calling complete() again with the results
        appended to messages (see core/agent.py's tool-use loop).

        `max_tokens` overrides this backend instance's configured default
        for this call only (e.g. a structured-JSON-generation call that
        needs a bigger budget than normal chat replies) — omit it to use
        the config.yaml-configured value as before.
        """
        ...

    @abstractmethod
    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        """Build the backend-specific message format for returning a tool's result."""
        ...

    @abstractmethod
    def name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Claude (Anthropic)
# ---------------------------------------------------------------------------

class ClaudeBackend(LLMBackend):
    def __init__(self):
        import anthropic
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = config.get("llm.claude.model", "claude-sonnet-4-6")
        self._max_tokens = config.get("llm.claude.max_tokens", 1024)
        self._temperature = config.get("llm.claude.temperature", 0.7)

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        # raw_assistant_message must echo Claude's own content blocks verbatim —
        # required so a subsequent tool_result message correctly references them.
        raw_assistant_message = {"role": "assistant", "content": response.content}

        usage = getattr(response, "usage", None)
        prompt_tokens     = getattr(usage, "input_tokens",  0) if usage else 0
        completion_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        log.info(f"Tokens [{self.name()}] prompt={prompt_tokens} completion={completion_tokens} total={prompt_tokens+completion_tokens}")

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            raw_assistant_message=raw_assistant_message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": result,
                }
            ],
        }

    def name(self) -> str:
        return f"claude/{self._model}"


def _to_openai_tool(t: dict) -> dict:
    """
    Convert an Anthropic-format tool definition to OpenAI function-calling format.
    Anthropic uses 'input_schema'; OpenAI/xAI use 'parameters'.
    """
    fn = {
        "name":        t.get("name", ""),
        "description": t.get("description", ""),
        "parameters":  t.get("input_schema") or t.get("parameters") or {},
    }
    return {"type": "function", "function": fn}


def _to_openai_content(content):
    """
    Translate a message's content from the canonical Anthropic-style block
    format (see build_vision_message) into OpenAI's format. Plain string
    content and already-OpenAI-shaped blocks pass through unchanged.
    """
    if not isinstance(content, list):
        return content
    translated = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source", {})
            translated.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}",
                },
            })
        else:
            translated.append(block)
    return translated


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    return [{**m, "content": _to_openai_content(m.get("content"))} for m in messages]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIBackend(LLMBackend):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self._model = config.get("llm.openai.model", "gpt-4o")
        self._max_tokens = config.get("llm.openai.max_tokens", 1024)
        self._temperature = config.get("llm.openai.temperature", 0.7)

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        full_messages = messages
        if system:
            full_messages = [{"role": "system", "content": system}] + messages
        full_messages = _to_openai_messages(full_messages)

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            temperature=self._temperature,
            messages=full_messages,
        )
        if tools:
            # OpenAI expects tools wrapped as {"type": "function", "function": {...}}
            # with 'parameters' not 'input_schema'
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0].message

        tool_calls = []
        if choice.tool_calls:
            import json as _json
            for tc in choice.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments),
                ))

        usage = getattr(response, "usage", None)
        prompt_tokens     = getattr(usage, "prompt_tokens",     0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        log.info(f"Tokens [{self.name()}] prompt={prompt_tokens} completion={completion_tokens} total={prompt_tokens+completion_tokens}")

        return LLMResponse(
            text=choice.content or "",
            tool_calls=tool_calls,
            raw_assistant_message={"role": "assistant", "content": choice.content, "tool_calls": choice.tool_calls},
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        }

    def name(self) -> str:
        return f"openai/{self._model}"


# ---------------------------------------------------------------------------
# Ollama (local) — tool use not yet supported by most local models reliably,
# so this backend ignores `tools` entirely for now.
# ---------------------------------------------------------------------------

def _to_ollama_message(m: dict) -> dict:
    """
    Translate a message from the canonical Anthropic-style block format
    (see build_vision_message) into Ollama's format: plain text `content`
    plus a separate `images` list of raw base64 strings (no data URI prefix,
    unlike OpenAI's image_url). Non-list content passes through unchanged.
    """
    content = m.get("content")
    if not isinstance(content, list):
        return m

    text_parts = []
    images = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image":
            images.append(block.get("source", {}).get("data", ""))
        elif block.get("type") == "text":
            text_parts.append(block.get("text", ""))

    translated = {**m, "content": "\n".join(text_parts)}
    if images:
        translated["images"] = images
    return translated


class OllamaBackend(LLMBackend):
    def __init__(self):
        import requests
        self._requests = requests
        self._host = config.get("llm.ollama.host", "http://localhost:11434")
        self._model = config.get("llm.ollama.model", "llama3")

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": [_to_ollama_message(m) for m in messages],
            "stream": False,
        }
        if system:
            payload["system"] = system
        if max_tokens:
            payload["options"] = {"num_predict": max_tokens}
        r = self._requests.post(f"{self._host}/api/chat", json=payload, timeout=60)
        r.raise_for_status()
        text = r.json()["message"]["content"]
        return LLMResponse(text=text, tool_calls=[], raw_assistant_message={"role": "assistant", "content": text})

    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        # Not supported — Ollama backend never produces tool_calls, so this should
        # never actually be invoked, but implemented for interface completeness.
        return {"role": "user", "content": f"[Tool result for {tool_call.name}]: {result}"}

    def name(self) -> str:
        return f"ollama/{self._model}"


# ---------------------------------------------------------------------------
# Grok (xAI) — OpenAI-compatible API at api.x.ai
# Grok supports function calling with the same schema as OpenAI.
# ---------------------------------------------------------------------------

class GrokBackend(LLMBackend):
    def __init__(self):
        from openai import OpenAI
        api_key = os.environ.get("XAI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "XAI_API_KEY not set in .env. "
                "Get one at console.x.ai and add it to ~/imq2/.env"
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        self._model      = config.get("llm.grok.model",      "grok-3-mini")
        self._max_tokens = config.get("llm.grok.max_tokens", 1024)
        self._temperature= config.get("llm.grok.temperature", 0.7)

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        import json as _json

        full_messages = messages
        if system:
            full_messages = [{"role": "system", "content": system}] + messages
        full_messages = _to_openai_messages(full_messages)

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            temperature=self._temperature,
            messages=full_messages,
        )
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        response = self._client.chat.completions.create(**kwargs)
        choice   = response.choices[0].message

        tool_calls = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments),
                ))

        usage = getattr(response, "usage", None)
        prompt_tokens     = getattr(usage, "prompt_tokens",     0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        log.info(f"Tokens [{self.name()}] prompt={prompt_tokens} completion={completion_tokens} total={prompt_tokens+completion_tokens}")

        return LLMResponse(
            text=choice.content or "",
            tool_calls=tool_calls,
            raw_assistant_message={
                "role": "assistant",
                "content": choice.content,
                "tool_calls": choice.tool_calls,
            },
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        }

    def name(self) -> str:
        return f"grok/{self._model}"


# ---------------------------------------------------------------------------
# GLM (Z.ai / Zhipu AI) — OpenAI-compatible API
# GLM-5.2 is a 744B MoE model with 1M context, MIT licensed, strong at coding
# and agentic tasks. API endpoint: api.z.ai/api/paas/v4
# ---------------------------------------------------------------------------

class GLMBackend(LLMBackend):
    def __init__(self):
        from openai import OpenAI
        # Use Ollama local endpoint if no ZAI_API_KEY — routes via Ollama cloud
        api_key = os.environ.get("ZAI_API_KEY", "ollama")
        base_url = "https://api.z.ai/api/paas/v4" if api_key != "ollama" else "http://localhost:11434/v1"
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model      = config.get("llm.glm.model",      "glm-5.2")
        self._max_tokens = config.get("llm.glm.max_tokens", 1024)
        self._temperature= config.get("llm.glm.temperature", 0.7)

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        import json as _json

        full_messages = messages
        if system:
            full_messages = [{"role": "system", "content": system}] + messages
        full_messages = _to_openai_messages(full_messages)

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            temperature=self._temperature,
            messages=full_messages,
        )
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        response = self._client.chat.completions.create(**kwargs)
        choice   = response.choices[0].message

        tool_calls = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments),
                ))

        usage = getattr(response, "usage", None)
        prompt_tokens     = getattr(usage, "prompt_tokens",     0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        log.info(f"Tokens [{self.name()}] prompt={prompt_tokens} completion={completion_tokens} total={prompt_tokens+completion_tokens}")

        return LLMResponse(
            text=choice.content or "",
            tool_calls=tool_calls,
            raw_assistant_message={
                "role": "assistant",
                "content": choice.content,
                "tool_calls": choice.tool_calls,
            },
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        }

    def name(self) -> str:
        return f"glm/{self._model}"


# ---------------------------------------------------------------------------
# Gemini (Google AI Studio) — OpenAI-compatible API at generativelanguage.googleapis.com
# Free tier: 500 RPD, 60 RPM, 1M token context. No credit card required.
# ---------------------------------------------------------------------------

class GeminiBackend(LLMBackend):
    def __init__(self):
        from openai import OpenAI
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set in .env. "
                "Get a free key at aistudio.google.com — "
                "no credit card required."
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        self._model      = config.get("llm.gemini.model",      "gemini-2.5-flash")
        self._max_tokens = config.get("llm.gemini.max_tokens", 1024)
        self._temperature= config.get("llm.gemini.temperature", 0.7)

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        import json as _json

        full_messages = messages
        if system:
            full_messages = [{"role": "system", "content": system}] + messages
        full_messages = _to_openai_messages(full_messages)

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            temperature=self._temperature,
            messages=full_messages,
        )
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        response = self._client.chat.completions.create(**kwargs)
        choice   = response.choices[0].message

        tool_calls = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments),
                ))

        usage = getattr(response, "usage", None)
        prompt_tokens     = getattr(usage, "prompt_tokens",     0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        log.info(f"Tokens [{self.name()}] prompt={prompt_tokens} completion={completion_tokens} total={prompt_tokens+completion_tokens}")

        return LLMResponse(
            text=choice.content or "",
            tool_calls=tool_calls,
            raw_assistant_message={
                "role": "assistant",
                "content": choice.content,
                "tool_calls": choice.tool_calls,
            },
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def build_tool_result_message(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        }

    def name(self) -> str:
        return f"gemini/{self._model}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_BACKENDS = {
    "claude": ClaudeBackend,
    "openai": OpenAIBackend,
    "ollama": OllamaBackend,
    "grok":   GrokBackend,
    "glm":    GLMBackend,
    "gemini": GeminiBackend,
}


def get_llm_backend(override: Optional[str] = None) -> LLMBackend:
    """Return the configured LLM backend. Pass override to force a specific one."""
    backend_name = override or config.get("llm.backend", "claude")
    cls = _BACKENDS.get(backend_name)
    if cls is None:
        raise ValueError(f"Unknown LLM backend '{backend_name}'. Choose from: {list(_BACKENDS)}")
    log.info(f"Loading LLM backend: {backend_name}")
    return cls()


def list_backends() -> list[str]:
    """Return available backend names for the settings panel."""
    return list(_BACKENDS.keys())
