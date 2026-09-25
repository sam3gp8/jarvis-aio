"""
JARVIS — LLM provider abstraction.

The agent doesn't know or care which LLM backend it's talking to. This
module gives every backend the same interface so swapping providers is a
configuration change rather than a code rewrite.

Supported backends today:
  - groq        (default — fast, free tier, OpenAI-compatible API)
  - openai      (OpenAI direct, or any OpenAI-compatible endpoint)
  - ollama      (local, self-hosted via OpenAI-compatible endpoint)
  - gemini      (Google GenAI SDK via the native Interactions API)
  - anthropic   (Claude API)
  - custom      (any OpenAI-compatible endpoint with a base_url)

Adding a new backend: subclass LLMProvider and register it in PROVIDERS.
Everything else — conversation, camera, briefings, sentinel — uses the
uniform interface and doesn't need changes.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


# ─── Standard response shape ─────────────────────────────────────────────────
#
# Every backend returns this structure:
#   {
#     "text": "...",                      # response text (may be empty if tools called)
#     "tool_calls": [                     # list of requested tool calls (or empty)
#       {"id": "call_xyz", "name": "...", "args": {...}},
#     ],
#     "raw": <provider-specific object>,  # for debugging / feeding back
#   }


class LLMProvider(ABC):
    """Abstract base for all LLM backends."""

    name: str = "abstract"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
        model_override: Optional[str] = None,
        thinking: Optional[bool] = None,
        run_state: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Run a synchronous chat completion. Returns standardised dict.

        model_override lets a caller request a different model for this single
        call (e.g. a vision-capable model for image analysis) without needing
        to create a new provider instance.

        thinking controls extended/internal reasoning on backends that support
        it (see supports_thinking). False requests the least-thinking setting
        available; None leaves the setting unset so the model default applies.
        Ignored by backends that don't support the toggle.

        run_state lets backends keep provider-specific continuation state for
        one agentic run without storing it on a shared provider instance.
        """
        ...

    def supports_vision(self) -> bool:
        """Whether this backend + model can take image inputs."""
        return False

    def supports_thinking(self) -> bool:
        """Whether this backend's `thinking` chat() kwarg has any effect."""
        return False

    def supports_tools(self) -> bool:
        """Whether this backend supports function/tool calling."""
        return True


# ─── Groq (default) ──────────────────────────────────────────────────────────

class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url)
        try:
            from groq import Groq
        except ImportError as exc:
            raise RuntimeError(
                "groq package not installed — `pip install groq`"
            ) from exc
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = Groq(**kwargs)

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None,
              thinking=None, run_state=None):
        kwargs: dict[str, Any] = {
            "model": model_override or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        tool_calls = []
        # Check message.tool_calls directly — Groq sometimes sets
        # finish_reason="stop" even when tool_calls are present.
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id":   tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments or "{}"),
                })
        return {
            "text": (choice.message.content or "").strip(),
            "tool_calls": tool_calls,
            "raw": choice.message,
        }

    def supports_vision(self) -> bool:
        # Groq vision-capable models all contain 'vision' in the name
        return "vision" in self.model.lower()


# ─── OpenAI / OpenAI-compatible ──────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """OpenAI, Azure OpenAI, or any OpenAI-compatible endpoint (Ollama, vLLM, etc)."""
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed — `pip install openai`"
            ) from exc
        kwargs = {"api_key": api_key or "not-required"}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None,
              thinking=None, run_state=None):
        kwargs: dict[str, Any] = {
            "model": model_override or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        extra = self._extra_body(thinking)
        if extra:
            kwargs["extra_body"] = extra
        for _attempt in range(3):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:
                # Newer reasoning models may reject both legacy parameters;
                # adapt each one once without maintaining a model-name list.
                msg = str(exc).lower()
                if ("max_tokens" in kwargs and "max_tokens" in msg
                        and "max_completion_tokens" in msg):
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                    continue
                if ("temperature" in kwargs and "temperature" in msg
                        and ("unsupported" in msg or "deprecated" in msg)):
                    kwargs.pop("temperature", None)
                    continue
                raise
        choice = resp.choices[0]
        if (not choice.message.tool_calls and not (choice.message.content or "").strip()
                and getattr(choice, "finish_reason", None) == "length"
                and "max_completion_tokens" in kwargs):
            # Reasoning models (o1/o3/gpt-5.x) spend the token budget on hidden
            # reasoning before ever emitting visible content — a modest budget
            # can be exhausted entirely by reasoning, leaving nothing to show
            # and no error. Retry once with substantially more room.
            kwargs["max_completion_tokens"] = max(kwargs["max_completion_tokens"] * 4, 2048)
            resp = self._client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
        tool_calls = []
        # Check message.tool_calls directly — some providers set
        # finish_reason="stop" even when tool_calls are present.
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id":   tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments or "{}"),
                })
        return {
            "text": (choice.message.content or "").strip(),
            "tool_calls": tool_calls,
            "raw": choice.message,
        }

    def _extra_body(self, thinking: Optional[bool] = None) -> dict:
        """Provider-specific request extras. Empty for vanilla OpenAI."""
        return {}

    def supports_vision(self) -> bool:
        # Modern OpenAI models all support vision; Ollama varies by model;
        # most Gemini models are multimodal; LLaVA supports vision locally
        vision_models = (
            "gpt-4", "gpt-5", "llava", "vision",
            "gemini-2", "gemini-3", "gemini-flash", "gemini-pro",
        )
        return any(v in self.model.lower() for v in vision_models)


