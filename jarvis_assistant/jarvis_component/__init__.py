"""JARVIS AI Assistant — Home Assistant integration."""
from __future__ import annotations

import logging
import json
import os
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_API_KEY,
    CONF_BEDROOM_AREAS,
    CONF_BROADCAST_GROUP,
    CONF_HONORIFIC,
    CONF_TTS_ENGINE,
    CONF_TTS_PREMIUM_ENGINE,
    CONF_TTS_PREMIUM_CONTEXTS,
    DEFAULT_HONORIFIC,
    DEFAULT_TTS_ENGINE,
    DEFAULT_TTS_PREMIUM_ENGINE,
    DEFAULT_TTS_PREMIUM_CONTEXTS,
    DOMAIN,
)
from .audio_routing import broadcast_target
from .camera import (
    async_analyze_camera,
    async_auto_analyze_on_event,
    register_event_listeners,
)
from .briefing import async_briefing
from .scenes import async_activate_by_intent
from .routines import async_run_routine, list_routines
from .reminders import async_add_reminder_service, ReminderWatcher
from .recognition import register_recognition_listener
from .summary import async_summarise
from .sentinel import JarvisSentinel
from .database import purge_old_records, get_stats
from .llm_provider import create_provider
from .migrations import migrate_config, CURRENT_SCHEMA_VERSION
from .panel_register import async_register_panel, async_unregister_panel
from .websocket import async_register as async_register_ws

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation"]
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)


def _maybe_trigger_import(hass: HomeAssistant) -> None:
    """
    Check for jarvis_config.json and kick off the import flow if needed.
    Idempotent — safe to call more than once.
    """
    config_file = hass.config.path("jarvis_config.json")
    if not os.path.exists(config_file):
        return
    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.debug("JARVIS: config entry already exists — skipping auto-import")
        return

    _LOGGER.info("JARVIS: found jarvis_config.json — starting import flow")
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={},
        )
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """
    Called once on HA startup IF our domain appears in configuration.yaml.
    If jarvis_config.json exists and no entry is registered, kick off the
    import flow.
    """
    _maybe_trigger_import(hass)
    return True


