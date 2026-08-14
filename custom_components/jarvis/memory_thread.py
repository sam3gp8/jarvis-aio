"""
JARVIS — conversation memory threading (v6.86.0).

Reads a bounded slice of recent cross-session conversation history back so a
fresh conversation picks up where you left off — continuity across session
boundaries and restarts, not just the 20-message in-session window.

Kept deliberately dependency-light (no Home Assistant entity imports) so it's
easy to test and so conversation.py just seeds its in-session window from
load_recent(). Turns are persisted by database.save_message; this reads them via
get_recent_messages, filters to user/assistant turns, truncates long ones, and
caps to the last N. Never raises — a failure yields no seed, never a broken turn.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

DEFAULT_ENABLED = True
DEFAULT_HOURS = 48
DEFAULT_MAX = 12
_CHAR_CAP = 600


def config() -> tuple:
    """(enabled, hours, limit) from jarvis_config, with defaults."""
    try:
        from . import jarvis_config
        enabled = bool(jarvis_config.get("memory_threading_enabled", DEFAULT_ENABLED))
        hours = int(jarvis_config.get("memory_threading_hours", DEFAULT_HOURS) or DEFAULT_HOURS)
        limit = int(jarvis_config.get("memory_threading_max", DEFAULT_MAX) or DEFAULT_MAX)
        return enabled, max(1, hours), max(1, limit)
    except Exception:
        return DEFAULT_ENABLED, DEFAULT_HOURS, DEFAULT_MAX


def shape_history(rows, limit: int = DEFAULT_MAX, char_cap: int = _CHAR_CAP) -> list:
    """Filter DB rows to {role, content} user/assistant turns, truncate long
    turns, cap to the last `limit`. Pure — junk rows are skipped."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        role = r.get("role")
        content = (r.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            if len(content) > char_cap:
                content = content[:char_cap].rstrip() + "\u2026"
            out.append({"role": role, "content": content})
    return out[-limit:] if limit else out


async def load_recent(hass, hours: int = DEFAULT_HOURS, limit: int = DEFAULT_MAX) -> list:
    """Recent cross-session turns to seed a fresh conversation with. Reads the
    DB in the executor. Never raises."""
    try:
        from .database import get_recent_messages
        rows = await hass.async_add_executor_job(get_recent_messages, hours, None, limit)
        return shape_history(rows, limit)
    except Exception as exc:
        _LOGGER.debug("memory_thread load_recent failed: %s", exc)
        return []