# ─── Ollama (local, self-hosted) ─────────────────────────────────────────────
# Tuning for local inference: keep the model resident so we don't pay the
# load-into-VRAM cost on every call; raise the context window well above
# Ollama's 2048 default (JARVIS's system prompt + knowledge + history need the
# room, and a too-small window silently truncates); and allow a generous
# timeout since the first token can lag while a cold model loads.
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_NUM_CTX = 8192
OLLAMA_TIMEOUT = 120.0  # seconds


class OllamaProvider(OpenAIProvider):
    """Ollama via its OpenAI-compatible API, tuned for local/self-hosted use."""
    name = "ollama"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key or "ollama", model, self._normalize_url(base_url))
        # Local generation (and cold model loads) can outlast the default HTTP
        # timeout — give it room so a slow first token isn't a hard failure.
        try:
            self._client = self._client.with_options(timeout=OLLAMA_TIMEOUT)
        except Exception:
            pass

    @staticmethod
    def _normalize_url(base_url: Optional[str]) -> Optional[str]:
        # Accept a bare host:port and append Ollama's OpenAI-compatible path
        # (…:11434 → …:11434/v1) so the endpoint is correct either way.
        if base_url and base_url.rstrip("/").endswith(":11434"):
            return base_url.rstrip("/") + "/v1"
        return base_url

    def _extra_body(self, thinking: Optional[bool] = None) -> dict:
        # keep_alive + num_ctx are Ollama extensions passed through the
        # OpenAI-compatible endpoint; harmless no-ops on non-Ollama backends,
        # but only OllamaProvider sends them.
        # think: many local models (gemma3/4, qwen3, deepseek-r1) are
        # reasoning models — their thinking goes to a separate "reasoning"
        # field and "content" stays empty until it finishes. On a small token
        # budget that means an empty answer, so thinking defaults OFF unless
        # the caller opts in (dashboard thinking switch). Harmless on
        # non-reasoning models either way.
        # num_ctx is configurable (ollama_num_ctx) so a larger local model can
        # use a bigger context window; falls back to the default.
        num_ctx = OLLAMA_NUM_CTX
        try:
            from . import jarvis_config
            num_ctx = int(jarvis_config.get("ollama_num_ctx", OLLAMA_NUM_CTX) or OLLAMA_NUM_CTX)
            if num_ctx < 512:
                num_ctx = OLLAMA_NUM_CTX
        except Exception:
            num_ctx = OLLAMA_NUM_CTX
        return {"keep_alive": OLLAMA_KEEP_ALIVE, "think": bool(thinking),
                "options": {"num_ctx": num_ctx}}

    def supports_thinking(self) -> bool:
        return True


# ─── Gemini (Google GenAI Interactions API) ──────────────────────────────────

class GeminiProvider(LLMProvider):
    """Google Gemini through the native Interactions API.

    JARVIS callers retain OpenAI-shaped messages and tools. This adapter
    translates them at the provider boundary and chains interactions on the
    Gemini server so returned function-call steps retain their signatures.
    """
    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url)
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package not installed — `pip install google-genai`"
            ) from exc
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["http_options"] = {"base_url": base_url}
        self._client = genai.Client(**kwargs)

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None,
              thinking=None, run_state=None):
        state = run_state if run_state is not None else {}
        system_instruction = self._system_instruction(messages)
        continuation = self._is_continuation(messages, state)
        previous_messages = state.get("previous_messages", [])
        new_messages = (
            messages[len(previous_messages):]
            if continuation else self._plain_conversation_turns(messages)
        )
        input_items = self._input_items(new_messages)
        if not input_items:
            input_items = self._input_items(self._plain_conversation_turns(messages))

        generation_config: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        # Thinking-capable Gemini/Gemma models spend max_output_tokens on
        # internal "thought" steps before any answer text, so JARVIS's small
        # per-call budgets (e.g. vision's 300) can be exhausted by thinking
        # alone — status "incomplete" with empty output_text. The Interactions
        # API takes thinking_level directly on generation_config (NOT nested
        # under a "thinking_config" object — that's the generateContent/REST
        # ThinkingConfig shape, and it's silently dropped here since it isn't
        # a recognised field): "high" to think, "minimal" to (mostly) not.
        # thinking=None (caller has no opinion) leaves it unset entirely so
        # the model applies its own default — some models (e.g. gemini-3.8-
        # flash) 400 on "minimal", so callers that can't tolerate that error
        # (e.g. camera-reasoning) should pass thinking=None rather than False.
        if thinking is True:
            generation_config["thinking_level"] = "high"
        elif thinking is False:
            generation_config["thinking_level"] = "minimal"
        kwargs: dict[str, Any] = {
            "model": model_override or self.model,
            "input": input_items,
            "generation_config": generation_config,
        }
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if tools:
            kwargs["tools"] = [
                {"type": "function", **tool["function"]}
                for tool in tools
                if tool.get("type") == "function" and tool.get("function")
            ]
        previous_interaction_id = state.get("previous_interaction_id")
        if continuation and previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id

        active_generation_config = generation_config
        try:
            resp = self._client.interactions.create(**kwargs)
        except Exception as exc:
            # Not every model accepts "minimal" (ai.google.dev/gemini-api/docs/
            # thinking) — some reject it with "thinking level" (space), others
            # with "thinking_level" (field name), so match either.
            exc_msg = str(exc).lower()
            if (thinking is False
                    and ("thinking_level" in exc_msg or "thinking level" in exc_msg)):
                # Fresh dict for the retry — kwargs["generation_config"] must
                # not be mutated in place, or the first (failed) call's
                # recorded/logged config would silently reflect the retry.
                # "low" (not dropping the field) — every model in the
                # thinking-levels table supports it, and omitting it entirely
                # would leave thinking on by default, defeating the toggle.
                retry_config = dict(generation_config, thinking_level="low")
                resp = self._client.interactions.create(**{**kwargs, "generation_config": retry_config})
                active_generation_config = retry_config
            else:
                raise
        if self._is_incomplete(resp):
            retry_config = dict(
                active_generation_config,
                max_output_tokens=max(max_tokens * 4, 2048),
            )
            resp = self._client.interactions.create(
                **{**kwargs, "generation_config": retry_config}
            )
            if self._is_incomplete(resp):
                raise RuntimeError(
                    "Gemini interaction remained incomplete after output-budget retry"
                )
        if self._is_terminal_failure(resp):
            status = self._status_value(resp)
            raise RuntimeError(f"Gemini interaction failed with status: {status}")
        if run_state is not None:
            run_state["previous_interaction_id"] = getattr(resp, "id", None)
            run_state["previous_messages"] = [dict(message) for message in messages]
        tool_calls = []
        for step in getattr(resp, "steps", []) or []:
            if getattr(step, "type", None) == "function_call":
                tool_calls.append({
                    "id": getattr(step, "id", ""),
                    "name": getattr(step, "name", ""),
                    "args": getattr(step, "arguments", {}) or {},
                })
        return {
            "text": (getattr(resp, "output_text", "") or "").strip(),
            "tool_calls": tool_calls,
            "raw": resp,
        }

    def supports_vision(self) -> bool:
        return True

    def supports_thinking(self) -> bool:
        return True

    @staticmethod
    def _is_incomplete(response) -> bool:
        return GeminiProvider._status_value(response) == "incomplete"

    @staticmethod
    def _status_value(response) -> str:
        status = getattr(response, "status", None)
        status_value = getattr(status, "value", status)
        return str(status_value or "").lower().rsplit(".", 1)[-1]

    @staticmethod
    def _is_terminal_failure(response) -> bool:
        return GeminiProvider._status_value(response) in {
            "failed", "cancelled", "budget_exceeded",
        }

    @staticmethod
    def _is_continuation(messages: list[dict], run_state: dict[str, Any]) -> bool:
        previous_interaction_id = run_state.get("previous_interaction_id")
        previous_messages = run_state.get("previous_messages", [])
        return bool(previous_interaction_id and len(messages) >= len(previous_messages)
                    and messages[:len(previous_messages)] == previous_messages)

    @staticmethod
    def _plain_conversation_turns(messages: list[dict]) -> list[dict]:
        """Return text/image user turns and plain assistant text for a new interaction.

        JARVIS provides client-managed OpenAI-style history. Replaying its old
        structured tool-call messages as Interaction inputs can make Gemini
        process stale function calls without the server-side signatures it
        expects. Plain transcript turns are safe to preserve as native
        user_input/model_output items; same-run tool continuations use the
        server interaction ID instead.
        """
        turns = []
        for message in messages:
            role = message.get("role")
            plain_assistant = (
                role == "assistant" and not message.get("tool_calls")
                and GeminiProvider._text_content(message.get("content"))
            )
            if role == "user" or plain_assistant:
                turns.append(message)
        return turns

    @staticmethod
    def _system_instruction(messages: list[dict]) -> str:
        return "\n\n".join(
            GeminiProvider._text_content(message.get("content"))
            for message in messages if message.get("role") == "system"
        ).strip()

    @staticmethod
    def _text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content or "")

    def _input_items(self, messages: list[dict]) -> list[dict]:
        call_names = {
            call.get("id", ""): (call.get("function", {}) or {}).get("name", "")
            for message in messages if message.get("role") == "assistant"
            for call in message.get("tool_calls", []) or []
        }
        items = []
        for message in messages:
            role = message.get("role")
            if role == "user":
                items.append({"type": "user_input", "content": self._content_parts(message.get("content"))})
            elif role == "assistant" and not message.get("tool_calls"):
                text = self._text_content(message.get("content"))
                if text:
                    items.append({"type": "model_output", "content": [{"type": "text", "text": text}]})
            elif role == "tool":
                call_id = message.get("tool_call_id", "")
                items.append({
                    "type": "function_result",
                    "name": call_names.get(call_id, ""),
                    "call_id": call_id,
                    "result": [{"type": "text", "text": self._text_content(message.get("content"))}],
                })
        return items

    @staticmethod
    def _content_parts(content: Any) -> list[dict]:
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content or "")}]
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append({"type": "text", "text": part.get("text", "")})
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    header, data = url.split(",", 1)
                    mime_type = header.split(":", 1)[1].split(";", 1)[0]
                    parts.append({"type": "image", "mime_type": mime_type, "data": data})
                elif url:
                    parts.append({"type": "image", "uri": url})
        return parts or [{"type": "text", "text": ""}]


