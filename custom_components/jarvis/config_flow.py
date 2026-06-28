"""
JARVIS Config Flow.

HACS integration: the config flow is the primary setup path.
  1. Manual entry of a cloud API key OR a local LLM endpoint (the common case).
  2. If migrating from the legacy add-on, auto-imports an existing
     /config/jarvis/config.json so nothing is re-entered.
  3. Options flow redirects to the JARVIS panel (Settings tab).

All runtime configuration is managed via the JARVIS panel and
persisted by jarvis_config.py. The HA config entry is just the
bootstrap shell that registers the conversation platform.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_API_KEY,
    CONF_HONORIFIC,
    CONF_MODEL,
    DEFAULT_HONORIFIC,
    DEFAULT_MODEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Config file locations (new and legacy)
_CONFIG_PATHS = [
    "/config/jarvis/config.json",
    "/config/jarvis_config.json",
]


def _find_config() -> dict | None:
    """Find and read JARVIS config from known paths."""
    for path in _CONFIG_PATHS:
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                if data.get(CONF_API_KEY) or data.get("groq_api_key"):
                    return data
        except Exception:
            pass
    return None


class JarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle JARVIS config flow — auto-imports from addon config."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> dict:
        """
        UI-driven setup. Tries auto-import first; falls back to
        manual API key entry only if no config file exists.
        """
        # Try auto-import from addon config
        cfg = await self.hass.async_add_executor_job(_find_config)
        if cfg:
            return await self.async_step_import(cfg)

        # Manual fallback — a cloud API key OR a local LLM endpoint.
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()
            base_url = user_input.get("llm_base_url", "").strip()
            if api_key or base_url:
                # No cloud key + a local URL ⇒ run a local model (Ollama).
                provider = "groq" if api_key else "ollama"
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="JARVIS",
                    data={
                        CONF_API_KEY: api_key,
                        CONF_MODEL: user_input.get(CONF_MODEL, DEFAULT_MODEL),
                        CONF_HONORIFIC: user_input.get(CONF_HONORIFIC, DEFAULT_HONORIFIC),
                        "llm_provider": provider,
                        "llm_base_url": base_url,
                        "schema_version": 7,
                    },
                )
            errors["base"] = "need_llm"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(CONF_API_KEY, default=""): str,
                vol.Optional("llm_base_url", default=""): str,
                vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): str,
                vol.Optional(CONF_HONORIFIC, default=DEFAULT_HONORIFIC): str,
            }),
            errors=errors,
            description_placeholders={
                "note": "Enter a cloud API key (e.g. Groq), OR leave it blank and "
                        "enter a local LLM URL (e.g. http://homeassistant.local:11434/v1) "
                        "to run Ollama with no cloud account. Everything else is "
                        "configured later in the JARVIS panel → Settings.",
            },
        )

    async def async_step_import(
        self, import_data: dict[str, Any],
    ) -> dict:
        """Auto-import from addon config file."""
        api_key = (
            import_data.get(CONF_API_KEY)
            or import_data.get("groq_api_key", "")
        ).strip()
        base_url = (import_data.get("llm_base_url", "") or "").strip()
        provider = import_data.get("llm_provider", "groq")
        local_ok = bool(base_url) or provider in ("ollama", "custom")

        # An LLM is required, but a local model counts: proceed if we have either
        # a cloud key OR a local endpoint (provider=ollama/custom, or a base_url).
        if not api_key and not local_ok:
            _LOGGER.warning("JARVIS: config found but no API key and no local LLM")
            return self.async_abort(reason="import_failed")

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(
            updates={CONF_API_KEY: api_key}
        )

        _LOGGER.info("JARVIS: auto-configuring from addon config (provider=%s%s)",
                     provider, ", local" if (not api_key and local_ok) else "")
        return self.async_create_entry(
            title="JARVIS",
            data={
                CONF_API_KEY: api_key,
                CONF_MODEL: import_data.get(CONF_MODEL, import_data.get("model", DEFAULT_MODEL)),
                CONF_HONORIFIC: import_data.get(CONF_HONORIFIC, import_data.get("honorific", DEFAULT_HONORIFIC)),
                "llm_provider": provider,
                "llm_base_url": base_url,
                "schema_version": 7,
            },
            options={k: v for k, v in import_data.items()
                     if k not in (CONF_API_KEY, "groq_api_key", "schema_version")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "JarvisOptionsFlow":
        return JarvisOptionsFlow(config_entry)


class JarvisOptionsFlow(OptionsFlow):
    """
    Options flow — redirects to the JARVIS panel.
    All configuration is managed via the panel Settings tab.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ) -> dict:
        if user_input is not None:
            return self.async_create_entry(title="", data=self._entry.options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
            description_placeholders={
                "message": "All JARVIS settings are managed in the JARVIS panel. "
                           "Go to the JARVIS sidebar → Settings tab to configure "
                           "satellites, speakers, observer, floor plan, and more.",
            },
        )
