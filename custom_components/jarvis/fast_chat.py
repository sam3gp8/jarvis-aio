"""Fast-chat tier — a small local model for simple conversational turns.

JARVIS's regex ``local_engine`` already handles ~95% of requests at zero API
cost and escalates only what it can't template. Historically every escalation
went to the single big Main Agent model, so a throwaway "tell me a joke" paid
the same cost as "analyse my energy use and draft a plan".

This tier splits that escalation in two, using the complexity score
``local_engine`` already computes:

  * a turn that escalates but scores **below** the threshold is conversational
    but un-templatable → send it to a small, fast model (e.g. Gemma 3n e4b);
  * anything at or above the threshold is genuinely complex → the big Main
    Agent model (e.g. Qwen3) handles it, exactly as before.

So the fast model carries everyday chat (instant), and the big model is reserved
for when depth actually matters. Pure routing policy — no I/O, no HA import — so
it is trivially testable; ``conversation.py`` consults it at the escalation point
and swaps the provider/model it hands to ``run_agent``.

Off until a fast model is named. Threshold and provider are configurable; a
blank fast provider means "same provider as the Main Agent" (the common case:
both are local Ollama models, differing only by tag).
"""
from __future__ import annotations

from .const import DEFAULT_FAST_CHAT_ENABLED, DEFAULT_FAST_MAX_COMPLEXITY


def _as_bool(v, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def fast_tier_target(config: dict, complexity):
    """Return ``{"provider": ..., "model": ...}`` for the fast conversational
    tier when this turn should use the small model, else ``None`` (use the Main
    Agent unchanged).

    A turn routes to the fast tier only when ALL hold:
      * the tier is enabled (``fast_chat_enabled``);
      * a fast model is configured (``fast_model`` non-blank);
      * ``complexity`` is a real, non-negative score **below** the threshold
        (``fast_chat_max_complexity``, default 70).

    The non-negative guard matters: direct-reasoning mode passes ``complexity =
    -1`` to force the big reasoning agent, and that must never divert to the
    small model. A blank ``fast_provider`` falls back to the Main Agent's
    provider (``llm_provider``), since the usual setup is one Ollama server
    serving both the big and small models."""
    if not isinstance(config, dict):
        return None
    if not _as_bool(config.get("fast_chat_enabled"), DEFAULT_FAST_CHAT_ENABLED):
        return None
    model = str(config.get("fast_model") or "").strip()
    if not model:
        return None
    try:
        complexity = int(complexity)
    except (TypeError, ValueError):
        return None
    if complexity < 0:                       # direct-reasoning sentinel → big model
        return None
    try:
        thresh = int(config.get("fast_chat_max_complexity") or DEFAULT_FAST_MAX_COMPLEXITY)
    except (TypeError, ValueError):
        thresh = DEFAULT_FAST_MAX_COMPLEXITY
    if complexity >= thresh:
        return None
    provider = (str(config.get("fast_provider") or "").strip()
                or str(config.get("llm_provider") or "").strip()
                or "ollama")
    return {"provider": provider, "model": model}


def fast_model_name(config: dict):
    """The configured fast model, or ``None`` — for the keep-warm pinger, which
    warms it alongside the Main Agent model whenever the tier is enabled. Honours
    the same enable gate so a stale model name left in config isn't warmed."""
    if not isinstance(config, dict):
        return None
    if not _as_bool(config.get("fast_chat_enabled"), DEFAULT_FAST_CHAT_ENABLED):
        return None
    model = str(config.get("fast_model") or "").strip()
    return model or None