# ─── Anthropic ───────────────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url)
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed — `pip install anthropic`"
            ) from exc
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = Anthropic(**kwargs)

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None,
              thinking=None, run_state=None):
        # Anthropic's API splits system vs user/assistant, and uses a different
        # image block format than the OpenAI-style image_url our callers send.
        # It also has no "tool" role: a tool call is an assistant `tool_use`
        # content block, and its result is a `tool_result` block inside a
        # *user* message — our callers (agent.py) build history in OpenAI's
        # shape (assistant.tool_calls + role="tool"), so translate it here
        # rather than push Anthropic-specific shapes up into shared code.
        system = ""
        chat_msgs = []
        for m in messages:
            role = m["role"]
            if role == "system":
                sys_c = m["content"]
                if isinstance(sys_c, list):
                    sys_c = " ".join(
                        p.get("text", "") for p in sys_c
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                system = sys_c if not system else system + "\n\n" + sys_c
            elif role == "assistant" and m.get("tool_calls"):
                content = []
                text = m.get("content") or ""
                if text:
                    content.append({"type": "text", "text": text})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {}) or {}
                    args = fn.get("arguments", "{}")
                    try:
                        parsed_args = json.loads(args) if isinstance(args, str) else (args or {})
                    except Exception:
                        parsed_args = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": parsed_args,
                    })
                chat_msgs.append({"role": "assistant", "content": content})
            elif role == "tool":
                tool_result = {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": str(m.get("content", "")),
                }
                # Consecutive tool results (multiple calls in one turn) must
                # collapse into ONE user message — Anthropic doesn't allow
                # back-to-back messages with the same role.
                if chat_msgs and self._is_pure_tool_result(chat_msgs[-1]):
                    chat_msgs[-1]["content"].append(tool_result)
                else:
                    chat_msgs.append({"role": "user", "content": [tool_result]})
            else:
                new_content = self._to_anthropic_content(m["content"])
                # A plain user turn (e.g. the iteration-cap summary request)
                # can immediately follow a tool_result user message — merge
                # into it instead of emitting two consecutive "user" messages,
                # which Anthropic rejects.
                if role == "user" and chat_msgs and chat_msgs[-1]["role"] == "user":
                    prev_content = chat_msgs[-1]["content"]
                    if not isinstance(prev_content, list):
                        prev_content = [{"type": "text", "text": prev_content}]
                        chat_msgs[-1]["content"] = prev_content
                    if isinstance(new_content, list):
                        prev_content.extend(new_content)
                    else:
                        prev_content.append({"type": "text", "text": new_content})
                else:
                    chat_msgs.append({"role": role, "content": new_content})

        kwargs: dict[str, Any] = {
            "model": model_override or self.model,
            "system": system,
            "messages": chat_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            # Anthropic uses a slightly different tool schema
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:
            # Newer Claude models (e.g. claude-sonnet-5) reject `temperature`
            # outright ("temperature is deprecated for this model") rather
            # than just clamping it — retry once without it instead of
            # hardcoding a model list that will always be out of date.
            msg = str(exc).lower()
            if "temperature" in kwargs and "temperature" in msg and "deprecated" in msg:
                kwargs.pop("temperature", None)
                resp = self._client.messages.create(**kwargs)
            else:
                raise

        text_parts = []
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id":   block.id,
                    "name": block.name,
                    "args": block.input,
                })
        return {
            "text": "".join(text_parts).strip(),
            "tool_calls": tool_calls,
            "raw": resp,
        }

    def supports_vision(self) -> bool:
        return True  # all modern Claude models

    @staticmethod
    def _is_pure_tool_result(msg) -> bool:
        """Whether `msg` is a user message made entirely of tool_result
        blocks — the marker for "merge the next tool_result in here too"."""
        c = msg.get("content")
        return (msg.get("role") == "user" and isinstance(c, list) and bool(c)
                and all(isinstance(p, dict) and p.get("type") == "tool_result" for p in c))

    @staticmethod
    def _to_anthropic_content(content):
        """
        Convert OpenAI-style message content to Anthropic's format. Our camera
        pipeline sends images as {"type":"image_url","image_url":{"url":...}};
        Anthropic wants {"type":"image","source":{"type":"base64",...}}.
        Plain strings and text blocks pass through unchanged.
        """
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return content
        out = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "image_url":
                url = (part.get("image_url") or {}).get("url", "") or ""
                if url.startswith("data:"):
                    try:
                        header, b64 = url.split(",", 1)
                        media_type = header.split(":", 1)[1].split(";", 1)[0] or "image/jpeg"
                    except Exception:
                        media_type, b64 = "image/jpeg", ""
                    out.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    })
                elif url:
                    out.append({"type": "image", "source": {"type": "url", "url": url}})
            elif ptype == "text":
                out.append({"type": "text", "text": part.get("text", "")})
            else:
                out.append(part)
        return out


