"""JARVIS intent layer: local NLP command routing and feedback."""
from __future__ import annotations

from .intent_router import (
    EVENT_FEEDBACK_WINDOW,
    LocalIntentRouter,
    is_affirmative,
    match_intent,
)
from .templates import STATUS_TEMPLATES, match_status, template_for

__all__ = [
    "LocalIntentRouter",
    "match_intent",
    "is_affirmative",
    "EVENT_FEEDBACK_WINDOW",
    "STATUS_TEMPLATES",
    "template_for",
    "match_status",
    "async_setup_intents",
]


def async_setup_intents(hass) -> None:
    """Home Assistant's intent loader calls ``<integration>.intent.async_setup_intents``
    for any integration that exposes an ``intent`` module. JARVIS's ``intent``
    package is its own local NLP layer (LocalIntentRouter) — it routes intents
    through the JARVIS conversation agent, not Home Assistant's intent registry —
    so there are no HA intent handlers to register here. This no-op exists purely
    so HA's loader doesn't raise AttributeError on the package (fixes 'Unexpected
    error during intent recognition' on HA 2026.8.x)."""
    return None
