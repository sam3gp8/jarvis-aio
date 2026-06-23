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
]
