"""Home Assistant config-directory path helpers for JARVIS storage."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# HA always supplies its real config dir via the live ``hass`` object, so this
# default is only the fallback for sync-only modules that have no hass. The
# ``JARVIS_CONFIG_DIR`` env override exists so a test harness (or a non-standard
# deployment) can point that fallback somewhere hermetic instead of ``/config`` —
# in production the variable is unset and behaviour is unchanged.
DEFAULT_CONFIG_DIR = Path(os.environ.get("JARVIS_CONFIG_DIR") or "/config")

_config_dir = DEFAULT_CONFIG_DIR

# Optional external home for JARVIS's own storage subtree — its .db files and the
# rest of the ``jarvis/`` directory — so an operator can keep the (potentially
# large, append-heavy) databases on a separate volume or network share instead of
# the HA config disk (issue #491c). None → default ``<config>/jarvis``.
_jarvis_storage_override: Path | None = None


def set_jarvis_storage_dir(path: Any) -> Path | None:
    """Redirect the ``jarvis/`` storage subtree to ``path`` (issue #491c). Creates
    the directory and proves it is writable first — a dead share must never
    silently break every DB open — and returns the resolved dir, or None (keeping
    the default) when the path is blank or unusable. Never raises."""
    global _jarvis_storage_override
    try:
        s = str(path or "").strip()
        if not s:
            _jarvis_storage_override = None
            return None
        p = Path(s).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".jarvis_write_test"
        probe.write_text("ok")
        probe.unlink()
        _jarvis_storage_override = p
        return p
    except Exception:
        _jarvis_storage_override = None     # unusable → fall back to default
        return None


def clear_jarvis_storage_dir() -> None:
    """Drop any storage override (used on unload and in tests)."""
    global _jarvis_storage_override
    _jarvis_storage_override = None


def jarvis_storage_dir() -> Path | None:
    """The active external storage dir, or None when the default is in use."""
    return _jarvis_storage_override


def _hass_config_path(hass: Any, *parts: str) -> Path | None:
    try:
        path_fn = getattr(getattr(hass, "config", None), "path", None)
        if callable(path_fn):
            return Path(path_fn(*parts))
    except Exception:
        return None
    return None


def has_hass_config_path(hass: Any) -> bool:
    """Whether this hass-like object exposes Home Assistant's config.path API."""
    return callable(getattr(getattr(hass, "config", None), "path", None))


def set_config_dir_from_hass(hass: Any) -> Path:
    """Remember Home Assistant's config directory for sync-only modules."""
    global _config_dir
    resolved = _hass_config_path(hass)
    if resolved is not None:
        _config_dir = resolved
    return _config_dir


def config_dir(hass: Any | None = None) -> Path:
    """Return HA's config directory, preferring the live hass object."""
    if hass is not None:
        resolved = _hass_config_path(hass)
        if resolved is not None:
            return resolved
    return _config_dir


def config_path(*parts: str, hass: Any | None = None) -> Path:
    """Return a path inside Home Assistant's config directory.

    When an external JARVIS storage dir is configured (issue #491c), the ``.db``
    files under ``jarvis/`` are redirected there (SQLite keeps each file's -wal /
    -shm sidecars alongside it automatically). Only the databases move — the small
    bootstrap files (config.json, secrets, floor-plan assets) stay in the HA config
    dir, so reading the setting that points here never depends on the share."""
    if (_jarvis_storage_override is not None and len(parts) >= 2
            and parts[0] == "jarvis" and str(parts[-1]).endswith(".db")):
        return _jarvis_storage_override.joinpath(*parts[1:])
    if hass is not None:
        resolved = _hass_config_path(hass, *parts)
        if resolved is not None:
            return resolved
    return config_dir() / Path(*parts)


def config_path_str(*parts: str, hass: Any | None = None) -> str:
    """String form for libraries that do not accept pathlib.Path."""
    return str(config_path(*parts, hass=hass))