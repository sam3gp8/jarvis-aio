"""
JARVIS Panel Registration (v5.7.00).

Registers the JARVIS custom panel with HA's frontend. Called from
async_setup_entry. Idempotent — safe to call repeatedly on entry reload.

Session 1: static asset serving + panel_custom registration.
Session 2+: WebSocket API for live data.
"""
from __future__ import annotations

import logging
import os
from typing import Final

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH:       Final = "jarvis"
PANEL_TITLE:          Final = "JARVIS"
PANEL_ICON:           Final = "mdi:robot-outline"
PANEL_WEBCOMPONENT:   Final = "jarvis-panel"
PANEL_STATIC_URL:     Final = "/jarvis_panel_static"
PANEL_JS_FILENAME:    Final = "jarvis-panel.js"

# Command Center — the operational HUD (separate sidebar entry, same static dir)
CMD_URL_PATH:         Final = "jarvis-command"
CMD_TITLE:            Final = "Command Center"
CMD_ICON:             Final = "mdi:hexagon-multiple-outline"
CMD_WEBCOMPONENT:     Final = "jarvis-command"
CMD_JS_FILENAME:      Final = "jarvis-command.js"


def _hash_file(path: str) -> str:
    """Content hash for cache-busting; mtime/time fallback if unreadable."""
    try:
        import hashlib
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except Exception:
        try:
            return str(int(os.path.getmtime(path)))
        except Exception:
            import time
            return str(int(time.time()))


async def _register_one(
    hass: HomeAssistant, panel_dir: str, *,
    webcomponent: str, url_path: str, title: str, icon: str, js_filename: str,
) -> bool:
    """Register a single custom panel served from the shared static dir."""
    js_path = os.path.join(panel_dir, js_filename)
    if not os.path.isfile(js_path):
        _LOGGER.error("JARVIS panel: JS file not found at %s", js_path)
        return False
    module_url = f"{PANEL_STATIC_URL}/{js_filename}?v={_hash_file(js_path)}"
    try:
        frontend.async_remove_panel(hass, url_path)
    except Exception:
        pass
    try:
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=webcomponent,
            frontend_url_path=url_path,
            sidebar_title=title,
            sidebar_icon=icon,
            module_url=module_url,
            embed_iframe=False,
            require_admin=False,
        )
        _LOGGER.info("JARVIS panel registered: /%s", url_path)
        return True
    except ValueError:
        _LOGGER.debug("JARVIS panel /%s already registered (idempotent)", url_path)
        return True
    except Exception as exc:
        _LOGGER.error("JARVIS panel registration failed (/%s): %s", url_path, exc)
        return False


async def async_register_panel(hass: HomeAssistant) -> bool:
    """
    Register the JARVIS sidebar panel.

    Returns True on success, False on error.
    Uses panel_custom.async_register_panel which handles idempotency
    internally (raises ValueError on duplicate, which we catch).
    """
    panel_dir = os.path.join(os.path.dirname(__file__), "frontend")
    if not os.path.isdir(panel_dir):
        _LOGGER.error("JARVIS panel: frontend dir not found at %s", panel_dir)
        return False

    js_path = os.path.join(panel_dir, PANEL_JS_FILENAME)
    if not os.path.isfile(js_path):
        _LOGGER.error("JARVIS panel: JS file not found at %s", js_path)
        return False

    # Register static path for serving the frontend dir (both panels' JS live here)
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(PANEL_STATIC_URL, panel_dir, cache_headers=False)
        ])
    except Exception as exc:
        _LOGGER.debug("JARVIS panel: static path note: %s", exc)

    # Clean up the old separate Command Center panel from <=6.14.x — it's now
    # folded into the main JARVIS panel, so the standalone entry must go.
    try:
        frontend.async_remove_panel(hass, CMD_URL_PATH)
    except Exception:
        pass

    # Single combined panel: the JARVIS Command Center (dashboard + cameras +
    # 3D residence + settings + logs all in one).
    main_ok = await _register_one(
        hass, panel_dir,
        webcomponent=PANEL_WEBCOMPONENT, url_path=PANEL_URL_PATH,
        title=PANEL_TITLE, icon=PANEL_ICON, js_filename=PANEL_JS_FILENAME,
    )
    return main_ok


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Unregister the panel on entry unload. Best-effort, errors non-fatal."""
    try:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        _LOGGER.info("JARVIS panel unregistered")
    except Exception as exc:
        _LOGGER.debug("JARVIS panel /%s unregister note: %s", PANEL_URL_PATH, exc)
