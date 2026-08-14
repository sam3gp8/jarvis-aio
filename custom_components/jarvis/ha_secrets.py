"""
JARVIS — Home Assistant secrets.yaml resolver (v6.81.0).

Credentials and passwords belong in Home Assistant's secrets.yaml, not in the
plaintext panel config (/config/jarvis/config.json). This module is the single,
read-only bridge to that file: it resolves a named secret from
/config/secrets.yaml and does nothing else.

Read-only by design. This module NEVER writes to secrets.yaml — the user owns
that file. It tolerates a missing or malformed file (returns the default and
logs, never raises), so a secrets typo can't take integration setup down — the
same "sideline, don't crash" discipline jarvis_config learned the hard way.

Blocking file I/O is offloaded to HA's executor via async_get_secret; a bare
synchronous reader is exposed for the executor job and for tests.

(v6.81.0 lands the resolver + its first consumer, the mail agent. Migrating the
existing LLM/observer keys onto it is a separate, isolated change.)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

SECRETS_PATH = Path("/config/secrets.yaml")


def _read_secrets(path: Path | None = None) -> dict:
    """Parse secrets.yaml into a dict.

    Missing file → {} (not an error — many installs have none). Malformed YAML,
    or a top level that isn't a mapping → {} plus a warning. Never raises.
    Blocking — call via the executor from async code.

    `path` is resolved at call time (default SECRETS_PATH), not bound at
    definition — otherwise a test monkeypatching SECRETS_PATH wouldn't take,
    the same default-binding trap the DB layer hit.
    """
    if path is None:
        path = SECRETS_PATH
    try:
        if not path.exists():
            return {}
        import yaml  # PyYAML ships with Home Assistant core
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
        _LOGGER.warning(
            "JARVIS: %s top level is %s, expected a mapping — ignoring",
            path, type(data).__name__,
        )
        return {}
    except Exception as exc:  # yaml.YAMLError, OSError, UnicodeDecodeError, …
        _LOGGER.warning("JARVIS: could not read %s: %s", path, exc)
        return {}


def get_secret_sync(key: str, default: Any = None,
                    path: Path | None = None) -> Any:
    """Synchronous secret lookup (blocking).

    Prefer async_get_secret from async code so the read runs off the event loop.
    An empty string in secrets.yaml is treated as unset (returns `default`).
    `path` resolves at call time (default SECRETS_PATH).
    """
    if not key:
        return default
    val = _read_secrets(path).get(key, default)
    return val if val not in (None, "") else default


async def async_get_secret(hass, key: str, default: Any = None) -> Any:
    """Resolve a named secret from secrets.yaml, off the event loop.

    Returns `default` when the key is absent/empty or the file is unusable.
    Never raises.
    """
    if hass is None:
        return get_secret_sync(key, default)
    try:
        return await hass.async_add_executor_job(get_secret_sync, key, default)
    except Exception as exc:
        _LOGGER.debug("JARVIS async_get_secret(%s) failed: %s", key, exc)
        return default
