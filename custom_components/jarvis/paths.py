"""Home Assistant config-directory path helpers for JARVIS storage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path("/config")

_config_dir = DEFAULT_CONFIG_DIR


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
    """Return a path inside Home Assistant's config directory."""
    if hass is not None:
        resolved = _hass_config_path(hass, *parts)
        if resolved is not None:
            return resolved
    return config_dir() / Path(*parts)


def config_path_str(*parts: str, hass: Any | None = None) -> str:
    """String form for libraries that do not accept pathlib.Path."""
    return str(config_path(*parts, hass=hass))