# ─── Registry ────────────────────────────────────────────────────────────────

PROVIDERS = {
    "groq":      GroqProvider,
    "openai":    OpenAIProvider,
    "ollama":    OllamaProvider,    # Ollama's OpenAI-compatible API, tuned local
    "gemini":    GeminiProvider,    # Google GenAI SDK / native Interactions API
    "custom":    OpenAIProvider,    # Any OpenAI-compatible endpoint
    "anthropic": AnthropicProvider,
}


_CLOUD_PROVIDERS = {"groq", "gemini", "openai", "anthropic"}
# Ollama tag syntax: name[:size-tag], e.g. gemma4:26b, llama3.3:70b-instruct.
# No cloud provider uses colon-tagged model ids, which makes this a safe tell.
_OLLAMA_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*$", re.I)


def normalize_routing(provider_name: str, model: str,
                       base_url: Optional[str]) -> tuple[str, Optional[str], Optional[str]]:
    """
    (provider_name, base_url, correction_note|None). v6.47.1: a colon-tagged
    model (Ollama syntax, e.g. 'gemma4:26b') configured against a cloud
    provider is a settings mismatch that produces confusing 404s from the
    cloud API ('models/gemma4:26b is not found') — the model plainly lives
    on the local Ollama server. Route it there and say so, instead of
    faithfully forwarding a local model name to Google.
    """
    p = (provider_name or "").lower().strip()
    m = (model or "").strip()
    if p in _CLOUD_PROVIDERS and _OLLAMA_TAG_RE.match(m):
        note = (f"model '{m}' uses Ollama tag syntax but provider was "
                f"'{p}' — routing to ollama"
                + ("" if base_url else " (default base URL)"))
        return "ollama", base_url, note
    return p, base_url, None


