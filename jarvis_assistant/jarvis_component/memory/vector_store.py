"""Local chronological/semantic memory for JARVIS.

``LocalSemanticMemory`` keeps a rolling, on-disk JSON buffer of past events
(infrastructure faults, notable announcements) capped at the most recent 1000
entries, and supports keyword recall so JARVIS can say "this has happened before"
when a fault recurs.

File I/O is synchronous and lock-guarded; callers on the event loop should invoke
``commit_event`` / ``query_related_faults`` via ``hass.async_add_executor_job``.
Persisted alongside the integration's other state under /config/jarvis/.
"""
from __future__ import annotations

import json
import logging
import os
import time
from threading import Lock

_LOGGER = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = "/config/jarvis/semantic_memory.json"
MAX_ENTRIES = 1000


class LocalSemanticMemory:
    """A bounded, persisted event log with keyword-overlap recall."""

    def __init__(self, path: str = DEFAULT_MEMORY_PATH, *, max_entries: int = MAX_ENTRIES) -> None:
        self.path = path
        self.max_entries = max_entries
        self._lock = Lock()

    # ── Persistence (guarded, corruption-tolerant) ────────────────────────
    def _load(self) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("semantic memory unreadable (%s) — starting fresh", exc)
            return []

    def _save(self, entries: list[dict]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=False)
            os.replace(tmp, self.path)  # atomic on POSIX
        except OSError as exc:
            _LOGGER.error("Failed to persist semantic memory: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────
    def commit_event(self, entry_text: str, tags: list[str] | None = None) -> dict:
        """Append an event and trim to the most recent ``max_entries``.

        Returns the stored entry dict (text, tags, ts).
        """
        entry = {
            "text": str(entry_text),
            "tags": [str(t).lower() for t in (tags or [])],
            "ts": time.time(),
        }
        with self._lock:
            entries = self._load()
            entries.append(entry)
            if len(entries) > self.max_entries:
                entries = entries[-self.max_entries:]
            self._save(entries)
        return entry

    def query_related_faults(self, target_keywords: list[str]) -> list[dict]:
        """Return past entries whose text or tags overlap any target keyword
        (case-insensitive substring match), oldest → newest."""
        keywords = [str(k).lower() for k in (target_keywords or []) if str(k).strip()]
        if not keywords:
            return []
        with self._lock:
            entries = self._load()
        out: list[dict] = []
        for entry in entries:
            haystack = (
                str(entry.get("text", "")).lower()
                + " "
                + " ".join(str(t).lower() for t in entry.get("tags", []))
            )
            if any(kw in haystack for kw in keywords):
                out.append(entry)
        return out

    def recent(self, limit: int = 10) -> list[dict]:
        """The most recent ``limit`` entries (newest last)."""
        with self._lock:
            return self._load()[-limit:]
