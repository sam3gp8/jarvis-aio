"""JARVIS intent layer: local NLP command routing and feedback."""
from __future__ import annotations

from .intent_router import (
    EVENT_FEEDBACK_WINDOW,
    LocalIntentRouter,
    is_affirmative,
    match_intent,
)

__all__ = [
    "LocalIntentRouter",
    "match_intent",
    "is_affirmative",
    "EVENT_FEEDBACK_WINDOW",
]
