"""
JARVIS — Centralized Configuration.

Single source of truth for all JARVIS settings.

Config file: /config/jarvis/config.json

Lifecycle (v6.45.0 — config-entry-only, no add-on):
  1. Integration loads → reads config.json
  2. Panel / Configure-dialog changes → written to config.json + in-memory cache
  3. Restart → config.json persists, in-memory reloads from it

All modules should import and use `get()` and `set()` from this module
instead of reading from entry.options or hass.data directly.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

CONFIG_PATH = Path("/config/jarvis/config.json")
_lock = threading.Lock()
_cache: dict = {}
_loaded = False
# v6.48.0 hardening: set when a hand-edited config.json couldn't be used
# (invalid JSON, or valid JSON whose top level isn't an object). The bad
# file is sidelined — never deleted — and defaults take over, so a typo in
# the file can no longer kill integration setup (which killed the panel).
last_load_error: Optional[str] = None


def _ensure_dir():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _sideline_corrupt(reason: str) -> None:
    """Move the unusable config aside (preserving the user's edits for
    recovery) and record why. Best-effort — failure to move must not stop
    startup either."""
    global last_load_error
    import time as _t
    dest = CONFIG_PATH.with_name(
        CONFIG_PATH.name + f".corrupt-{int(_t.time())}")
    try:
        CONFIG_PATH.rename(dest)
        last_load_error = f"{reason} — file preserved at {dest.name}"
    except Exception:
        last_load_error = f"{reason} — could not sideline file"
    _LOGGER.error(
        "JARVIS config.json unusable (%s). Starting with defaults; "
        "your file was kept for recovery. Fix the JSON and settings return.",
        last_load_error,
    )


def load() -> dict:
    """Load config from disk into cache. A file that can't be parsed, or
    parses to something that isn't a JSON object, is sidelined instead of
    crashing setup (v6.48.0 — a hand-edited config took the panel down)."""
    global _cache, _loaded, last_load_error
    _ensure_dir()
    with _lock:
        last_load_error = None
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    _cache = data
                else:
                    _cache = {}
                    _sideline_corrupt(
                        f"top level is {type(data).__name__}, expected object")
            else:
                _cache = {}
            _loaded = True
            _LOGGER.info(
                "JARVIS config loaded: %d keys from %s",
                len(_cache), CONFIG_PATH,
            )
        except json.JSONDecodeError as exc:
            _cache = {}
            _loaded = True
            _sideline_corrupt(f"invalid JSON: {exc}")
        except Exception as exc:
            _LOGGER.warning("JARVIS config load error: %s", exc)
            _cache = {}
            _loaded = True
    return dict(_cache)


def _cache_dict() -> dict:
    """Belt-and-braces: the cache is ALWAYS a dict at point of use, even if
    something replaced it at runtime."""
    global _cache
    if not isinstance(_cache, dict):
        _LOGGER.error("JARVIS config cache was %s — resetting to {}",
                      type(_cache).__name__)
        _cache = {}
    return _cache


def save() -> None:
    """Persist current cache to disk."""
    _ensure_dir()
    with _lock:
        try:
            # Write atomically via temp file
            tmp = CONFIG_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(_cache, f, indent=2, default=str)
            tmp.replace(CONFIG_PATH)
        except Exception as exc:
            _LOGGER.warning("JARVIS config save error: %s", exc)


def get(key: str, default: Any = None) -> Any:
    """Read a config value. Loads from disk on first access."""
    global _loaded
    if not _loaded:
        load()
    with _lock:
        return _cache_dict().get(key, default)


def get_all() -> dict:
    """Return a copy of the entire config."""
    global _loaded
    if not _loaded:
        load()
    with _lock:
        return dict(_cache_dict())


def set(key: str, value: Any) -> None:
    """Set a config value and persist to disk."""
    global _loaded
    if not _loaded:
        load()
    with _lock:
        _cache_dict()[key] = value
    save()
    _LOGGER.debug("JARVIS config set: %s = %s", key, str(value)[:100])


def set_many(updates: dict) -> None:
    """Set multiple config values and persist."""
    global _loaded
    if not _loaded:
        load()
    with _lock:
        _cache_dict().update(updates)
    save()
    _LOGGER.debug("JARVIS config set_many: %d keys", len(updates))


def delete(key: str) -> None:
    """Remove a config key."""
    global _loaded
    if not _loaded:
        load()
    with _lock:
        _cache_dict().pop(key, None)
    save()


def init_from_addon(addon_options: dict) -> None:
    """
    Called by bootstrap/run.sh on addon startup.
    Writes addon options to config.json, but only for keys that
    aren't already set (preserves panel overrides).
    """
    global _loaded
    if not _loaded:
        load()

    updated = 0
    with _lock:
        for key, value in addon_options.items():
            if key not in _cache_dict():
                _cache_dict()[key] = value
                updated += 1
            # Always update API keys (user might change them in addon config)
            elif key in ("groq_api_key", "api_key", "gemini_api_key",
                         "anthropic_api_key", "openai_api_key"):
                if value and value != _cache_dict().get(key):
                    _cache_dict()[key] = value
                    updated += 1

    if updated:
        save()
        _LOGGER.info(
            "JARVIS config: merged %d addon options (preserved %d panel overrides)",
            updated, len(addon_options) - updated,
        )


def init_from_entry(entry_data: dict, entry_options: dict) -> None:
    """
    Called when the HA integration loads. Backfills any settings
    from the entry that aren't in config.json yet.
    """
    global _loaded
    if not _loaded:
        load()

    merged = {**entry_data, **entry_options}
    updated = 0
    with _lock:
        for key, value in merged.items():
            if key not in _cache_dict() and value:
                _cache_dict()[key] = value
                updated += 1

    if updated:
        save()
        _LOGGER.debug(
            "JARVIS config: backfilled %d keys from integration entry",
            updated,
        )