def create_provider(
    provider_name: str,
    api_key: str,
    model: str,
    base_url: Optional[str] = None,
) -> LLMProvider:
    """
    Factory for provider instances.

    provider_name: 'groq' | 'openai' | 'gemini' | 'ollama' | 'anthropic' | 'custom'
    For 'ollama', set base_url to e.g. 'http://homeassistant.local:11434/v1'
    Gemini uses Google's native Interactions API through the google-genai SDK.
    For 'custom', set base_url to whatever OpenAI-compatible endpoint you want.
    """
    provider_name = provider_name.lower().strip()
    provider_name, base_url, note = normalize_routing(provider_name, model, base_url)
    if note:
        _LOGGER.warning("LLM routing corrected: %s", note)
    cls = PROVIDERS.get(provider_name)
    if cls is None:
        _LOGGER.warning(
            "Unknown LLM provider '%s' — falling back to groq", provider_name
        )
        cls = GroqProvider

    # Default base URLs for provider-specific cases
    if provider_name == "ollama" and not base_url:
        base_url = "http://homeassistant.local:11434/v1"

    try:
        return cls(api_key=api_key, model=model, base_url=base_url)
    except Exception as exc:
        _LOGGER.error(
            "Failed to create provider '%s' (%s). Falling back to Groq.",
            provider_name, exc,
        )
        return GroqProvider(api_key=api_key, model=model, base_url=None)


def list_providers() -> list[str]:
    """For UI dropdowns."""
    return list(PROVIDERS.keys())


async def async_refresh_main_client(hass, entry) -> None:
    """Rebuild the active Main Agent client after a provider key rotation."""
    from .const import DOMAIN

    entry_id = getattr(entry, "entry_id", None)
    runtime = hass.data.get(DOMAIN, {}).get(entry_id) if entry_id else None
    if not isinstance(runtime, dict):
        return
    from . import ha_secrets, jarvis_config
    from .const import resolve_provider_base_url

    config = await hass.async_add_executor_job(jarvis_config.effective_config, entry)
    provider = config.get("llm_provider", "groq")
    api_key = await ha_secrets.async_get_provider_key(hass, provider)
    client = await hass.async_add_executor_job(
        create_provider,
        provider,
        api_key,
        config.get("model", "openai/gpt-oss-120b"),
        resolve_provider_base_url(config, provider),
    )
    runtime["client"] = client
    client_ref = runtime.get("client_ref")
    if isinstance(client_ref, dict):
        client_ref["client"] = client
    sentinel = runtime.get("sentinel")
    if sentinel is not None:
        sentinel._groq = client


# ─── v5.2 Tiered provider selection (observer mode) ──────────────────────────
#
# Observer mode uses three distinct LLM tiers:
#   - classifier: called often, must be cheap + fast (Flash-Lite)
#   - reasoning:  called when classifier flags something (Flash)
#   - review:     called periodically for deeper reasoning (Pro)
#
# Each tier can have its own provider and model. If tier-specific keys aren't
# set, falls back to defaults defined in const.py.

