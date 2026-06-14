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

    # Cache buster: content hash. The URL changes if and only if the JS bytes
    # change, so a stale browser/service-worker cache cannot survive an actual
    # update — and an unchanged file won't churn the URL on every reload. (mtime
    # fallback only if the file can't be read for some reason.)
    try:
        import hashlib
        with open(js_path, "rb") as _f:
            js_token = hashlib.sha1(_f.read()).hexdigest()[:10]
    except Exception:
        try:
            js_token = str(int(os.path.getmtime(js_path)))
        except Exception:
            import time
            js_token = str(int(time.time()))

    # Register static path for serving JS
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(PANEL_STATIC_URL, panel_dir, cache_headers=False)
        ])
    except Exception as exc:
        _LOGGER.debug("JARVIS panel: static path note: %s", exc)

    # Register the custom panel. Remove any existing registration first so a
    # reload always applies the fresh (content-hashed) module_url — otherwise
    # panel_custom raises "already registered" and the new URL is silently
    # dropped, which is exactly how a stale panel survives an update.
    cache_busted_url = f"{PANEL_STATIC_URL}/{PANEL_JS_FILENAME}?v={js_token}"
    try:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    except Exception:
        pass
    try:
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=PANEL_WEBCOMPONENT,
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=cache_busted_url,
            embed_iframe=False,
            require_admin=False,
        )
        _LOGGER.info("JARVIS panel registered: /%s", PANEL_URL_PATH)
        return True
    except ValueError:
        # "Overwriting panel" — already registered, this is fine
        _LOGGER.debug("JARVIS panel already registered (idempotent)")
        return True
    except Exception as exc:
        _LOGGER.error("JARVIS panel registration failed: %s", exc)
        return False


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Unregister on entry unload. Best-effort, errors non-fatal."""
    try:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        _LOGGER.info("JARVIS panel unregistered")
    except Exception as exc:
        _LOGGER.debug("JARVIS panel unregister note: %s", exc)
