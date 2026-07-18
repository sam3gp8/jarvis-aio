"""
JARVIS — LLM provider abstraction.

The agent doesn't know or care which LLM backend it's talking to. This
module gives every backend the same interface so swapping providers is a
configuration change rather than a code rewrite.

Supported backends today:
  - groq        (default — fast, free tier, OpenAI-compatible API)
  - openai      (OpenAI direct, or any OpenAI-compatible endpoint)
  - ollama      (local, self-hosted via OpenAI-compatible endpoint)
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
    ) -> dict:
        """Run a synchronous chat completion. Returns standardised dict.

        model_override lets a caller request a different model for this single
        call (e.g. a vision-capable model for image analysis) without needing
        to create a new provider instance.
        """
        ...

    def supports_vision(self) -> bool:
        """Whether this backend + model can take image inputs."""
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

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None):
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

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None):
        kwargs: dict[str, Any] = {
            "model": model_override or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        extra = self._extra_body()
        if extra:
            kwargs["extra_body"] = extra
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

    def _extra_body(self) -> dict:
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

    def _extra_body(self) -> dict:
        # keep_alive + num_ctx are Ollama extensions passed through the
        # OpenAI-compatible endpoint; harmless no-ops on non-Ollama backends,
        # but only OllamaProvider sends them.
        return {"keep_alive": OLLAMA_KEEP_ALIVE, "options": {"num_ctx": OLLAMA_NUM_CTX}}


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

    def chat(self, messages, tools=None, max_tokens=512, temperature=0.7, model_override=None):
        # Anthropic's API splits system vs user/assistant, and uses a different
        # image block format than the OpenAI-style image_url our callers send.
        system = ""
        chat_msgs = []
        for m in messages:
            if m["role"] == "system":
                sys_c = m["content"]
                if isinstance(sys_c, list):
                    sys_c = " ".join(
                        p.get("text", "") for p in sys_c
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                system = sys_c if not system else system + "\n\n" + sys_c
            else:
                chat_msgs.append({
                    "role": m["role"],
                    "content": self._to_anthropic_content(m["content"]),
                })

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

        resp = self._client.messages.create(**kwargs)

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
    "gemini":    OpenAIProvider,    # Gemini exposes an OpenAI-compatible API
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
    For 'gemini', base_url defaults to Google's OpenAI-compat endpoint.
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
    elif provider_name == "gemini" and not base_url:
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

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
    For Gemini tiers, uses gemini_api_key if set, else falls back to api_key.
    """
    from .const import (
        CONF_API_KEY, CONF_MODEL, CONF_GEMINI_API_KEY,
        DEFAULT_CLASSIFIER_PROVIDER, DEFAULT_CLASSIFIER_MODEL,
        DEFAULT_REASONING_PROVIDER, DEFAULT_REASONING_MODEL,
        DEFAULT_REVIEW_PROVIDER, DEFAULT_REVIEW_MODEL,
    )

    tier_defaults = {
        "classifier":   (DEFAULT_CLASSIFIER_PROVIDER, DEFAULT_CLASSIFIER_MODEL),
        "reasoning":    (DEFAULT_REASONING_PROVIDER, DEFAULT_REASONING_MODEL),
        "review":       (DEFAULT_REVIEW_PROVIDER, DEFAULT_REVIEW_MODEL),
        "conversation": (
            config.get("llm_provider", "groq"),
            config.get(CONF_MODEL, "llama-3.3-70b-versatile"),
        ),
    }

    if tier not in tier_defaults:
        raise ValueError(f"Unknown tier: {tier}")

    default_provider, default_model = tier_defaults[tier]

    provider_name = config.get(f"{tier}_provider", default_provider)
    model         = config.get(f"{tier}_model", default_model)

    # Pick the right API key based on provider
    if provider_name == "gemini":
        api_key = config.get(CONF_GEMINI_API_KEY) or config.get(CONF_API_KEY, "")
    elif provider_name == "groq":
        api_key = config.get(CONF_API_KEY, "")
    else:
        api_key = config.get(f"{tier}_api_key") or config.get(CONF_API_KEY, "")

    # Per-tier base_url wins; otherwise the shared llm_base_url applies for
    # local/self-hosted backends (Ollama on the GPU server, any OpenAI-compatible
    # endpoint). Cloud providers keep their canonical endpoints.
    base_url = config.get(f"{tier}_base_url")
    if not base_url and provider_name in ("ollama", "custom"):
        base_url = config.get("llm_base_url") or None

    _LOGGER.debug("Creating %s tier provider: %s / %s", tier, provider_name, model)

    return create_provider(
        provider_name=provider_name,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )
