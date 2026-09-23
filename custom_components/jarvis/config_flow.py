"""
JARVIS Config Flow.

HACS integration: the config flow is the primary setup path.
  1. Pick and configure one or more LLM providers (each gets its own stored
     key so multiple providers stay usable at once — e.g. Groq for the Main
     Agent, Gemini for the Observer tiers), then finish setup by choosing
     which configured provider drives the Main Agent.
  2. If /config/jarvis/config.json already exists (a previous install — the
     panel's runtime config survives integration removal), auto-imports it so
     a re-install is zero-touch.
  3. Options flow: a Configure dialog (LLM, Core, Routing, Observer, Identity)
     for the common settings — the full set still lives in the JARVIS panel.

All runtime configuration is managed via the JARVIS panel and
persisted by jarvis_config.py. The HA config entry is just the
bootstrap shell that registers the conversation platform.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .paths import config_path
from .const import (
    CONF_API_KEY,
    CONF_HONORIFIC,
    CONF_MODEL,
    CONF_DIRECTIVE,
    CONF_DIRECTIVE_PRESET,
    CONF_USE_HASS_API,
    CONF_WEB_RESEARCH_LLM_FALLBACK,
    DEFAULT_WEB_RESEARCH_LLM_FALLBACK,
    CONF_BEDROOM_AREAS,
    CONF_BROADCAST_GROUP,
    CONF_NOTIFY_SERVICE,
    CONF_OBSERVER_ENABLED,
    CONF_GEMINI_API_KEY,
    CONF_CUSTOM_BASE_URL,
    CONF_OLLAMA_BASE_URL,
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
_RUNTIME_CONFIG_PATH = str(config_path("jarvis", "config.json"))

# Providers offered in the "add a provider" menu — ollama needs no key, every
# other one gets its own dedicated credential field (PROVIDER_API_KEY_FIELDS)
# so configuring one never overwrites another's key.
_PROVIDER_STEPS = ("groq", "openai", "anthropic", "gemini", "custom", "ollama")


async def _validate_provider_key(hass, provider: str, api_key: str) -> str | None:
    """Validate a cloud provider key without issuing a chat completion."""
    try:
        from .websocket import _fetch_models
        models = await _fetch_models(hass, provider, api_key, "")
    except Exception as exc:
        from .llm_provider import _classify_conn_error
        return _classify_conn_error(exc)
    return None if models else "unknown"


def _legacy_provider_key(data: dict[str, Any], provider: str) -> str:
    """The selected provider's legacy plaintext runtime credential, if any."""
    from . import ha_secrets
    return ha_secrets.get_legacy_provider_key(data, provider)


async def _fetch_available_models(hass, provider: str, api_key: str = "", base_url: str = "") -> list[str]:
    """Fetch live model IDs for a provider, returning [] on any lookup error."""
    try:
        from .websocket import _fetch_models
        return await _fetch_models(hass, provider, api_key, base_url)
    except Exception as exc:
        _LOGGER.warning("JARVIS config flow: model list for %s failed: %s", provider, exc)
        return []


def _find_config(runtime_config_path: str | None = None, secrets_path: str | None = None) -> dict | None:
    """Read an existing runtime config, if one with a usable LLM exists.
    Credentials live only in secrets.yaml (never config.json), so "usable"
    means either a provider secret is present there, or a local provider
    (ollama/custom) is set up with a base URL and needs no key."""
    try:
        runtime_config_path = runtime_config_path or _RUNTIME_CONFIG_PATH
        if not os.path.exists(runtime_config_path):
            return None
        with open(runtime_config_path) as f:
            data = json.load(f)
    except Exception:
        return None
    from . import ha_secrets
    from .const import resolve_provider_base_url
    provider = data.get("llm_provider", "groq")
    secrets_file = str(secrets_path) if secrets_path else None
    try:
        has_secret = bool(
            ha_secrets.get_stored_provider_key_sync(
                provider, Path(secrets_file) if secrets_file else None
            )
        )
    except TypeError:
        has_secret = bool(ha_secrets.get_stored_provider_key_sync(provider))
    # A legacy install may still have its credential in config.json (not yet
    # migrated to secrets.yaml). Treat that as usable too, so the entry gets
    # created and async_setup_entry()'s migration can relocate it, instead of
    # sending an already-configured user through fresh setup.
    has_legacy_secret = bool(_legacy_provider_key(data, provider))
    local_ok = provider == "ollama" or (
        provider == "custom" and bool(resolve_provider_base_url(data, "custom"))
    )
    return data if (has_secret or has_legacy_secret or local_ok) else None


class JarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle JARVIS config flow — auto-imports an existing runtime config."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        # provider -> {"api_key": ..., "llm_base_url": ...} for whatever was
        # configured this flow session (not yet written anywhere until Finish).
        self._provider_keys: dict[str, dict[str, str]] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> dict:
        """
        UI-driven setup. Tries auto-import first; falls back to the
        provider-picker menu only if no config file exists.
        """
        # Try auto-import from an existing runtime config (re-install case)
        runtime_config_path = str(config_path("jarvis", "config.json", hass=self.hass))
        secrets_path = str(config_path("secrets.yaml", hass=self.hass))
        cfg = await self.hass.async_add_executor_job(_find_config, runtime_config_path, secrets_path)
        if cfg:
            return await self.async_step_import(cfg)
        return await self.async_step_provider_menu()

    async def async_step_provider_menu(self, user_input: dict[str, Any] | None = None) -> dict:
        """Landing menu — add a provider's key, or finish once at least one is set."""
        if self._provider_keys:
            note = ("Configured so far: " + ", ".join(sorted(self._provider_keys)) +
                    ". Add another provider, or choose Finish setup.")
        else:
            note = ("Pick a provider to add its API key (or URL for Ollama/Custom). "
                    "At least one is required before you can finish setup.")
        return self.async_show_menu(
            step_id="provider_menu",
            menu_options=list(_PROVIDER_STEPS) + ["finish"],
            description_placeholders={"note": note},
        )

    async def _async_step_simple_key(self, provider: str, user_input: dict[str, Any] | None) -> dict:
        """Shared body for the single-API-key-field provider steps."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if not api_key:
                errors["base"] = "need_llm"
            else:
                conn_err = await _validate_provider_key(self.hass, provider, api_key)
                if conn_err:
                    errors["base"] = conn_err
                else:
                    self._provider_keys[provider] = {"api_key": api_key}
                    return await self.async_step_provider_menu()
        schema = vol.Schema({
            vol.Required(CONF_API_KEY): selector.TextSelector(selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id=provider, data_schema=schema, errors=errors)

    async def async_step_groq(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("groq", user_input)

    async def async_step_openai(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("openai", user_input)

    async def async_step_anthropic(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("anthropic", user_input)

    async def async_step_gemini(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("gemini", user_input)

    async def async_step_custom(self, user_input: dict[str, Any] | None = None) -> dict:
        """Any OpenAI-compatible endpoint — needs a URL; the key is optional
        (some self-hosted servers don't require one)."""
        from .llm_provider import test_connection

        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            base_url = (user_input.get(CONF_CUSTOM_BASE_URL) or "").strip()
            if not base_url:
                errors["base"] = "need_llm"
            else:
                conn_err = await test_connection(self.hass, "custom", api_key, DEFAULT_MODEL, base_url)
                if conn_err:
                    errors["base"] = conn_err
                else:
                    self._provider_keys["custom"] = {"api_key": api_key, CONF_CUSTOM_BASE_URL: base_url}
                    return await self.async_step_provider_menu()
        schema = vol.Schema({
            vol.Required(CONF_CUSTOM_BASE_URL): str,
            vol.Optional(CONF_API_KEY, default=""): selector.TextSelector(selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="custom", data_schema=schema, errors=errors)

    async def async_step_ollama(self, user_input: dict[str, Any] | None = None) -> dict:
        """Local Ollama server — no key needed; a blank URL uses the default."""
        from .llm_provider import test_connection

        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = (user_input.get(CONF_OLLAMA_BASE_URL) or "").strip()
            conn_err = await test_connection(self.hass, "ollama", "", DEFAULT_MODEL, base_url or None)
            if conn_err:
                errors["base"] = conn_err
            else:
                self._provider_keys["ollama"] = {CONF_OLLAMA_BASE_URL: base_url}
                return await self.async_step_provider_menu()
        schema = vol.Schema({
            vol.Optional(CONF_OLLAMA_BASE_URL, default=""): str,
        })
        return self.async_show_form(step_id="ollama", data_schema=schema, errors=errors)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> dict:
        """Pick which configured provider drives the Main Agent, then create
        the entry — writing every configured provider's key to secrets.yaml
        (never config.json) so they're all usable immediately (Observer
        tiers, AI Models roles), not just the Main Agent's."""
        if not self._provider_keys:
            return await self.async_step_provider_menu()

        providers = sorted(self._provider_keys)
        errors: dict[str, str] = {}
        if user_input is not None:
            provider = user_input.get("llm_provider", providers[0])
            honorific = user_input.get(CONF_HONORIFIC, DEFAULT_HONORIFIC)
            if provider not in self._provider_keys:
                errors["base"] = "need_llm"
            else:
                self._finish_provider = provider
                self._finish_honorific = honorific
                return await self.async_step_finish_model()
        schema = vol.Schema({
            vol.Required("llm_provider", default=providers[0]):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=providers, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_HONORIFIC, default=DEFAULT_HONORIFIC): str,
        })
        return self.async_show_form(
            step_id="finish",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "note": "Choose which configured provider runs the Main Agent. "
                        "Everything else is configured later in the JARVIS panel → Settings.",
            },
        )

    async def async_step_finish_model(self, user_input: dict[str, Any] | None = None) -> dict:
        """Choose the initial Main Agent model from the provider's live list."""
        provider = getattr(self, "_finish_provider", None)
        if not provider or provider not in self._provider_keys:
            return await self.async_step_provider_menu()
        fields = self._provider_keys[provider]
        base_url_key = {
            "custom": CONF_CUSTOM_BASE_URL,
            "ollama": CONF_OLLAMA_BASE_URL,
        }.get(provider)
        provider_base_url = fields.get(base_url_key, "") if base_url_key else ""
        models = await _fetch_available_models(
            self.hass, provider, fields.get("api_key", ""), provider_base_url,
        )
        if not models:
            return self.async_show_form(
                step_id="finish_model", data_schema=vol.Schema({}), errors={"base": "no_models"},
            )
        model_schema = vol.Schema({
            vol.Required(CONF_MODEL): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=models, mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
        })
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            from . import ha_secrets, jarvis_config
            base_url = provider_base_url
            endpoint_updates: dict[str, str] = {}
            for prov, fields in self._provider_keys.items():
                if fields.get("api_key"):
                    if not await ha_secrets.async_set_provider_key(
                        self.hass, prov, fields["api_key"]
                    ):
                        return self.async_show_form(
                            step_id="finish_model",
                            data_schema=model_schema,
                            errors={"base": "unknown"},
                        )
                endpoint_key = {"custom": CONF_CUSTOM_BASE_URL, "ollama": CONF_OLLAMA_BASE_URL}.get(prov)
                if endpoint_key and fields.get(endpoint_key):
                    endpoint_updates[endpoint_key] = fields[endpoint_key]
            if endpoint_updates:
                from . import paths
                paths.set_config_dir_from_hass(self.hass)
                jarvis_config.set_config_path(paths.config_path("jarvis", "config.json", hass=self.hass))
                await self.hass.async_add_executor_job(jarvis_config.set_many, endpoint_updates)
            return self.async_create_entry(
                title="JARVIS",
                data={
                    CONF_API_KEY: "",
                    CONF_MODEL: user_input[CONF_MODEL],
                    CONF_HONORIFIC: self._finish_honorific,
                    "llm_provider": provider,
                    "llm_base_url": base_url,
                    "schema_version": 7,
                },
            )
        return self.async_show_form(
            step_id="finish_model",
            data_schema=model_schema,
        )

    async def async_step_import(
        self, import_data: dict[str, Any],
    ) -> dict:
        """Create the entry from an existing runtime config (re-install).
        Credentials live in secrets.yaml (independent of the integration), so
        this only needs to confirm one exists for the chosen provider — never
        reads/writes a key value itself."""
        from . import ha_secrets
        from .const import resolve_provider_base_url

        provider = import_data.get("llm_provider", "groq")
        base_url = resolve_provider_base_url(import_data, provider)
        local_ok = provider == "ollama" or (provider == "custom" and bool(base_url))
        secrets_path = config_path("secrets.yaml", hass=self.hass)
        try:
            has_key = bool(await self.hass.async_add_executor_job(
                ha_secrets.get_stored_provider_key_sync, provider, secrets_path))
        except TypeError:
            has_key = bool(await self.hass.async_add_executor_job(
                ha_secrets.get_stored_provider_key_sync, provider))
        # A legacy install may still have its credential in config.json only
        # (not yet relocated to secrets.yaml). Treat that as usable too, so
        # the entry gets created and async_setup_entry()'s migration can
        # relocate it instead of aborting an already-configured install.
        legacy_key = _legacy_provider_key(import_data, provider)
        has_legacy_key = bool(legacy_key)

        # An LLM is required, but a local model counts: proceed if we have either
        # a cloud key (in secrets.yaml) OR a local endpoint (ollama/custom + URL).
        if not has_key and not has_legacy_key and not local_ok:
            _LOGGER.warning("JARVIS: config found but no API key and no local LLM")
            return self.async_abort(reason="import_failed")

        if not has_key and legacy_key:
            has_key = await ha_secrets.async_set_provider_key(
                self.hass, provider, legacy_key)
            if not has_key:
                _LOGGER.warning("JARVIS: config found but could not persist API key to secrets.yaml")
                return self.async_abort(reason="import_failed")

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        _LOGGER.info("JARVIS: auto-configuring from existing runtime config (provider=%s%s)",
                     provider, ", local" if (not has_key and local_ok) else "")
        return self.async_create_entry(
            title="JARVIS",
            data={
                CONF_API_KEY: "",
                CONF_MODEL: import_data.get(CONF_MODEL, import_data.get("model", DEFAULT_MODEL)),
                CONF_HONORIFIC: import_data.get(CONF_HONORIFIC, import_data.get("honorific", DEFAULT_HONORIFIC)),
                "llm_provider": provider,
                "llm_base_url": base_url,
                "schema_version": 7,
            },
            options={k: v for k, v in import_data.items()
                     if k not in ha_secrets.CREDENTIAL_KEYS
                     and k not in (CONF_API_KEY, "schema_version")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "JarvisOptionsFlow":
        return JarvisOptionsFlow(config_entry)


class JarvisOptionsFlow(OptionsFlow):
    """
    Options flow — JARVIS is configurable from Settings → Devices &
    Services → JARVIS → Configure (in addition to the in-app panel).

    A menu lets you jump straight to the section you want instead of clicking
    through every screen: Core (persona/model/home control), Routing (bedroom
    areas, broadcast group, notify service), Observer (proactive awareness),
    and Identity (per-person recognition). Each section saves on its own and
    returns to the menu, so changing one setting no longer means walking the
    whole sequence.

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

    async def _available_models(self, provider: str) -> list[str]:
        """Fetch models for a configured provider for a HA selector."""
        from . import ha_secrets
        api_key = await ha_secrets.async_get_provider_key(self.hass, provider)
        endpoint_key = {"custom": CONF_CUSTOM_BASE_URL, "ollama": CONF_OLLAMA_BASE_URL}.get(provider, "llm_base_url")
        base_url = str(self._cur(endpoint_key, "") or "")
        return await _fetch_available_models(self.hass, provider, api_key, base_url)

    def _model_selector(self, models: list[str], current: str = ""):
        return selector.SelectSelector(selector.SelectSelectorConfig(
            options=list(models),
            mode=selector.SelectSelectorMode.DROPDOWN,
        ))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict:
        """Landing menu — jump to any section directly."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["llm", "core", "routing", "observer", "identity", "email", "agents"],
        )

    async def async_step_agents(self, user_input: dict[str, Any] | None = None) -> dict:
        """Agents — specialised sub-agents and related autonomy toggles.

        FRIDAY is a background automator sub-agent that CAN control devices, run
        scenes, and run scripts. That deliberately breaks the rule that keeps
        every other sub-agent read-only, so it is off by default and turning it
        on requires ticking an explicit acknowledgement — enabling it without the
        acknowledgement is rejected and the form is shown again with a warning."""
        if user_input is not None:
            enable_friday = bool(user_input.get("friday_automator", False))
            acknowledged = bool(user_input.pop("friday_confirm", False))
            if enable_friday and not acknowledged:
                # Refuse to arm an actuating sub-agent without the explicit tick.
                return self.async_show_form(
                    step_id="agents",
                    data_schema=self._agents_schema(force_friday_on=True),
                    errors={"base": "friday_confirm_required"},
                )
            return await self._save_section(user_input)
        return self.async_show_form(step_id="agents", data_schema=self._agents_schema())

    def _agents_schema(self, force_friday_on: bool = False):
        friday_default = True if force_friday_on else self._cur("friday_automator", False)
        return vol.Schema({
            vol.Optional("friday_automator",
                         description={"suggested_value": friday_default}):
                selector.BooleanSelector(),
            vol.Optional("friday_confirm", description={"suggested_value": False}):
                selector.BooleanSelector(),
            vol.Optional("proximity_volume",
                         description=self._sv("proximity_volume", True)):
                selector.BooleanSelector(),
            vol.Optional("host_telemetry",
                         description=self._sv("host_telemetry", True)):
                selector.BooleanSelector(),
        })

    async def async_step_llm(self, user_input: dict[str, Any] | None = None) -> dict:
        """LLM — submenu for adding or rotating provider keys."""
        configured = [p for p in _PROVIDER_STEPS if await self._provider_configured(p)]
        if configured:
            note = "Configured: " + ", ".join(configured) + ". Add or rotate another provider."
        else:
            note = "No provider configured yet. Pick one to add its API key (or URL for Ollama/Custom)."
        return self.async_show_menu(
            step_id="llm",
            menu_options=list(_PROVIDER_STEPS) + ["back"],
            description_placeholders={"note": note},
        )

    async def async_step_back(self, user_input: dict[str, Any] | None = None) -> dict:
        """Return from the LLM submenu to the main options menu."""
        return await self.async_step_init()

    async def _provider_configured(self, provider: str) -> bool:
        """Whether `provider` currently has a usable key/endpoint."""
        if provider == "ollama":
            return True   # no key needed; has a working default endpoint
        if provider == "custom":
            # A Custom endpoint requires a URL — the key is optional, not
            # the other way around. A key with no URL still isn't usable,
            # since create_provider() would fall through to the default
            # OpenAI endpoint instead of the intended custom one.
            from .const import CONF_CUSTOM_BASE_URL
            return bool(self._cur(CONF_CUSTOM_BASE_URL, "")) or bool(self._cur("llm_base_url", ""))
        return bool(await self._cur_secret(provider))

    async def _cur_secret(self, provider: str) -> str:
        """Current API key for `provider`, read straight from secrets.yaml —
        the only place credentials live (never config.json/entry data)."""
        try:
            from . import ha_secrets
            return await ha_secrets.async_get_provider_key(self.hass, provider)
        except Exception:
            return ""

    async def _async_step_simple_key(self, provider: str, user_input: dict[str, Any] | None) -> dict:
        """Shared body for the single-API-key-field provider steps."""
        from .llm_provider import test_connection
        from . import ha_secrets

        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if not api_key:
                errors["base"] = "need_llm"
            else:
                conn_err = await test_connection(self.hass, provider, api_key, DEFAULT_MODEL, None)
                if conn_err:
                    errors["base"] = conn_err
                else:
                    ok = await ha_secrets.async_set_provider_key(self.hass, provider, api_key)
                    if not ok:
                        errors["base"] = "unknown"
                    else:
                        active_provider = self._cur("llm_provider", "groq")
                        if provider == active_provider:
                            from .llm_provider import async_refresh_main_client
                            from . import observer
                            await async_refresh_main_client(self.hass, self._entry)
                            if observer.is_running():
                                await observer.refresh_tier_providers(self.hass)
                        return await self.async_step_llm()
        schema = vol.Schema({
            vol.Required(CONF_API_KEY, description={"suggested_value": await self._cur_secret(provider)}):
                selector.TextSelector(selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id=provider, data_schema=schema, errors=errors)

    async def async_step_groq(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("groq", user_input)

    async def async_step_openai(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("openai", user_input)

    async def async_step_anthropic(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("anthropic", user_input)

    async def async_step_gemini(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_simple_key("gemini", user_input)

    async def async_step_custom(self, user_input: dict[str, Any] | None = None) -> dict:
        """Any OpenAI-compatible endpoint — needs a URL; the key is optional."""
        from .llm_provider import test_connection
        from . import ha_secrets

        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            base_url = (user_input.get(CONF_CUSTOM_BASE_URL) or "").strip()
            if not base_url:
                errors["base"] = "need_llm"
            else:
                conn_err = await test_connection(self.hass, "custom", api_key, DEFAULT_MODEL, base_url)
                if conn_err:
                    errors["base"] = conn_err
                else:
                    if api_key:
                        ok = await ha_secrets.async_set_provider_key(self.hass, "custom", api_key)
                        if not ok:
                            errors["base"] = "unknown"
                            api_key = ""
                    if not errors:
                        await self._persist({CONF_CUSTOM_BASE_URL: base_url})
                        return await self.async_step_llm()
        schema = vol.Schema({
            vol.Required(CONF_CUSTOM_BASE_URL, description=self._sv(CONF_CUSTOM_BASE_URL, "")): str,
            vol.Optional(CONF_API_KEY, description={"suggested_value": await self._cur_secret("custom")}):
                selector.TextSelector(selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="custom", data_schema=schema, errors=errors)

    async def async_step_ollama(self, user_input: dict[str, Any] | None = None) -> dict:
        """Local Ollama server — no key needed; a blank URL uses the default."""
        from .llm_provider import test_connection

        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = (user_input.get(CONF_OLLAMA_BASE_URL) or "").strip()
            conn_err = await test_connection(self.hass, "ollama", "", DEFAULT_MODEL, base_url or None)
            if conn_err:
                errors["base"] = conn_err
            else:
                await self._persist({CONF_OLLAMA_BASE_URL: base_url})
                return await self.async_step_llm()
        schema = vol.Schema({
            vol.Optional(CONF_OLLAMA_BASE_URL, description=self._sv(CONF_OLLAMA_BASE_URL, "")): str,
        })
        return self.async_show_form(step_id="ollama", data_schema=schema, errors=errors)

    async def _persist(self, updates: dict[str, Any]) -> None:
        """Write a non-credential value (llm_base_url) straight to jarvis_config
        without ending the options flow — used by the LLM submenu's provider
        steps so adding a key loops back to the menu instead of closing the
        dialog. Credentials never go through this — see ha_secrets.
        (Contrast with _save_section, which ends the flow and reloads the
        entry — appropriate only when something reload-worthy changes, like
        the Main Agent's provider/model.) Runs off the event loop — jarvis_config
        does blocking file I/O. The live runtime copy is updated too because
        the dashboard reads runtime_config before config.json."""
        entry_id = getattr(self._entry, "entry_id", None)
        if entry_id:
            domain_data = self.hass.data.setdefault(DOMAIN, {})
            if isinstance(domain_data, dict):
                entry_data = domain_data.setdefault(entry_id, {})
                if isinstance(entry_data, dict):
                    entry_data.setdefault("runtime_config", {}).update(updates)
        try:
            from . import jarvis_config
            await self.hass.async_add_executor_job(jarvis_config.set_many, updates)
        except Exception as exc:
            _LOGGER.warning("JARVIS options: jarvis_config write failed: %s", exc)
        if any(key.endswith(("_provider", "_model")) for key in updates):
            try:
                from . import observer
                if observer.is_running():
                    await observer.refresh_tier_providers(self.hass, updates)
            except Exception as exc:
                _LOGGER.warning("JARVIS options: observer provider refresh failed: %s", exc)
        self._data.update(updates)

    async def async_step_core(self, user_input: dict[str, Any] | None = None) -> dict:
        """Core — persona, directive, conversation model, home control."""
        if user_input is not None:
            provider = user_input.pop("llm_provider", self._cur("llm_provider", "groq"))
            self._data.update(user_input)
            self._data["llm_provider"] = provider
            return await self.async_step_core_model()
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
            vol.Optional("llm_provider", description=self._sv("llm_provider", "groq")):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[p for p in _PROVIDER_STEPS if await self._provider_configured(p)],
                    mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_USE_HASS_API, description=self._sv(CONF_USE_HASS_API, True)):
                selector.BooleanSelector(),
            vol.Optional(CONF_WEB_RESEARCH_LLM_FALLBACK,
                         description=self._sv(CONF_WEB_RESEARCH_LLM_FALLBACK,
                                              DEFAULT_WEB_RESEARCH_LLM_FALLBACK)):
                selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="core", data_schema=schema)

    async def async_step_core_model(self, user_input: dict[str, Any] | None = None) -> dict:
        """Choose the conversation model from the selected provider's list."""
        provider = self._data.get("llm_provider", self._cur("llm_provider", "groq"))
        models = await self._available_models(provider)
        if user_input is not None:
            self._data.update(user_input)
            return await self._save_section(dict(self._data))
        if not models:
            return self.async_show_form(
                step_id="core_model",
                data_schema=vol.Schema({}),
                errors={"base": "no_models"},
            )
        current = self._cur(CONF_MODEL, DEFAULT_MODEL)
        selected = current if current in models else models[0]
        return self.async_show_form(
            step_id="core_model",
            data_schema=vol.Schema({
                vol.Required(CONF_MODEL, default=selected): self._model_selector(models),
            }),
        )

    async def async_step_routing(self, user_input: dict[str, Any] | None = None) -> dict:
        """Routing — bedroom areas, broadcast group, phone notify service."""
        if user_input is not None:
            return await self._save_section(user_input)
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
        """Observer — configure each model tier and observer settings."""
        if user_input is not None:
            return await self._save_section(user_input)
        return self.async_show_menu(
            step_id="observer",
            menu_options=["classifier", "reasoning", "review", "observer_settings", "back"],
        )

    async def _async_step_observer_tier(
        self, tier: str, user_input: dict[str, Any] | None,
    ) -> dict:
        provider_key = f"{tier}_provider"
        if user_input is not None:
            self._data[provider_key] = user_input[provider_key]
            updates = {provider_key: user_input[provider_key]}
            if tier == "review":
                updates["review_enabled"] = True
            await self._persist(updates)
            return await self._async_step_observer_model(tier)
        providers = [p for p in _PROVIDER_STEPS if await self._provider_configured(p)]
        if not providers:
            return self.async_show_form(
                step_id=tier,
                data_schema=vol.Schema({}),
                errors={"base": "no_providers"},
            )
        default_provider = self._cur(provider_key, providers[0])
        if default_provider not in providers:
            default_provider = providers[0]
        return self.async_show_form(
            step_id=tier,
            data_schema=vol.Schema({
                vol.Required(provider_key, default=default_provider):
                    selector.SelectSelector(selector.SelectSelectorConfig(
                        options=providers, mode=selector.SelectSelectorMode.DROPDOWN)),
            }),
        )

    async def _async_step_observer_model(self, tier: str, user_input: dict[str, Any] | None = None) -> dict:
        provider = self._data.get(f"{tier}_provider", self._cur(f"{tier}_provider", "groq"))
        models = await self._available_models(provider)
        model_key = f"{tier}_model"
        if user_input is not None:
            self._data[model_key] = user_input[model_key]
            updates = {model_key: user_input[model_key]}
            if tier == "review":
                updates["review_enabled"] = True
            await self._persist(updates)
            return await self.async_step_observer()
        current = self._cur(model_key, {
            "classifier": DEFAULT_CLASSIFIER_MODEL,
            "reasoning": DEFAULT_REASONING_MODEL,
            "review": DEFAULT_REVIEW_MODEL,
        }[tier])
        if not models:
            return self.async_show_form(
                step_id=f"{tier}_model",
                data_schema=vol.Schema({}),
                errors={"base": "no_models"},
            )
        return self.async_show_form(
            step_id=f"{tier}_model",
            data_schema=vol.Schema({
                vol.Required(model_key, default=current if current in models else models[0]):
                    self._model_selector(models),
            }),
        )

    async def async_step_classifier(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_observer_tier("classifier", user_input)

    async def async_step_classifier_model(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_observer_model("classifier", user_input)

    async def async_step_reasoning(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_observer_tier("reasoning", user_input)

    async def async_step_reasoning_model(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_observer_model("reasoning", user_input)

    async def async_step_review(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_observer_tier("review", user_input)

    async def async_step_review_model(self, user_input: dict[str, Any] | None = None) -> dict:
        return await self._async_step_observer_model("review", user_input)

    async def async_step_observer_settings(self, user_input: dict[str, Any] | None = None) -> dict:
        if user_input is not None:
            return await self._save_section(user_input)
        schema = vol.Schema({
            vol.Optional(CONF_OBSERVER_ENABLED, description=self._sv(CONF_OBSERVER_ENABLED, False)):
                selector.BooleanSelector(),
            vol.Optional(CONF_OBSERVER_QUIET_START,
                         description=self._sv(CONF_OBSERVER_QUIET_START, DEFAULT_OBSERVER_QUIET_START)):
                selector.TextSelector(),
            vol.Optional(CONF_OBSERVER_QUIET_END,
                         description=self._sv(CONF_OBSERVER_QUIET_END, DEFAULT_OBSERVER_QUIET_END)):
                selector.TextSelector(),
        })
        return self.async_show_form(step_id="observer_settings", data_schema=schema)

    async def async_step_identity(self, user_input: dict[str, Any] | None = None) -> dict:
        """Identity — per-person recognition; voice fingerprint tier needs a GPU."""
        if user_input is not None:
            return await self._save_section(user_input)
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

    async def async_step_email(self, user_input: dict[str, Any] | None = None) -> dict:
        """Email — read-only IMAP inbox access. The password lives in HA
        secrets.yaml (imap_secret_key), never in the panel config."""
        if user_input is not None:
            return await self._save_section(user_input)
        schema = vol.Schema({
            vol.Optional("imap_enabled", description=self._sv("imap_enabled", False)):
                selector.BooleanSelector(),
            vol.Optional("imap_host", description=self._sv("imap_host", "")):
                selector.TextSelector(),
            vol.Optional("imap_port", description=self._sv("imap_port", 993)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("imap_user", description=self._sv("imap_user", "")):
                selector.TextSelector(),
            vol.Optional("imap_folder", description=self._sv("imap_folder", "INBOX")):
                selector.TextSelector(),
            vol.Optional("imap_ssl", description=self._sv("imap_ssl", True)):
                selector.BooleanSelector(),
            vol.Optional("imap_secret_key",
                         description=self._sv("imap_secret_key", "jarvis_imap_password")):
                selector.TextSelector(),
        })
        return self.async_show_form(step_id="email", data_schema=schema)

    async def _save_section(self, user_input: dict[str, Any]) -> dict:
        """Persist one section's values to runtime config + entry options, then
        finish. Each section saves independently (menu-based flow), so only the
        edited keys are written — other sections' stored values are untouched."""
        self._data.update(user_input)
        try:
            from . import jarvis_config
            await self.hass.async_add_executor_job(jarvis_config.set_many, dict(user_input))
        except Exception as exc:
            _LOGGER.warning("JARVIS options: jarvis_config write failed: %s", exc)
        return self.async_create_entry(title="", data={**self._entry.options, **self._data})
