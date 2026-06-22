"""Proactive audio + infrastructure audit bridge for the JARVIS integration.

This module owns the ``jarvis.speak`` service (area-aware, prosody-shaped TTS
with media ducking) and the 15-minute infrastructure audit. It is wired into the
existing integration via two calls from ``__init__.py``:

    async_setup_entry   →  await async_setup_proactive_audio(hass, entry)
    async_unload_entry  →  await async_unload_proactive_audio(hass, entry)

It deliberately keeps the area-driven design from the feature spec rather than
routing through audio_routing/tts_helper, so the two systems stay decoupled; the
only shared state is honorific (from config) and the entry's unsub list.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
)
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import audio_routing
from .audio import ProsodyController
from .automation import PredictiveHabitMatrix
from .const import CONF_BROADCAST_GROUP, CONF_HONORIFIC, DEFAULT_HONORIFIC, DOMAIN
from .diagnostics import InfrastructureTriage
from .intent import LocalIntentRouter
from .memory import LocalSemanticMemory
from .vision import SpatialContextEngine

_LOGGER = logging.getLogger(__name__)

SERVICE_SPEAK = "speak"
SERVICE_PROCESS_INTENT = "process_intent"

# ── Tunables ──────────────────────────────────────────────────────────────────
# TTS entity for tts.speak. Override per-install via runtime_config key
# "proactive_tts_entity" (panel Settings), else this default is used. Set this
# to YOUR configured TTS entity id (the custom Piper voice → typically tts.piper).
DEFAULT_TTS_ENTITY = "tts.piper"
DEFAULT_ANNOUNCE_PLAYER = ""            # optional fallback player when an area has none
MEDIA_DUCK_LEVEL = 0.10                 # spec background-duck floor — see _announce() note
AUDIT_INTERVAL = timedelta(minutes=15)
AUDIT_STARTUP_DELAY = timedelta(seconds=60)
AUDIT_TARGET_AREA = "office"            # ← set to your office area_id

# Predictive habit matrix: record occupancy each audit tick and surface likely
# upcoming actions. Pre-emptive *execution* is OFF by default — JARVIS earns
# autonomy; until then due preemptions are logged as suggestions only.
PREDICTOR_AUTOEXECUTE = False

# Spoken-duration estimate.
WORDS_PER_SECOND = 2.6                  # ≈ 156 wpm at normal rate
TTS_PADDING_S = 0.9
TTS_MIN_S = 1.5
TTS_MAX_S = 30.0

SPEAK_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Required("target_area"): cv.string,
        vol.Optional("critical", default=False): cv.boolean,
        vol.Optional("user_id"): cv.string,
        vol.Optional("expect_response", default=False): cv.boolean,
        vol.Optional("confirm_intent"): cv.string,
    }
)

PROCESS_INTENT_SCHEMA = vol.Schema(
    {
        vol.Required("phrase"): cv.string,
        vol.Required("target_area"): cv.string,
        vol.Optional("user_id"): cv.string,
    }
)

# Single shared controller; quiet hours fall back to its defaults (22→7), which
# match the integration's DEFAULT_OBSERVER_QUIET_START/END.
_PROSODY = ProsodyController()


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _resolve_honorific(hass: HomeAssistant, entry: ConfigEntry) -> str:
    return entry.options.get(
        CONF_HONORIFIC, entry.data.get(CONF_HONORIFIC, DEFAULT_HONORIFIC)
    )


def _resolve_tts_entity(hass: HomeAssistant) -> str:
    """Prefer a panel-configured TTS entity (runtime_config), else the default."""
    for data in hass.data.get(DOMAIN, {}).values():
        if isinstance(data, dict):
            rc = data.get("runtime_config", {})
            if isinstance(rc, dict) and rc.get("proactive_tts_entity"):
                return str(rc["proactive_tts_entity"])
    return DEFAULT_TTS_ENTITY


# ── Area / entity resolution ──────────────────────────────────────────────────
@callback
def _resolve_area_id(hass: HomeAssistant, target: str) -> str | None:
    """Accept an area_id or an area name and return the canonical area_id."""
    area_reg = ar.async_get(hass)
    if area_reg.async_get_area(target) is not None:
        return target
    by_name = area_reg.async_get_area_by_name(target)
    return by_name.id if by_name is not None else None


@callback
def _resolve_broadcast_speakers(hass: HomeAssistant) -> list[str]:
    """House-wide fallback, resolved exactly like the rest of JARVIS: a panel
    `announcement_speakers` override, else the configured `broadcast_group`, else
    every non-satellite speaker (via audio_routing.broadcast_target)."""
    for data in hass.data.get(DOMAIN, {}).values():
        if isinstance(data, dict):
            rc = data.get("runtime_config", {})
            speakers = rc.get("announcement_speakers") if isinstance(rc, dict) else None
            if speakers:
                valid = [s for s in speakers if hass.states.get(s)]
                if valid:
                    return valid
    group = ""
    for entry in hass.config_entries.async_entries(DOMAIN):
        group = (
            entry.options.get(CONF_BROADCAST_GROUP, entry.data.get(CONF_BROADCAST_GROUP, ""))
            or group
        )
        if group:
            break
    return audio_routing.broadcast_target(hass, broadcast_group=group)


@callback
def _resolve_targets(hass: HomeAssistant, area_id: str) -> tuple[list[str], str]:
    """Resolve announcement speakers through JARVIS's own routing.

    Primary: the speakers in the requested area (audio_routing.speakers_in_area),
    excluding listen-only satellites. If the area has none, fall back to the
    house broadcast set so an announcement is never silently dropped. Returns
    (targets, mode) where mode is 'area', 'broadcast', or 'none'.
    """
    area_speakers = [
        s for s in audio_routing.speakers_in_area(hass, area_id)
        if not s.startswith("assist_satellite.")
    ]
    if area_speakers:
        return area_speakers, "area"

    broadcast = [
        s for s in _resolve_broadcast_speakers(hass)
        if not s.startswith("assist_satellite.")
    ]
    if broadcast:
        return broadcast, "broadcast"
    if DEFAULT_ANNOUNCE_PLAYER:
        return [DEFAULT_ANNOUNCE_PLAYER], "broadcast"
    return [], "none"


@callback
def _build_telemetry(
    hass: HomeAssistant, area_id: str, targets: list[str], critical: bool
) -> dict:
    """Ambient telemetry for prosody. Light/noise come from sensors in the
    requested area (resolved with the same audio_routing.entity_area logic used
    for speakers); media activity comes from the resolved target speakers."""
    lux_vals: list[float] = []
    db_vals: list[float] = []

    for st in hass.states.async_all("sensor"):
        if audio_routing.entity_area(hass, st.entity_id) != area_id:
            continue
        eid = st.entity_id
        device_class = st.attributes.get("device_class")
        if device_class == "illuminance" or "lux" in eid or "illuminance" in eid:
            if (v := _as_float(st.state)) is not None:
                lux_vals.append(v)
        elif device_class == "sound_pressure" or any(
            k in eid for k in ("noise", "sound", "decibel", "_db")
        ):
            if (v := _as_float(st.state)) is not None:
                db_vals.append(v)

    media_active = any(
        (s := hass.states.get(t)) is not None and str(s.state).lower() == "playing"
        for t in targets
    )

    # Fuse spatial presence (Frigate + gaze + mmWave) to decide whether the
    # listener is attending closely enough that we can skip the preamble.
    spatial = SpatialContextEngine(hass).evaluate(area_id)

    return {
        "critical_alert": critical,
        "ambient_lux": min(lux_vals) if lux_vals else None,
        "ambient_db": max(db_vals) if db_vals else None,
        "media_active": media_active,
        "skip_preamble": spatial["skip_preamble"],
        "spatial_confidence": spatial["confidence"],
    }


# ── Announcement primitives ───────────────────────────────────────────────────
def _estimate_duration(message: str, speech_rate: float) -> float:
    words = max(1, len(message.split()))
    effective_wps = WORDS_PER_SECOND * max(speech_rate, 0.5)
    seconds = words / effective_wps + TTS_PADDING_S
    return max(TTS_MIN_S, min(seconds, TTS_MAX_S))


def _tts_options(profile: dict) -> dict:
    return {"rate": round(float(profile["speech_rate"]), 2)}


async def _set_volume(hass: HomeAssistant, entity_id: str, level: float) -> None:
    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": entity_id, "volume_level": max(0.0, min(1.0, level))},
        blocking=True,
    )


async def _speak_tts(
    hass: HomeAssistant, targets: list[str], message: str, profile: dict
) -> None:
    """Call tts.speak, retrying without options if the engine rejects them."""
    payload = {
        "entity_id": _resolve_tts_entity(hass),
        "media_player_entity_id": targets,
        "message": message,
    }
    try:
        await hass.services.async_call(
            "tts", "speak", {**payload, "options": _tts_options(profile)}, blocking=True
        )
    except vol.Invalid:
        _LOGGER.debug("TTS rejected options; retrying without them")
        await hass.services.async_call("tts", "speak", payload, blocking=True)


async def _announce(hass: HomeAssistant, message: str, area_id: str, critical: bool) -> None:
    """Shape, duck, speak, and restore — best-effort, always restoring volumes.

    Targets are resolved through audio_routing (speakers in the area, with a
    house-broadcast fallback), so jarvis.speak uses the same speaker selection as
    the rest of JARVIS. We duck the resolved targets to the computed profile
    volume for the announcement window (whisper ≈0.25 … critical =1.0) — not a
    flat 0.10, which would render an authoritative alert inaudible — and restore
    the original levels in a finally block.
    """
    targets, mode = _resolve_targets(hass, area_id)
    if not targets:
        _LOGGER.warning(
            "jarvis.speak: no speaker resolved for area '%s' (no area speaker and "
            "no broadcast/default fallback) — nothing to announce on", area_id,
        )
        return

    telemetry = _build_telemetry(hass, area_id, targets, critical)
    profile = _PROSODY.calculate_vocal_profile(telemetry)
    announce_volume = float(profile["volume"])

    # Duck/restore the speakers we actually announce through.
    original: dict[str, float] = {}
    for eid in targets:
        st = hass.states.get(eid)
        if st is not None and (v := _as_float(st.attributes.get("volume_level"))) is not None:
            original[eid] = v

    _LOGGER.debug(
        "jarvis.speak → area=%s mode=%s style=%s vol=%.2f targets=%s",
        area_id, mode, profile["style"], announce_volume, targets,
    )

    try:
        if profile["duck_media"] or original:
            for eid in original:
                try:
                    await _set_volume(hass, eid, announce_volume)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("jarvis.speak: failed to set volume for %s", eid)

        await _speak_tts(hass, targets, message, profile)
        await asyncio.sleep(_estimate_duration(message, float(profile["speech_rate"])))
    except Exception:  # noqa: BLE001
        _LOGGER.exception("jarvis.speak: announcement failed in area '%s'", area_id)
    finally:
        for eid, level in original.items():
            try:
                await _set_volume(hass, eid, level)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("jarvis.speak: failed to restore volume for %s", eid)


def _history_phrase(matches: list[dict], honorific: str) -> str:
    """A short clause folding prior occurrences into the spoken warning."""
    count = len(matches)
    if count <= 0:
        return ""
    if count == 1:
        return f" For context, {honorific.title()}, this has occurred once before."
    return f" For context, {honorific.title()}, this has occurred {count} times before."


async def _run_predictor(hass: HomeAssistant, predictor: PredictiveHabitMatrix) -> None:
    """Sample current occupancy into the habit matrix and surface due
    pre-emptions. Execution is gated behind PREDICTOR_AUTOEXECUTE (default off) —
    until JARVIS has earned that autonomy, candidates are logged as suggestions.
    """
    try:
        occupied = audio_routing.currently_occupied_areas(hass)
    except Exception:  # noqa: BLE001
        occupied = []
    for area in occupied:
        await hass.async_add_executor_job(predictor.record_event, f"{area}_entry")

    due = await hass.async_add_executor_job(predictor.due_preemptions)
    for item in due:
        if PREDICTOR_AUTOEXECUTE:
            _LOGGER.info(
                "Predictor: pre-empting %s (p=%.2f) — wire a per-action handler",
                item["key"], item["probability"],
            )
        else:
            _LOGGER.info(
                "Predictor suggestion: %s likely soon (p=%.2f); auto-execute off",
                item["key"], item["probability"],
            )


# ── Service registration ──────────────────────────────────────────────────────
def _intent_router(hass: HomeAssistant) -> LocalIntentRouter:
    """One shared router per HA instance so a feedback window opened by
    jarvis.speak survives until process_intent delivers the response."""
    store = hass.data.setdefault(DOMAIN, {})
    router = store.get("_intent_router")
    if router is None:
        router = LocalIntentRouter(hass)
        store["_intent_router"] = router
    return router


async def async_register_services(hass: HomeAssistant) -> None:
    """Register jarvis.speak and jarvis.process_intent. Idempotent — safe across
    multiple config entries."""
    if hass.services.has_service(DOMAIN, SERVICE_SPEAK):
        return

    async def _handle_speak(call: ServiceCall) -> None:
        message: str = call.data["message"]
        target: str = call.data["target_area"]
        critical: bool = call.data.get("critical", False)
        user_id: str | None = call.data.get("user_id")
        expect_response: bool = call.data.get("expect_response", False)
        confirm_intent: str | None = call.data.get("confirm_intent")

        area_id = _resolve_area_id(hass, target)
        if area_id is None:
            _LOGGER.warning("jarvis.speak: unknown area %r — ignoring", target)
            return
        if user_id:
            # Reserved for per-user biometric/profile filtering; threaded through
            # and logged until a profile store exists.
            _LOGGER.debug("jarvis.speak: addressed to user_id=%s", user_id)

        await _announce(hass, message, area_id, critical)

        # Optionally open a short voice-confirmation window for an actionable
        # announcement ("Shall I secure the garage, sir?").
        if expect_response and confirm_intent:
            try:
                await _intent_router(hass).open_feedback_window(
                    {"intent": confirm_intent, "area": area_id}
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("jarvis.speak: failed to open feedback window")

    async def _handle_process_intent(call: ServiceCall) -> None:
        phrase: str = call.data["phrase"]
        target: str = call.data["target_area"]
        user_id: str | None = call.data.get("user_id")

        area_id = _resolve_area_id(hass, target) or target
        router = _intent_router(hass)

        # If a confirmation window is open, an affirmative completes the pending
        # action; otherwise treat the phrase as a fresh local command.
        handled = await router.handle_voice_response(phrase)
        if handled.get("handled"):
            _LOGGER.info("jarvis.process_intent: confirmed → %s", handled)
            return
        result = await router.route(phrase, area_id, user_id=user_id)
        _LOGGER.info("jarvis.process_intent: %r → %s", phrase, result)

    hass.services.async_register(DOMAIN, SERVICE_SPEAK, _handle_speak, schema=SPEAK_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_PROCESS_INTENT, _handle_process_intent, schema=PROCESS_INTENT_SCHEMA
    )
    _LOGGER.info(
        "Registered services %s.%s and %s.%s",
        DOMAIN, SERVICE_SPEAK, DOMAIN, SERVICE_PROCESS_INTENT,
    )


# ── Entry wiring (called from __init__.py) ────────────────────────────────────
async def async_setup_proactive_audio(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the speak service and schedule the infrastructure audit. Unsubs
    are stored on the entry's data dict so async_unload_proactive_audio can
    cancel them alongside the integration's other listeners."""
    await async_register_services(hass)

    honorific = _resolve_honorific(hass, entry)
    memory = LocalSemanticMemory()
    predictor = PredictiveHabitMatrix()

    async def _run_audit(_now=None) -> None:
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if entry_data.get("_audit_running"):
            return  # don't overlap a slow announcement with the next tick
        entry_data["_audit_running"] = True
        try:
            verdict = InfrastructureTriage(hass, honorific=honorific).evaluate()
            if verdict["alert_required"]:
                message = verdict["message"]
                tags = verdict.get("tags", [])
                # Recall prior occurrences (file I/O off the event loop) and fold
                # them into the spoken warning.
                matches = await hass.async_add_executor_job(
                    memory.query_related_faults, tags
                )
                if matches:
                    message += _history_phrase(matches, honorific)
                _LOGGER.info("Infrastructure audit: %s", message)
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SPEAK,
                    {
                        "message": message,
                        "target_area": AUDIT_TARGET_AREA,
                        "critical": verdict["critical"],
                    },
                    blocking=False,
                )
                # Persist this occurrence for future recall.
                await hass.async_add_executor_job(
                    memory.commit_event, verdict["message"], tags
                )

            # Habit modelling: sample occupancy and surface likely upcoming actions.
            await _run_predictor(hass, predictor)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Infrastructure audit failed")
        finally:
            entry_data["_audit_running"] = False

    unsub_interval = async_track_time_interval(hass, _run_audit, AUDIT_INTERVAL)
    unsub_startup = async_call_later(
        hass, AUDIT_STARTUP_DELAY.total_seconds(), _run_audit
    )

    entry_data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    entry_data.setdefault("proactive_audio_unsubs", []).extend(
        [unsub_interval, unsub_startup]
    )
    _LOGGER.debug("Proactive audio scheduled (audit every %s)", AUDIT_INTERVAL)


async def async_unload_proactive_audio(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cancel the audit listeners and remove the service if no entry needs it."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    for cancel in entry_data.pop("proactive_audio_unsubs", []):
        try:
            if callable(cancel):
                cancel()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to cancel a proactive-audio listener")

    # Remove services only if no other loaded entry still wants them.
    others = [
        eid
        for eid, d in hass.data.get(DOMAIN, {}).items()
        if eid != entry.entry_id and isinstance(d, dict)
    ]
    if not others:
        for svc in (SERVICE_SPEAK, SERVICE_PROCESS_INTENT):
            if hass.services.has_service(DOMAIN, svc):
                hass.services.async_remove(DOMAIN, svc)
        hass.data.get(DOMAIN, {}).pop("_intent_router", None)
