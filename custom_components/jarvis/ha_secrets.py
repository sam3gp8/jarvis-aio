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


# ── Credential relocation (v6.83.0) ──────────────────────────────────────────
# Config keys that hold LLM credentials. In secrets.yaml they live namespaced
# under jarvis_<key> so they can't collide with another integration's secrets in
# the shared file.
CREDENTIAL_KEYS = ("api_key", "gemini_api_key", "anthropic_api_key",
                   "openai_api_key", "groq_api_key")


def secret_key_for(config_key: str) -> str:
    """secrets.yaml key name for a plaintext config credential key."""
    return "jarvis_" + str(config_key)


def overlay_credentials(config: dict, path: Path | None = None) -> dict:
    """Overlay any credential present in secrets.yaml (under jarvis_<key>) onto
    `config` — secrets.yaml wins for credentials. One file read. Mutates and
    returns `config`. Never raises."""
    try:
        secrets = _read_secrets(path)
    except Exception:
        return config
    if not secrets:
        return config
    for ck in CREDENTIAL_KEYS:
        sv = secrets.get(secret_key_for(ck))
        if sv not in (None, ""):
            config[ck] = sv
    return config


def _upsert_secret_line(text: str, key: str, value) -> str:
    """secrets.yaml text with `key: "value"` upserted: replace an existing
    top-level `key:` line if present, else append. The rest of the file is kept
    verbatim (comments, other keys, formatting)."""
    import re
    esc = str(value).replace("\\", "\\\\").replace('"', '\\"')
    line = '%s: "%s"' % (key, esc)
    pat = re.compile(r"(?m)^" + re.escape(key) + r":.*$")
    if pat.search(text):
        return pat.sub(line, text, count=1)
    sep = "" if (text == "" or text.endswith("\n")) else "\n"
    return text + sep + line + "\n"


def set_secret_sync(key: str, value, path: Path | None = None) -> bool:
    """Upsert one secret into secrets.yaml. Safe: backs up the existing file,
    writes atomically via a temp file + rename, preserves the rest of the file.
    Returns True on success. Never raises. Blocking — executor from async."""
    if not key:
        return False
    import os
    import shutil
    import tempfile
    try:
        if path is None:
            path = SECRETS_PATH
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text() if path.exists() else ""
        new_text = _upsert_secret_line(text, key, value)
        if path.exists():
            shutil.copy2(str(path), str(path) + ".jarvis.bak")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".secrets-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(new_text)
            os.replace(tmp, str(path))
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        return True
    except Exception as exc:
        _LOGGER.warning("JARVIS: could not write secret '%s': %s", key, exc)
        return False


async def relocate_plaintext_credentials(hass) -> int:
    """One-time, safe migration of plaintext LLM credentials out of the panel
    config (config.json) and into secrets.yaml.

    Per credential key present in config.json with a real value:
      - already in secrets.yaml with the SAME value -> drop the redundant copy;
      - already there but DIFFERENT -> leave both (don't guess; secrets wins on read);
      - otherwise write it, re-read to VERIFY it's durable, and only then delete
        it from config.json. If write or verify fails, config.json is left
        untouched — the key still resolves via fallback, so auth can't break.

    Returns the count of plaintext copies removed. Never raises.
    """
    from . import jarvis_config
    removed = 0
    try:
        cfg = await hass.async_add_executor_job(jarvis_config.get_all)
    except Exception:
        return 0
    for ck in CREDENTIAL_KEYS:
        val = cfg.get(ck)
        if not val:
            continue
        skey = secret_key_for(ck)
        try:
            existing = await hass.async_add_executor_job(get_secret_sync, skey, None)
            if existing == val:
                await hass.async_add_executor_job(jarvis_config.delete, ck)
                removed += 1
                continue
            if existing:
                continue  # present but different — leave both untouched
            ok = await hass.async_add_executor_job(set_secret_sync, skey, val)
            if not ok:
                continue
            check = await hass.async_add_executor_job(get_secret_sync, skey, None)
            if check == val:
                await hass.async_add_executor_job(jarvis_config.delete, ck)
                removed += 1
            # else: verify failed -> leave plaintext (resolves via fallback)
        except Exception as exc:
            _LOGGER.debug("JARVIS: relocate %s skipped: %s", ck, exc)
    if removed:
        _LOGGER.info("JARVIS: relocated %d plaintext credential(s) to secrets.yaml", removed)
    return removed
