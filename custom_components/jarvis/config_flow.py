"""
JARVIS Config Flow.

HACS integration: the config flow is the primary setup path.
  1. Manual entry of a cloud API key OR a local LLM endpoint (the common case).
  2. If /config/jarvis/config.json already exists (a previous install — the
     panel's runtime config survives integration removal), auto-imports it so
     a re-install is zero-touch.
  3. Options flow: a 4-step Configure dialog (Core, Routing, Observer, Identity)
     for the common settings — the full set still lives in the JARVIS panel.

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
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_HONORIFIC,
    CONF_MODEL,
    CONF_DIRECTIVE,
    CONF_DIRECTIVE_PRESET,
    CONF_USE_HASS_API,
    CONF_BEDROOM_AREAS,
    CONF_BROADCAST_GROUP,
    CONF_NOTIFY_SERVICE,
    CONF_OBSERVER_ENABLED,
    CONF_GEMINI_API_KEY,
    CONF_CLASSIFIER_MODEL,
    CONF_REASONING_MODEL,
    CONF_REVIEW_MODEL,
    CONF_OBSERVER_QUIET_START,
    CONF_OBSERVER_QUIET_END,
    DEFAULT_HONORIFIC,
    DEFAULT_MODEL,
    DEFAULT_DIRECTIVE_PRESET,
    DEFAULT_CLASSIFIER_MODEL,
    DEFAULT_REASONING_MODEL,
    DEFAULT_REVIEW_MODEL,
    DEFAULT_OBSERVER_QUIET_START,
    DEFAULT_OBSERVER_QUIET_END,
    DIRECTIVE_PRESETS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# The panel's runtime config — survives integration removal, so a re-install
# can pick everything back up without re-entry. (v6.45.0: the legacy add-on
# path /config/jarvis_config.json is no longer read.)
_RUNTIME_CONFIG_PATH = "/config/jarvis/config.json"


def _find_config() -> dict | None:
    """Read an existing runtime config, if one with a usable LLM exists."""
    try:
        if os.path.exists(_RUNTIME_CONFIG_PATH):
            with open(_RUNTIME_CONFIG_PATH) as f:
                data = json.load(f)
            if data.get(CONF_API_KEY) or data.get("groq_api_key"):
                return data
    except Exception:
        pass
    return None


class JarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle JARVIS config flow — auto-imports an existing runtime config."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> dict:
        """
        UI-driven setup. Tries auto-import first; falls back to
        manual API key entry only if no config file exists.
        """
        # Try auto-import from an existing runtime config (re-install case)
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
        """Create the entry from an existing runtime config (re-install)."""
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

        _LOGGER.info("JARVIS: auto-configuring from existing runtime config (provider=%s%s)",
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
    Full options flow — JARVIS is configurable from Settings → Devices &
    Services → JARVIS → Configure (in addition to the in-app panel). Steps:
      1. Core      — persona/honorific, directive, conversation model, home control
      2. Routing   — bedroom areas, broadcast group, phone notify service
      3. Observer  — proactive awareness (Gemini key + model tiers + quiet hours)
      4. Identity  — per-person recognition (voice-fingerprint tier is GPU-only)
    Collected values are written straight into jarvis_config (the runtime source
    of truth the panel and modules read) and persisted as entry options, which
    triggers a reload so they take effect immediately.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._data: dict[str, Any] = {}

    def _cur(self, key: str, default: Any = None) -> Any:
        """Current value: runtime config first, then entry options/data."""
        try:
            from . import jarvis_config
            val = jarvis_config.get(key, None)
            if val not in (None, ""):
                return val
        except Exception:
            pass
        return self._entry.options.get(key, self._entry.data.get(key, default))

    def _sv(self, key: str, default: Any = None) -> dict:
        """suggested_value wrapper to pre-fill a field with its current value."""
        return {"suggested_value": self._cur(key, default)}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict:
        """Step 1 — Core."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_routing()
        schema = vol.Schema({
            vol.Optional(CONF_HONORIFIC, description=self._sv(CONF_HONORIFIC, DEFAULT_HONORIFIC)):
                selector.TextSelector(),
            vol.Optional(CONF_DIRECTIVE_PRESET,
                         description=self._sv(CONF_DIRECTIVE_PRESET, DEFAULT_DIRECTIVE_PRESET)):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=list(DIRECTIVE_PRESETS.keys()),
                    mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_DIRECTIVE, description=self._sv(CONF_DIRECTIVE, "")):
                selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_MODEL, description=self._sv(CONF_MODEL, DEFAULT_MODEL)):
                selector.TextSelector(),
            vol.Optional(CONF_USE_HASS_API, description=self._sv(CONF_USE_HASS_API, True)):
                selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_routing(self, user_input: dict[str, Any] | None = None) -> dict:
        """Step 2 — Routing."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_observer()
        schema = vol.Schema({
            vol.Optional(CONF_BEDROOM_AREAS, description=self._sv(CONF_BEDROOM_AREAS, [])):
                selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
            vol.Optional(CONF_BROADCAST_GROUP, description=self._sv(CONF_BROADCAST_GROUP, "")):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
            vol.Optional(CONF_NOTIFY_SERVICE, description=self._sv(CONF_NOTIFY_SERVICE, "")):
                selector.TextSelector(),
        })
        return self.async_show_form(step_id="routing", data_schema=schema)

    async def async_step_observer(self, user_input: dict[str, Any] | None = None) -> dict:
        """Step 3 — Observer."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_identity()
        schema = vol.Schema({
            vol.Optional(CONF_OBSERVER_ENABLED, description=self._sv(CONF_OBSERVER_ENABLED, False)):
                selector.BooleanSelector(),
            vol.Optional(CONF_GEMINI_API_KEY, description=self._sv(CONF_GEMINI_API_KEY, "")):
                selector.TextSelector(selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD)),
            vol.Optional(CONF_CLASSIFIER_MODEL,
                         description=self._sv(CONF_CLASSIFIER_MODEL, DEFAULT_CLASSIFIER_MODEL)):
                selector.TextSelector(),
            vol.Optional(CONF_REASONING_MODEL,
                         description=self._sv(CONF_REASONING_MODEL, DEFAULT_REASONING_MODEL)):
                selector.TextSelector(),
            vol.Optional(CONF_REVIEW_MODEL,
                         description=self._sv(CONF_REVIEW_MODEL, DEFAULT_REVIEW_MODEL)):
                selector.TextSelector(),
            vol.Optional(CONF_OBSERVER_QUIET_START,
                         description=self._sv(CONF_OBSERVER_QUIET_START, DEFAULT_OBSERVER_QUIET_START)):
                selector.TextSelector(),
            vol.Optional(CONF_OBSERVER_QUIET_END,
                         description=self._sv(CONF_OBSERVER_QUIET_END, DEFAULT_OBSERVER_QUIET_END)):
                selector.TextSelector(),
        })
        return self.async_show_form(step_id="observer", data_schema=schema)

    async def async_step_identity(self, user_input: dict[str, Any] | None = None) -> dict:
        """Step 4 — Identity (per-person recognition; voice tier needs a GPU)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self._save()
        schema = vol.Schema({
            vol.Optional("identity_enabled", description=self._sv("identity_enabled", True)):
                selector.BooleanSelector(),
            vol.Optional("identity_voice_fingerprint",
                         description=self._sv("identity_voice_fingerprint", False)):
                selector.BooleanSelector(),
            vol.Optional("voice_recognition_source",
                         description=self._sv("voice_recognition_source", "")):
                selector.TextSelector(),
            vol.Optional("voice_recognition_auto_enroll",
                         description=self._sv("voice_recognition_auto_enroll", True)):
                selector.BooleanSelector(),
            vol.Optional("identity_min_confidence",
                         description=self._sv("identity_min_confidence", 0.45)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.05, mode=selector.NumberSelectorMode.SLIDER)),
        })
        return self.async_show_form(step_id="identity", data_schema=schema)

    async def _save(self) -> dict:
        """Persist all collected values to the runtime config + entry options."""
        try:
            from . import jarvis_config
            await self.hass.async_add_executor_job(jarvis_config.set_many, dict(self._data))
        except Exception as exc:
            _LOGGER.warning("JARVIS options: jarvis_config write failed: %s", exc)
        return self.async_create_entry(title="", data={**self._entry.options, **self._data})