async def async_setup_post_start(hass: HomeAssistant) -> None:
    """
    Fallback: also trigger the import flow AFTER HA startup completes.
    This covers the common case where the user has NOT added `jarvis:` to
    their configuration.yaml — without this, async_setup never fires for
    custom integrations, so a config-file-based install would silently do
    nothing. Called from async_setup_entry's first run OR from a startup
    listener registered during module import.
    """
    _maybe_trigger_import(hass)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up JARVIS from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # ── Reconcile addon-owned keys from jarvis_config.json ──────────────────
    # The addon writes jarvis_config.json on every start. Keys listed in
    # ADDON_OWNED_KEYS are considered "addon-controlled" — toggling them in
    # addon config must take effect on next restart.
    #
    # How it works:
    #   1. Hash the current addon config's addon-owned keys.
    #   2. Compare against the hash we stored last reconcile.
    #   3. If the hash DIFFERS, the user changed addon config since last run.
    #      Push new values into BOTH entry.data AND entry.options (options
    #      wins at runtime, so we must overwrite it or the Configure dialog
    #      value will keep winning forever).
    #   4. If the hash MATCHES, the user hasn't touched addon config since
    #      last run — their Configure-dialog options win, don't touch them.
    #
    # This gives: addon-config toggle is authoritative when changed; user's
    # Configure-dialog choices persist when addon config is stable.
    ADDON_OWNED_KEYS = (
        "observer_enabled",
        "announcements_enabled",      # v5.4.7 master kill switch
        "sentinel_enabled",           # v5.4.7 per-subsystem toggle
        "groq_api_key",               # fallback field name (some installs)
        CONF_API_KEY,                 # v5.9.26: addon writes the groq key as "api_key"
        "gemini_api_key",
        "classifier_provider",
        "classifier_model",
        "reasoning_provider",
        "reasoning_model",
        "review_provider",
        "review_model",
        "observer_quiet_start",
        "observer_quiet_end",
        "classifier_rate_limit",
        "cognition_enabled",
        "cognition_threshold",
    )
    try:
        config_file = hass.config.path("jarvis_config.json")
        if os.path.exists(config_file):
            def _read_json(path: str) -> dict:
                with open(path) as f:
                    return json.load(f)
            addon_cfg = await hass.async_add_executor_job(_read_json, config_file)

            # Build a stable hash of just the addon-owned slice
            import hashlib
            addon_slice = {k: addon_cfg.get(k) for k in ADDON_OWNED_KEYS}
            addon_hash = hashlib.sha256(
                json.dumps(addon_slice, sort_keys=True).encode()
            ).hexdigest()[:16]

            prev_hash = entry.data.get("addon_config_hash")
            if addon_hash != prev_hash:
                # Addon config changed since last reconcile — push authoritatively
                new_data = dict(entry.data)
                new_options = dict(entry.options)
                changed = []
                for k in ADDON_OWNED_KEYS:
                    if k in addon_cfg:
                        old_val = new_options.get(k, new_data.get(k))
                        if addon_cfg[k] != old_val:
                            # Never log secret values.
                            if "key" in k:
                                changed.append(f"{k}: (changed)")
                            else:
                                changed.append(f"{k}: {old_val}→{addon_cfg[k]}")
                        new_data[k] = addon_cfg[k]
                        new_options[k] = addon_cfg[k]
                # The addon stores the primary LLM key as `groq_api_key`, but the
                # conversation agent reads it as `api_key` (CONF_API_KEY). Map it
                # so rotating the key in addon config actually reaches the agent
                # on the next reload (previously it never did → stale 401s).
                gk = (addon_cfg.get("groq_api_key") or "").strip()
                if gk and gk != (new_data.get(CONF_API_KEY) or ""):
                    changed.append("api_key: (synced from groq_api_key)")
                    new_data[CONF_API_KEY] = gk
                    new_options[CONF_API_KEY] = gk
                new_data["addon_config_hash"] = addon_hash
                hass.config_entries.async_update_entry(
                    entry, data=new_data, options=new_options,
                )
                if changed:
                    _LOGGER.warning(
                        "JARVIS: addon config changed — applied to entry: %s",
                        "; ".join(changed),
                    )
                else:
                    _LOGGER.info(
                        "JARVIS: addon config hash updated (first reconcile or schema change)"
                    )
            else:
                _LOGGER.debug("JARVIS: addon config unchanged, preserving user options")
    except Exception as exc:
        _LOGGER.debug("JARVIS: addon config reconcile failed (non-fatal): %s", exc)

    # ── Run config migrations if entry is from an older schema ──────────────
    current_version = entry.data.get("schema_version", 1)
    if current_version < CURRENT_SCHEMA_VERSION:
        new_data   = dict(entry.data)
        new_options = dict(entry.options)
        new_data, new_options, new_version = migrate_config(
            new_data, new_options, current_version
        )
        new_data["schema_version"] = new_version
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )
        _LOGGER.info(
            "JARVIS: migrated config from schema v%d to v%d",
            current_version, new_version,
        )

    api_key   = entry.data[CONF_API_KEY]
    honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))

    # ── LLM provider (defaults to groq for existing installs) ───────────────
    llm_provider_name = entry.options.get(
        "llm_provider", entry.data.get("llm_provider", "groq")
    )
    llm_model = entry.options.get(
        "model", entry.data.get("model", "llama-3.3-70b-versatile")
    )
    llm_base_url = entry.options.get(
        "llm_base_url", entry.data.get("llm_base_url", "")
    ) or None

    try:
        llm_client = await hass.async_add_executor_job(
            create_provider,
            llm_provider_name, api_key, llm_model, llm_base_url,
        )
        _LOGGER.info(
            "JARVIS: LLM provider '%s' initialised (model=%s)",
            llm_provider_name, llm_model,
        )
    except Exception as exc:
        _LOGGER.error("JARVIS: LLM provider init failed: %s", exc)
        return False

    sentinel = JarvisSentinel(hass, llm_client, honorific, entry=entry)

    # Register camera event listeners (nest_event, frigate_event)
    camera_unsubs = register_event_listeners(hass)

    # ── Auto-analyze camera events GOING FORWARD (doorbell / person) ─────────
    # The listeners above only CACHE Nest/Frigate events — historically nothing
    # was analyzed unless a user automation called jarvis.analyze_on_event. These
    # listeners make JARVIS inspect notable events itself: a doorbell PRESS always
    # gets a look; person/motion are throttled per-camera so a busy street/sidewalk
    # can't spam the vision model, and the spoken announcement is still notability-
    # gated (only deliveries, unfamiliar people, etc. are voiced). Toggle:
    # General → "Camera Watch" (camera_auto_analyze); motion behind a second flag.
    import time as _auto_time
    from .camera import _nest_device_to_camera as _nest2cam

    _auto_cd: dict[str, float] = {}      # person/motion throttle, per entity
    _chime_cd: dict[str, float] = {}     # doorbell-press anti-double, per entity

    def _auto_flag(key: str, default: bool) -> bool:
        try:
            _d = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            _rc = _d.get("runtime_config", {}) if isinstance(_d, dict) else {}
            if key in _rc:
                _v = _rc[key]
                return _v if isinstance(_v, bool) else str(_v).lower() in ("1", "true", "yes", "on")
        except Exception:
            pass
        return default

    def _auto_fire(entity_id: str, reason: str, ctx: str, doorbell: bool = False) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context=ctx)
        spk = _get_speakers(hass, entry)
        hass.async_create_task(
            async_auto_analyze_on_event(
                hass, llm_client, honorific, tts, spk, entity_id, reason, doorbell=doorbell
            )
        )

    @callback
    def _auto_nest(event) -> None:
        if not _auto_flag("camera_auto_analyze", True):
            return
        try:
            data = event.data
            device_id = data.get("device_id") or data.get("nest_device_id")
            etype = str(data.get("type") or data.get("event_type") or "").lower()
            if not device_id:
                return
            # Doorbell PRESS → the full announced analysis. Person events feed
            # SILENT visitor learning (training data only, never spoken) when
            # enabled; motion/sound stay ignored.
            if "chime" not in etype and "doorbell" not in etype:
                if "person" in etype and _auto_flag("visitor_learning", True):
                    entity_id = _nest2cam(hass, device_id)
                    if not entity_id:
                        return
                    now = _auto_time.monotonic()
                    if now - _auto_cd.get(entity_id, float("-inf")) < 180.0:
                        return
                    _auto_cd[entity_id] = now
                    honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
                    from .camera import async_visitor_observation
                    hass.async_create_task(
                        async_visitor_observation(hass, llm_client, honorific, entity_id)
                    )
                return
            entity_id = _nest2cam(hass, device_id)
            if not entity_id:
                return
            now = _auto_time.monotonic()
            if now - _chime_cd.get(entity_id, float("-inf")) < 12.0:
                return  # collapse a rapid double-press
            _chime_cd[entity_id] = now
            _auto_fire(entity_id, "Someone is at the front door", "doorbell", doorbell=True)
        except Exception as exc:
            _LOGGER.debug("JARVIS auto-analyze (nest) error: %s", exc)

    @callback
    def _auto_frigate(event) -> None:
        # Frigate has no doorbell-press concept; its events are person/object
        # detections. With the doorbell-only default, leave Frigate dormant unless
        # the user opts into the noisier non-doorbell analysis.
        if not (_auto_flag("camera_auto_analyze", True)
                and _auto_flag("camera_auto_analyze_motion", False)):
            return
        try:
            data = event.data
            if data.get("type") != "new":
                return
            after = data.get("after") or data.get("before") or {}
            cam = after.get("camera")
            label = str(after.get("label") or "").lower()
            if not cam:
                return
            entity_id = f"camera.{str(cam).lower()}"
            if not hass.states.get(entity_id):
                return
            if label and label not in (
                "person", "car", "truck", "package", "dog", "cat", "bicycle", "motorcycle",
            ):
                return  # ignore irrelevant tracked objects
            now = _auto_time.monotonic()
            if now - _auto_cd.get(entity_id, float("-inf")) < 120.0:
                return
            _auto_cd[entity_id] = now
            _auto_fire(entity_id, f"{label.capitalize()} detected" if label else "Motion detected", "camera")
        except Exception as exc:
            _LOGGER.debug("JARVIS auto-analyze (frigate) error: %s", exc)

    try:
        camera_unsubs.append(hass.bus.async_listen("nest_event", _auto_nest))
        camera_unsubs.append(hass.bus.async_listen("frigate_event", _auto_frigate))
        _LOGGER.info("JARVIS: camera auto-analysis active (doorbell always, person/motion throttled)")
    except Exception as exc:
        _LOGGER.debug("JARVIS: auto-analyze listener registration failed: %s", exc)

    # ── Package & mail detection — periodic porch check ─────────────────────
    # Deliveries often don't ring the bell (carrier drops and leaves), so a low-
    # frequency vision sweep of the doorbell/porch camera catches them. Per-camera
    # state means a package sitting all day is announced once, on arrival. Skipped
    # during quiet hours. Toggle: General → "Package Watch" (package_detection).
    PKG_INTERVAL = timedelta(minutes=15)

    async def _package_tick(_now) -> None:
        if not _auto_flag("package_detection", True):
            return
        try:
            from . import package_monitor
            honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
            tts = _get_tts(hass, entry, context="package")
            spk = _get_speakers(hass, entry)
            report = await package_monitor.periodic_check(
                hass, llm_client, honorific, tts, spk, configured_camera=None
            )
            _LOGGER.debug("JARVIS package check: %s", report)
        except Exception as exc:
            _LOGGER.debug("JARVIS package tick error: %s", exc)

    try:
        camera_unsubs.append(async_track_time_interval(hass, _package_tick, PKG_INTERVAL))
        _LOGGER.info("JARVIS: package/mail detection active (porch sweep every %s min)",
                     int(PKG_INTERVAL.total_seconds() // 60))
    except Exception as exc:
        _LOGGER.debug("JARVIS: package monitor registration failed: %s", exc)

    # Register DoubleTake MQTT face recognition listener
    recognition_unsubs = await register_recognition_listener(hass)

    # Reminder watcher — checks every 30 seconds for due reminders
    reminder_watcher = ReminderWatcher(
        hass,
        honorific_getter=lambda: entry.options.get(
            CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC)
        ),
        tts_getter=lambda: _get_tts(hass, entry, context="reminder"),
        speakers_getter=lambda: _get_speakers(hass, entry),
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "client":             llm_client,
        "sentinel":           sentinel,
        "camera_unsubs":      camera_unsubs,
        "recognition_unsubs": recognition_unsubs,
        "reminder_watcher":   reminder_watcher,
        "llm_provider_name":  llm_provider_name,
        "schema_version":     CURRENT_SCHEMA_VERSION,
    }

    # Restore persisted panel settings via centralized jarvis_config module.
    # This loads from /config/jarvis/config.json (or migrates from old path).
    # We restore EVERY panel-writable key (LLM provider/model selections,
    # cognition tunables, floor plan, etc.) so choices made in the panel win
    # over addon-config defaults and survive reboots/updates. runtime_config
    # takes precedence over entry.options/data, so this is authoritative.
    # Secrets (api_key, gemini_api_key) are intentionally NOT panel-writable and
    # therefore stay addon-controlled via the reconcile above.
    try:
        from . import jarvis_config
        from .websocket import PANEL_WRITABLE_KEYS

        # Initialize config from entry data (backfill any missing keys)
        await hass.async_add_executor_job(
            jarvis_config.init_from_entry,
            dict(entry.data), dict(entry.options),
        )

        # Load persisted settings into runtime_config
        cfg = await hass.async_add_executor_job(jarvis_config.get_all)
        restore_keys = set(PANEL_WRITABLE_KEYS) | {
            "broadcast_group", "observer_quiet_start",
            "observer_quiet_end", "bedroom_areas",
        }
        rc = {k: cfg[k] for k in restore_keys if k in cfg}
        if rc:
            hass.data[DOMAIN][entry.entry_id]["runtime_config"] = rc
            _LOGGER.info(
                "Restored %d panel settings from jarvis_config (%d total keys in file)",
                len(rc), len(cfg),
            )
    except Exception as exc:
        _LOGGER.debug("Config restore: %s", exc)

    # Register services — guard against double-registration on reload
    _register_services(hass, entry, llm_client, sentinel)

    # Reload services when options change
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Auto-start Sentinel + Reminder watcher
    await sentinel.async_start()
    await reminder_watcher.async_start()

    # ── v5.2 Observer Mode ──────────────────────────────────────────────────
    # Observer subscribes to state_changed events and proactively announces
    # interesting things through the LLM tier pipeline. OFF by default.
    # User enables via the `observer_enabled` option in addon config.
    observer_enabled = bool(entry.options.get(
        "observer_enabled",
        entry.data.get("observer_enabled", False),
    ))
    if observer_enabled:
        # Build a single combined config dict of what observer needs.
        # It reads from both entry.data and entry.options with options winning.
        observer_config = {**dict(entry.data), **dict(entry.options)}
        from . import observer as observer_mod
        await observer_mod.start(hass, observer_config)
        hass.data[DOMAIN][entry.entry_id]["observer_running"] = True
        _LOGGER.info("JARVIS Observer mode ENABLED — watching for interesting events")
    else:
        hass.data[DOMAIN][entry.entry_id]["observer_running"] = False
        _LOGGER.info(
            "JARVIS Observer mode disabled. Enable via addon config → observer_enabled=true"
        )

    # ── v5.4 Command Center panel ──────────────────────────────────────────
    # Register sidebar panel. Idempotent — safe if called after reload.
    try:
        await async_register_panel(hass)
    except Exception as exc:
        _LOGGER.warning("JARVIS panel registration failed (non-fatal): %s", exc)

    # Register WebSocket API command for live panel data
    try:
        async_register_ws(hass)
    except Exception as exc:
        _LOGGER.warning("JARVIS WS command registration failed (non-fatal): %s", exc)

    _LOGGER.info("JARVIS online. Good day, %s. Routines available: %s",
                 honorific, ", ".join(list_routines()))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload JARVIS."""
    data = hass.data[DOMAIN].get(entry.entry_id, {})

    # Unregister the panel early — best effort
    try:
        async_unregister_panel(hass)
    except Exception as exc:
        _LOGGER.debug("Panel unregister note: %s", exc)

    sentinel: JarvisSentinel | None = data.get("sentinel")
    if sentinel:
        await sentinel.async_stop()

    reminder_watcher = data.get("reminder_watcher")
    if reminder_watcher:
        await reminder_watcher.async_stop()

    # Stop observer if it's running
    if data.get("observer_running"):
        try:
            from . import observer as observer_mod
            await observer_mod.stop()
        except Exception as exc:
            _LOGGER.debug("Observer stop failed: %s", exc)

    # Remove camera event listeners
    for unsub in data.get("camera_unsubs", []):
        try:
            unsub()
        except Exception:
            pass

    # Remove recognition listeners
    for unsub in data.get("recognition_unsubs", []):
        try:
            if callable(unsub):
                unsub()
        except Exception:
            pass

    # Remove services registered by this entry
    for service in ("analyze_camera", "analyze_on_event",
                    "conversation_summary", "briefing",
                    "scene_by_intent", "routine", "add_reminder",
                    "sentinel_start", "sentinel_stop",
                    "database_purge", "database_stats",
                    "nap", "shush", "unshush",
                    "observer_start", "observer_stop", "observer_status"):
        hass.services.async_remove(DOMAIN, service)

    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_tts(hass: HomeAssistant, entry: ConfigEntry, context: str = "chat") -> str | None:
    """
    Return the TTS entity to use for this context.

    For 'premium' contexts (briefing/doorbell/camera/recognition by default)
    we route to the premium TTS engine (ElevenLabs) if configured.
    All other contexts use the regular engine (Piper or similar).
    """
    from .tts_helper import resolve_tts_for_context

    regular = entry.options.get(CONF_TTS_ENGINE, DEFAULT_TTS_ENGINE)
    premium = entry.options.get(CONF_TTS_PREMIUM_ENGINE, DEFAULT_TTS_PREMIUM_ENGINE)
    premium_contexts = entry.options.get(
        CONF_TTS_PREMIUM_CONTEXTS, DEFAULT_TTS_PREMIUM_CONTEXTS
    )

    return resolve_tts_for_context(
        hass, context, regular, premium, premium_contexts
    )


def _get_speakers(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """
    Return the list of speakers for PROACTIVE ANNOUNCEMENTS.
    Checks runtime_config.announcement_speakers first, falls back to
    broadcast_group from entry options/data.
    """
    # Check runtime_config first (panel Settings → Announcement Speakers)
    try:
        import json as _json
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
        raw = rc.get("announcement_speakers")
        if raw:
            speakers = _json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(speakers, list) and speakers:
                _LOGGER.debug("Announcement speakers from panel config: %s", speakers)
                return speakers
    except Exception as exc:
        _LOGGER.debug("Error reading announcement_speakers: %s", exc)

    # Fallback: broadcast_group from config
    broadcast_group = entry.options.get(
        CONF_BROADCAST_GROUP, entry.data.get(CONF_BROADCAST_GROUP, "")
    ) or None
    result = broadcast_target(hass, broadcast_group=broadcast_group)
    _LOGGER.debug("Announcement speakers from broadcast_group: %s", result)
    return result


def _register_services(
    hass: HomeAssistant,
    entry: ConfigEntry,
    groq_client,
    sentinel: JarvisSentinel,
) -> None:
    """Register all JARVIS services. Called once per entry setup."""

    async def _camera(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="camera")
        spk = _get_speakers(hass, entry)
        await async_analyze_camera(hass, call, groq_client, honorific, tts, spk)

    hass.services.async_register(
        DOMAIN, "analyze_camera", _camera,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
            vol.Optional("prompt"): cv.string,
            vol.Optional("announce", default=True): cv.boolean,
        }),
    )

    async def _analyze_on_event(call: ServiceCall) -> None:
        """Push-triggered analyze — intended for doorbell/motion automations."""
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="doorbell")
        spk = _get_speakers(hass, entry)
        entity_id = call.data["entity_id"]
        reason    = call.data.get("reason", "Activity detected")
        await async_auto_analyze_on_event(
            hass, groq_client, honorific, tts, spk, entity_id, reason
        )

    hass.services.async_register(
        DOMAIN, "analyze_on_event", _analyze_on_event,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
            vol.Optional("reason", default="Activity detected"): cv.string,
        }),
    )

    # ── Doorbell backlog → training data ───────────────────────────────────────
    async def _train_backlog(call: ServiceCall) -> None:
        """Analyse the Nest doorbell's recorded event history into the training
        log. Best-effort; reports how many events it managed to analyse."""
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        limit = int(call.data.get("limit", 40) or 40)
        from . import doorbell_training
        from .camera import async_analyze_camera, _FakeCall

        # Resolve a doorbell camera entity for naming/attribution
        doorbell_entity = None
        for st in hass.states.async_all("camera"):
            if any(k in st.entity_id for k in ("doorbell", "front_door")):
                doorbell_entity = st.entity_id
                break
        if doorbell_entity is None:
            cams = [s.entity_id for s in hass.states.async_all("camera")]
            doorbell_entity = cams[0] if cams else "camera.front_doorbell"

        async def _analyze_image(image_bytes, label):
            prompt = (
                f"Recorded doorbell event ({label}). Identify who is at the door — "
                f"appearance, clothing, packages, vehicles. "
                f"Focus on what {honorific} would want to know."
            )
            fc = _FakeCall({"entity_id": doorbell_entity, "prompt": prompt, "announce": False})
            return await async_analyze_camera(
                hass, fc, groq_client, honorific, None, [],
                gate_announce=True, force_images=[image_bytes],
            )

        report = await doorbell_training.scan_backlog(hass, _analyze_image, honorific, limit=limit)
        _LOGGER.info("JARVIS doorbell backlog scan: %s", report)
        try:
            from .websocket import jarvis_log
            if report.get("ok"):
                jarvis_log("CAMERA",
                           f"Backlog training: analysed {report['analyzed']} doorbell "
                           f"event(s) into the dataset (of {report['found']} found)")
            else:
                jarvis_log("CAMERA", f"Backlog training: {report.get('reason', 'no events analysed')}")
        except Exception:
            pass

    hass.services.async_register(
        DOMAIN, "train_doorbell_backlog", _train_backlog,
        schema=vol.Schema({
            vol.Optional("limit", default=40): vol.Coerce(int),
        }),
    )

    # ── Package / mail — on-demand check ───────────────────────────────────────
    async def _check_packages(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="package")
        spk = _get_speakers(hass, entry)
        from . import package_monitor
        cam = call.data.get("entity_id")
        report = await package_monitor.periodic_check(
            hass, groq_client, honorific, tts, spk, configured_camera=cam
        )
        _LOGGER.info("JARVIS manual package check: %s", report)

    hass.services.async_register(
        DOMAIN, "check_packages", _check_packages,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_id,
        }),
    )

    # ── Briefing ──────────────────────────────────────────────────────────────
    async def _briefing(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="briefing")
        spk = _get_speakers(hass, entry)
        await async_briefing(hass, call, groq_client, honorific, tts, spk)

    hass.services.async_register(
        DOMAIN, "briefing", _briefing,
        schema=vol.Schema({
            vol.Optional("announce", default=True): cv.boolean,
            vol.Optional("include_weather", default=True): cv.boolean,
            vol.Optional("include_calendar", default=True): cv.boolean,
            vol.Optional("include_presence", default=True): cv.boolean,
            vol.Optional("include_events", default=True): cv.boolean,
            vol.Optional("include_energy", default=True): cv.boolean,
            vol.Optional("hours", default=12): vol.All(int, vol.Range(min=1, max=48)),
        }),
    )

    # ── Scene by intent ───────────────────────────────────────────────────────
    async def _scene_intent(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="chat")
        spk = _get_speakers(hass, entry)
        await async_activate_by_intent(hass, call, groq_client, honorific, tts, spk)

    hass.services.async_register(
        DOMAIN, "scene_by_intent", _scene_intent,
        schema=vol.Schema({
            vol.Required("intent"): cv.string,
            vol.Optional("announce", default=True): cv.boolean,
        }),
    )

    # ── Routine ───────────────────────────────────────────────────────────────
    async def _routine(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="routine")
        spk = _get_speakers(hass, entry)
        await async_run_routine(hass, call, honorific, tts, spk)

    hass.services.async_register(
        DOMAIN, "routine", _routine,
        schema=vol.Schema({
            vol.Required("name"): cv.string,
        }),
    )

    # ── Add reminder ──────────────────────────────────────────────────────────
    async def _add_reminder(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="reminder")
        spk = _get_speakers(hass, entry)
        await async_add_reminder_service(hass, call, honorific, tts, spk)

    hass.services.async_register(
        DOMAIN, "add_reminder", _add_reminder,
        schema=vol.Schema({
            vol.Required("label"): cv.string,
            vol.Required("trigger_at"): cv.string,
            vol.Optional("repeat"): vol.In(["daily", "weekly", "hourly"]),
            vol.Optional("require_home", default=True): cv.boolean,
            vol.Optional("respect_quiet", default=True): cv.boolean,
        }),
    )

    async def _summary(call: ServiceCall) -> None:
        honorific = entry.options.get(CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC))
        tts = _get_tts(hass, entry, context="summary")
        spk = _get_speakers(hass, entry)
        await async_summarise(hass, call, groq_client, honorific, tts, spk)

    hass.services.async_register(
        DOMAIN, "conversation_summary", _summary,
        schema=vol.Schema({
            vol.Optional("hours", default=24): vol.All(int, vol.Range(min=1, max=168)),
            vol.Optional("device_id"): cv.string,
            vol.Optional("announce", default=True): cv.boolean,
            vol.Optional("store", default=True): cv.boolean,
        }),
    )

    async def _sentinel_start(call: ServiceCall) -> None:
        await sentinel.async_start()

    hass.services.async_register(DOMAIN, "sentinel_start", _sentinel_start)

    async def _sentinel_stop(call: ServiceCall) -> None:
        await sentinel.async_stop()

    hass.services.async_register(DOMAIN, "sentinel_stop", _sentinel_stop)

    async def _db_purge(call: ServiceCall) -> None:
        days = call.data.get("days", 30)
        deleted = await hass.async_add_executor_job(purge_old_records, days)
        _LOGGER.info("JARVIS DB purge: %d records deleted (>%d days)", deleted, days)

    hass.services.async_register(
        DOMAIN, "database_purge", _db_purge,
        schema=vol.Schema({
            vol.Optional("days", default=30): vol.All(int, vol.Range(min=1, max=365))
        }),
    )

    async def _db_stats(call: ServiceCall) -> None:
        stats = await hass.async_add_executor_job(get_stats)
        hass.bus.async_fire("jarvis_db_stats", stats)

    hass.services.async_register(DOMAIN, "database_stats", _db_stats)

    # ── v5.2 Observer Mode services ──────────────────────────────────────────

    async def _nap(call: ServiceCall) -> None:
        """Manual mute for N minutes (default 30). Suppresses non-critical
        announcements until the duration elapses."""
        from . import sleep_detection as sd
        duration = call.data.get("duration_minutes", 30)
        sd.set_nap(duration)
        hass.bus.async_fire("jarvis_observer_nap", {"duration_minutes": duration})

    hass.services.async_register(
        DOMAIN, "nap", _nap,
        schema=vol.Schema({
            vol.Optional("duration_minutes", default=30):
                vol.All(int, vol.Range(min=1, max=480)),
        }),
    )

    async def _shush(call: ServiceCall) -> None:
        """Tell JARVIS to stop announcing. Pass all=true for blanket kill switch."""
        from . import output_gate
        entity_id = call.data.get("entity_id")
        category  = call.data.get("category")
        shush_all = bool(call.data.get("all", False))
        result = output_gate.shush(entity_id=entity_id, category=category, all=shush_all)
        hass.bus.async_fire("jarvis_observer_shushed", result)
        _LOGGER.info("JARVIS shushed: %s", result)

    hass.services.async_register(
        DOMAIN, "shush", _shush,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.string,
            vol.Optional("category"): cv.string,
            vol.Optional("all"): cv.boolean,
        }),
    )

    async def _unshush(call: ServiceCall) -> None:
        """Undo a shush. Called with no args clears ALL mutes."""
        from . import output_gate
        entity_id = call.data.get("entity_id")
        category  = call.data.get("category")
        result = output_gate.unshush(entity_id=entity_id, category=category)
        hass.bus.async_fire("jarvis_observer_unshushed", result)

    hass.services.async_register(
        DOMAIN, "unshush", _unshush,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.string,
            vol.Optional("category"): cv.string,
        }),
    )

    async def _observer_start(call: ServiceCall) -> None:
        """Start the observer manually (even if config has it disabled)."""
        from . import observer as observer_mod
        observer_config = {**dict(entry.data), **dict(entry.options)}
        await observer_mod.start(hass, observer_config)
        hass.data[DOMAIN][entry.entry_id]["observer_running"] = True
        _LOGGER.info("Observer started via service call")

    hass.services.async_register(DOMAIN, "observer_start", _observer_start)

    async def _lockdown(call: ServiceCall) -> None:
        """Engage or lift the formal lockdown state (alarm-armed posture)."""
        from . import cognitive_core
        raw = call.data.get("state", call.data.get("enabled", "on"))
        on = raw in (True, "on", "true", "True", "engage", "lock", 1, "1")
        ok = await cognitive_core.request_lockdown(
            on, reason=call.data.get("reason", "requested via service"))
        if not ok:
            _LOGGER.warning("Lockdown service: cognitive core not running")
        else:
            _LOGGER.info("Lockdown %s via service call", "engaged" if on else "lifted")

    hass.services.async_register(DOMAIN, "lockdown", _lockdown)

    async def _observer_stop(call: ServiceCall) -> None:
        """Stop the observer."""
        from . import observer as observer_mod
        await observer_mod.stop()
        hass.data[DOMAIN][entry.entry_id]["observer_running"] = False
        _LOGGER.info("Observer stopped via service call")

    hass.services.async_register(DOMAIN, "observer_stop", _observer_stop)

    async def _observer_status(call: ServiceCall) -> None:
        """Fire event with current observer state — mute list, recent activity."""
        from . import output_gate, observer as observer_mod, sleep_detection as sd
        status = output_gate.status()
        status["running"] = observer_mod.is_running()
        # Check if user is currently being treated as sleeping
        bedroom_areas = entry.options.get(
            CONF_BEDROOM_AREAS,
            entry.data.get(CONF_BEDROOM_AREAS, [])
        ) or []
        sleeping, reason = sd.is_sleeping(
            hass,
            bedroom_area_ids=bedroom_areas,
            quiet_start=entry.options.get(
                "observer_quiet_start",
                entry.data.get("observer_quiet_start", "22:00")
            ),
            quiet_end=entry.options.get(
                "observer_quiet_end",
                entry.data.get("observer_quiet_end", "07:00")
            ),
        )
        status["sleeping"] = sleeping
        status["sleep_reason"] = reason
        status["bedroom_areas"] = list(bedroom_areas)
        hass.bus.async_fire("jarvis_observer_status", status)
        _LOGGER.info("Observer status: %s", status)

    hass.services.async_register(DOMAIN, "observer_status", _observer_status)

    # v5.6.0: Automation creation service
    async def _create_automation(call: ServiceCall) -> None:
        """Create an HA automation from service call data."""
        from .automation_creator import create_automation
        result = await create_automation(
            hass,
            alias=call.data.get("alias", "Unnamed"),
            description=call.data.get("description", ""),
            trigger=call.data.get("trigger"),
            condition=call.data.get("condition"),
            action=call.data.get("action"),
            mode=call.data.get("mode", "single"),
        )
        if result.get("success"):
            hass.bus.async_fire("jarvis_automation_created", result)
        else:
            _LOGGER.warning("Automation creation failed: %s", result.get("error"))

    hass.services.async_register(DOMAIN, "create_automation", _create_automation)

    # v5.6.0: Doorbell pipeline diagnostic
    async def _diagnose_doorbell(call: ServiceCall) -> None:
        """Run doorbell pipeline diagnostics and fire event with results."""
        diag = {"checks": [], "verdict": "unknown"}

        # Check 1: Does the doorbell automation exist?
        auto_state = hass.states.get("automation.doorbell_motion_analysis")
        if auto_state:
            diag["checks"].append({"check": "automation exists", "ok": True, "state": auto_state.state})
        else:
            diag["checks"].append({"check": "automation exists", "ok": False, "detail": "automation.doorbell_motion_analysis not found"})
            diag["verdict"] = "Automation missing — create it or check the entity ID"
            hass.bus.async_fire("jarvis_doorbell_diag", diag)
            return

        # Check 2: Is it enabled?
        if auto_state.state != "on":
            diag["checks"].append({"check": "automation enabled", "ok": False, "state": auto_state.state})
            diag["verdict"] = "Automation exists but is disabled"
            hass.bus.async_fire("jarvis_doorbell_diag", diag)
            return
        diag["checks"].append({"check": "automation enabled", "ok": True})

        # Check 3: Do we have camera entities?
        cameras = [s.entity_id for s in hass.states.async_all("camera")]
        diag["checks"].append({"check": "cameras found", "ok": len(cameras) > 0, "cameras": cameras[:10]})

        # Check 4: Is jarvis.analyze_on_event registered?
        svc_exists = hass.services.has_service(DOMAIN, "analyze_on_event")
        diag["checks"].append({"check": "analyze_on_event service", "ok": svc_exists})

        # Check 5: TTS working?
        tts_entities = [s.entity_id for s in hass.states.async_all("tts")]
        diag["checks"].append({"check": "TTS entities", "ok": len(tts_entities) > 0, "entities": tts_entities})

        if all(c["ok"] for c in diag["checks"]):
            diag["verdict"] = "All checks passed — trigger the doorbell and watch logs for jarvis.analyze_on_event"
        else:
            failed = [c["check"] for c in diag["checks"] if not c["ok"]]
            diag["verdict"] = f"Failed checks: {', '.join(failed)}"

        _LOGGER.info("Doorbell diagnostic: %s", diag)
        hass.bus.async_fire("jarvis_doorbell_diag", diag)

    hass.services.async_register(DOMAIN, "diagnose_doorbell", _diagnose_doorbell)

    # v5.6.5: Test notification service
    async def _test_notify(call: ServiceCall) -> None:
        """Send a test notification to the configured phone."""
        notify_svc = None
        # Check runtime_config first
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
        notify_svc = rc.get("notify_service") or entry.options.get(
            "notify_service", entry.data.get("notify_service", "")
        )
        if not notify_svc:
            _LOGGER.warning("Test notify: no notify_service configured")
            return
        try:
            domain, service = notify_svc.split(".", 1)
            await hass.services.async_call(
                domain, service,
                {
                    "title": "JARVIS",
                    "message": "This is a test notification from JARVIS. If you see this, phone notifications are working.",
                },
                blocking=False,
            )
            _LOGGER.info("Test notification sent via %s", notify_svc)
        except Exception as exc:
            _LOGGER.warning("Test notification failed: %s", exc)

    hass.services.async_register(DOMAIN, "test_notify", _test_notify)

    # v5.6.7: Test TTS with JARVIS voice
    async def _test_tts(call: ServiceCall) -> None:
        """Play a test tone using JARVIS Piper voice on the broadcast group."""
        from .tts_helper import resolve_tts_entity, async_announce
        from .audio_routing import broadcast_target
        tts_entity = resolve_tts_entity(hass, entry.options.get("tts_engine", entry.data.get("tts_engine", "auto")))
        broadcast_group = entry.options.get("broadcast_group", entry.data.get("broadcast_group", ""))
        speakers = broadcast_target(hass, broadcast_group=broadcast_group)
        if tts_entity and speakers:
            await async_announce(
                hass,
                "JARVIS test tone. If you hear this in a British accent, the JARVIS voice is working.",
                tts_entity,
                speakers,
                context="test",
            )
            _LOGGER.info("Test TTS sent via %s → %s", tts_entity, speakers)
        else:
            _LOGGER.warning("Test TTS: no TTS entity (%s) or speakers (%s)", tts_entity, speakers)

    hass.services.async_register(DOMAIN, "test_tts", _test_tts)

    # v5.7.00: Routing diagnostic — dumps current routing state to log
    async def _test_routing(call: ServiceCall) -> None:
        """Dump routing diagnostics to the HA log."""
        from .audio_routing import (
            broadcast_target, reply_target, observer_speak_target,
            currently_occupied_areas, anyone_home, all_areas_with_satellite,
            speakers_in_area, satellites_in_area,
        )
        from .tts_helper import resolve_tts_entity, find_best_tts_entity

        broadcast_group = entry.options.get(
            "broadcast_group", entry.data.get("broadcast_group", ""))
        tts_ent = resolve_tts_entity(
            hass, entry.options.get("tts_engine",
                                     entry.data.get("tts_engine", "auto")))
        bcast = broadcast_target(hass, broadcast_group=broadcast_group or None)
        occupied = currently_occupied_areas(hass)
        home = anyone_home(hass)
        sat_areas = all_areas_with_satellite(hass)

        # Read announcement_speakers from runtime_config
        ann_spk = None
        try:
            import json as _json
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
            raw = rc.get("announcement_speakers")
            if raw:
                parsed = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list):
                    ann_spk = parsed
        except Exception:
            pass

        # Read satellite_pairings
        sat_pairs = None
        try:
            raw = rc.get("satellite_pairings")
            if raw:
                parsed = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    sat_pairs = parsed
        except Exception:
            pass

        _LOGGER.warning("=== JARVIS ROUTING DIAGNOSTIC ===")
        _LOGGER.warning("TTS entity: %s", tts_ent)
        _LOGGER.warning("TTS auto-pick: %s", find_best_tts_entity(hass))
        _LOGGER.warning("Broadcast group (config): '%s'", broadcast_group)
        _LOGGER.warning("Broadcast target resolved: %s", bcast)
        _LOGGER.warning("Announcement speakers (panel): %s", ann_spk)
        _LOGGER.warning("Satellite pairings (panel): %s", sat_pairs)
        _LOGGER.warning("Anyone home: %s", home)
        _LOGGER.warning("Occupied areas: %s", occupied)
        _LOGGER.warning("Areas with satellites: %s", sat_areas)
        for area_id in sat_areas:
            sats = satellites_in_area(hass, area_id)
            spks = speakers_in_area(hass, area_id)
            _LOGGER.warning("  Area '%s': sats=%s, speakers=%s",
                            area_id, sats, spks)
            for sat in sats:
                target = reply_target(
                    hass, satellite_entity_id=sat,
                    satellite_pairings=sat_pairs,
                )
                _LOGGER.warning("    reply_target(%s) → %s", sat, target)

        # Test observer routing for each urgency
        for urg in ("low", "medium", "high", "critical"):
            targets, mode = observer_speak_target(
                hass, urgency=urg,
                broadcast_group=broadcast_group or None,
                announcement_speakers=ann_spk,
                is_sleeping=False,
            )
            _LOGGER.warning("  observer(%s): targets=%s, mode=%s",
                            urg, targets, mode)
        _LOGGER.warning("=== END ROUTING DIAGNOSTIC ===")

    hass.services.async_register(DOMAIN, "test_routing", _test_routing)