def create_tier_provider(
    config: dict,
    tier: str,
) -> LLMProvider:
    """
    Build a provider for a specific observer tier.

    tier must be one of: 'classifier', 'reasoning', 'review', 'conversation'.
    Every provider has its own dedicated credential field (see
    const.PROVIDER_API_KEY_FIELDS) so a tier can use a different provider than
    the Main Agent without clobbering or losing either key.
    """
    from .const import (
        CONF_API_KEY, CONF_MODEL, PROVIDER_API_KEY_FIELDS,
        DEFAULT_CLASSIFIER_PROVIDER, DEFAULT_CLASSIFIER_MODEL,
        DEFAULT_REASONING_PROVIDER, DEFAULT_REASONING_MODEL,
        DEFAULT_REVIEW_PROVIDER, DEFAULT_REVIEW_MODEL,
    )

    main_provider = config.get("llm_provider", "groq")
    main_model = config.get(CONF_MODEL, "openai/gpt-oss-120b")
    tier_defaults = {
        "classifier":   (DEFAULT_CLASSIFIER_PROVIDER, DEFAULT_CLASSIFIER_MODEL),
        "reasoning":    (DEFAULT_REASONING_PROVIDER, DEFAULT_REASONING_MODEL),
        "review":       (DEFAULT_REVIEW_PROVIDER, DEFAULT_REVIEW_MODEL),
        "conversation": (
            main_provider,
            main_model,
        ),
    }

    if tier not in tier_defaults:
        raise ValueError(f"Unknown tier: {tier}")

    default_provider, default_model = tier_defaults[tier]

    provider_name = config.get(f"{tier}_provider")
    model = config.get(f"{tier}_model")
    if not provider_name:
        provider_name = main_provider if tier in ("classifier", "reasoning") else default_provider
    if not model:
        model = main_model if tier in ("classifier", "reasoning") and provider_name == main_provider else default_model

    # Each provider's own field; ollama needs none. Falls back to the shared
    # api_key for an unrecognised provider name rather than raising.
    key_field = PROVIDER_API_KEY_FIELDS.get(provider_name, CONF_API_KEY)
    api_key = config.get(key_field, "") if key_field else ""

    # Per-tier base_url wins; otherwise the shared llm_base_url applies for
    # local/self-hosted backends (Ollama on the GPU server, any OpenAI-compatible
    # endpoint). Cloud providers keep their canonical endpoints.
    base_url = config.get(f"{tier}_base_url")
    if not base_url and provider_name in ("ollama", "custom"):
        provider_url_key = {
            "custom": "custom_base_url",
            "ollama": "ollama_base_url",
        }[provider_name]
        base_url = config.get(provider_url_key) or config.get("llm_base_url") or None

    _LOGGER.debug("Creating %s tier provider: %s / %s", tier, provider_name, model)

    return create_provider(
        provider_name=provider_name,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def _classify_conn_error(exc) -> str:
    """Map a provider/client exception to a config-flow error key."""
    msg = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return "cannot_connect"
    if any(t in msg for t in ("auth", "api key", "api_key", "401", "403",
                              "unauthorized", "invalid key", "permission")):
        return "invalid_auth"
    if any(t in msg for t in ("connect", "timeout", "timed out", "refused",
                              "resolve", "unreachable", "getaddrinfo",
                              "name or service", "connection", "network")):
        return "cannot_connect"
    return "unknown"


def _is_model_not_found(exc) -> bool:
    """Whether `exc` looks like a "model doesn't exist" response rather than a
    connection/auth failure. Every provider validates the key before checking
    the model, so this actually proves the key and endpoint are good — it
    just means the probe model (a fixed placeholder, since test_connection
    runs before the user picks a real model) isn't one this provider offers."""
    msg = str(exc).lower()
    if "model" not in msg:
        return False
    return any(t in msg for t in (
        "does not exist", "not found", "not_found", "no such model",
        "unknown model", "invalid model", "unsupported model",
    ))


async def test_connection(hass, provider, api_key, model, base_url):
    """Verify the LLM is reachable and the credentials work with a tiny chat
    call, so setup can fail fast on a bad URL or key instead of installing into
    a broken state. Returns None on success, else a config-flow error key
    ('cannot_connect' | 'invalid_auth' | 'unknown'). Client construction (which
    does blocking SSL cert loading) and the ping both run in the executor;
    never raises.
    """
    def _ping():
        client = create_provider(provider, api_key, model, base_url or None)
        if client is None:
            raise RuntimeError("cannot_connect")
        return client.chat([{"role": "user", "content": "ping"}],
                           tools=None, max_tokens=5)
    try:
        await hass.async_add_executor_job(_ping)
        return None
    except Exception as exc:
        if provider != "ollama" and _is_model_not_found(exc):
            return None
        return _classify_conn_error(exc)
