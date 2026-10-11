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
import time
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
)
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import audio_routing
from .audio import NoiseGate, ProsodyController
from .automation import EntityLockRegistry, PredictiveHabitMatrix
from .boot_guard import AlertBuffer
from .const import CONF_BROADCAST_GROUP, CONF_HONORIFIC, DEFAULT_HONORIFIC, DOMAIN
from .diagnostics import FaultLog, InfrastructureTriage
from .intent import LocalIntentRouter
from .paths import config_path_str
from .state_ledger import StateLedger
from .vision import SpatialContextEngine
from .vision import volume_damping_factor as spatial_volume_damping

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
# JARVIS must not nag about infrastructure health. By default only *critical*
# conditions are spoken (minor/warning findings stay in diagnostics + the fault
# log), and even a critical one is not repeated until it clears or escalates —
# otherwise the same unresolved condition is announced every AUDIT_INTERVAL.
# Set the `infra_audit_speak_warnings` config key true to also speak warnings.
CONF_INFRA_SPEAK_WARNINGS = "infra_audit_speak_warnings"
INFRA_ALERT_REPEAT_COOLDOWN = timedelta(hours=6)
# Where the infrastructure-health audit speaks. Override per-install via the
# `infra_audit_area` config key (an area id/name/alias). Empty — the default —
# means "no fixed area": the audit broadcasts house-wide so an infra alert is
# never silently dropped (a hardcoded area that doesn't exist on the install
# used to drop every audit alert with "unknown area").
AUDIT_TARGET_AREA = ""
CONF_INFRA_AUDIT_AREA = "infra_audit_area"

# Predictive habit matrix: record occupancy each audit tick and surface likely
# upcoming actions. Pre-emptive *execution* is OFF by default — JARVIS earns
# autonomy; until then due preemptions are logged as suggestions only.
PREDICTOR_AUTOEXECUTE = False

# ── Phase P shadow+parity: household anticipation (observe-only) ────────────────
# Fold each audit tick's occupancy sample into the kernel household model and log
# what it WOULD anticipate — an occupancy rhythm (per-daypart occupancy) and a
# routine graph (recurring area transitions) — alongside the live
# PredictiveHabitMatrix. PARITY: each tick also compares the household model's
# "proactivity warranted?" verdict against the live predictor's (does it flag a
# due pre-emption?) and accumulates a rolling agreement tally, logged periodically.
# Observe-only throughout: the household model carries NO actuator, so this drives
# nothing and changes no live behaviour; it never raises into the audit tick.
# Kill-switched by the module flag below and the `household_shadow` config key
# (default on). This is the pure→shadow→parity climb for the `household` primitive
# (roadmap Phase P).
HOUSEHOLD_SHADOW = True
_HOUSEHOLD_WINDOW_MAX = 240       # bounded rolling observation window per entry
_HOUSEHOLD_LOG_EVERY = 12         # log the rollup at most every N quiet ticks

# Phase P ENFORCE (owner-authorized): when on, the household model's anticipation
# is promoted from observe-only to an AUTHORITATIVE proactive advisory — its
# suggestions are surfaced (logged at INFO as `household(proactive)`) as a live
# proactive voice, not merely "would-suggest". This AUGMENTS, never replaces, the
# PredictiveHabitMatrix: the predictor is left exactly as-is as the fail-safe
# heuristic floor, so a household fault or an empty model leaves today's behaviour
# untouched. Strictly advisory — a household Suggestion carries NO actuator (the
# structural invariant), so enforce changes only what JARVIS *proposes*, never what
# it does; proactive execution stays gated behind PREDICTOR_AUTOEXECUTE. Kill-switch
# back to parity: HOUSEHOLD_PROACTIVE_ENFORCE / the `household_proactive_enforce`
# config key.
HOUSEHOLD_PROACTIVE_ENFORCE = True


def _household_enabled() -> bool:
    if not HOUSEHOLD_SHADOW:
        return False
    try:
        from . import jarvis_config
        if jarvis_config.get("household_shadow", True) is False:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _household_proactive_enforce_enabled() -> bool:
    if not HOUSEHOLD_PROACTIVE_ENFORCE:
        return False
    try:
        from . import jarvis_config
        if jarvis_config.get("household_proactive_enforce", True) is False:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _household_parity_record(entry_data: dict, *, hh_fires: bool,
                             pred_fires: bool) -> dict:
    """Fold one tick's agreement into the rolling parity tally and return it.

    The two models answer the same question — *is proactivity warranted now?* — so
    agreement is simply whether their booleans match. We keep the breakdown
    (both-fire / both-quiet / each-only) so a divergence is visible before anyone
    considers the owner-gated enforce flip. Pure bookkeeping; never raises."""
    tally = entry_data.setdefault("_household_parity", {
        "total": 0, "agree": 0, "both_fire": 0, "both_quiet": 0,
        "hh_only": 0, "pred_only": 0})
    tally["total"] += 1
    if hh_fires and pred_fires:
        tally["both_fire"] += 1
        tally["agree"] += 1
    elif not hh_fires and not pred_fires:
        tally["both_quiet"] += 1
        tally["agree"] += 1
    elif hh_fires:
        tally["hh_only"] += 1
    else:
        tally["pred_only"] += 1
    return tally


def _emit_household_shadow(entry_data: dict, occupied_areas,
                           due_preemptions=None) -> None:
    """Append this tick's occupancy sample to a bounded rolling window, log what the
    kernel household model WOULD anticipate (occupancy rhythm + routine graph), and
    — when the live predictor's due pre-emptions are passed — record a log-only
    parity tally of the two models' "proactivity warranted?" verdicts.

    When HOUSEHOLD_PROACTIVE_ENFORCE is on (Phase P enforce), the model's
    suggestions are additionally surfaced at INFO as an AUTHORITATIVE
    `household(proactive)` advisory — a live proactive voice. This augments, never
    replaces, the predictor (left untouched as the fail-safe floor); and the
    suggestions carry no actuator, so it changes only what JARVIS proposes.

    Still advisory-only — the household model's ``Suggestion`` objects carry no
    actuator, so nothing here acts. Kill-switched, bounded, and defensive: any
    failure is swallowed, never propagated into the audit tick. Phase P
    shadow+parity+enforce.
    """
    if not _household_enabled():
        return
    try:
        from .kernel import household, space_time
        from homeassistant.util import dt as dt_util

        now = dt_util.now()
        areas = sorted(str(a) for a in (occupied_areas or []))
        # The primary occupied area is the routine-mining activity label; "away"
        # when the home reads empty. Consecutive ticks become area→area routines.
        activity = areas[0] if areas else "away"
        daypart = space_time.daypart_of(now.hour)
        obs = household.Observation(
            daypart=daypart, weekday=now.weekday(),
            occupied=bool(areas), activity=activity)

        window = entry_data.setdefault("_household_window", [])
        window.append(obs)
        if len(window) > _HOUSEHOLD_WINDOW_MAX:
            del window[: len(window) - _HOUSEHOLD_WINDOW_MAX]

        prev = entry_data.get("_household_last_activity", "")
        suggestions = household.anticipate(window, daypart=daypart, last_activity=prev)
        entry_data["_household_last_activity"] = activity

        # Parity: compare the two models' "proactivity warranted?" verdict. The
        # predictor fires when it flags any due pre-emption; the household model
        # fires when it would surface any suggestion.
        tally = None
        if due_preemptions is not None:
            tally = _household_parity_record(
                entry_data,
                hh_fires=bool(suggestions),
                pred_fires=bool(due_preemptions))

        # Phase P enforce: promote the model to an AUTHORITATIVE proactive voice —
        # surface its suggestions as a live advisory (the predictor is untouched as
        # the fail-safe floor). Advisory only; nothing actuates.
        if suggestions and _household_proactive_enforce_enabled():
            _LOGGER.info(
                "household(proactive): %s",
                [s.to_dict() for s in suggestions])

        ticks = entry_data.get("_household_ticks", 0) + 1
        entry_data["_household_ticks"] = ticks
        # Log whenever the model would suggest something, else only periodically so
        # a quiet home doesn't spam the log every AUDIT_INTERVAL.
        if suggestions or ticks % _HOUSEHOLD_LOG_EVERY == 0:
            rate = (round(tally["agree"] / tally["total"], 4)
                    if tally and tally["total"] else None)
            _LOGGER.info(
                "household(shadow): %s; would-suggest=%s; parity=%s agree=%s",
                household.summarize(window),
                [s.to_dict() for s in suggestions],
                tally, rate)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("household shadow skipped", exc_info=True)

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
        # Opt-in house-wide delivery: skip strict area resolution and announce via
        # the broadcast set. Used by the infra audit when no fixed area is
        # configured, so an alert is never dropped for a missing area.
        vol.Optional("broadcast", default=False): cv.boolean,
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


def _proximity_enabled() -> bool:
    """Whether distance-based TTS volume dampening is on (default yes). It is
    self-limiting — a no-op wherever no distance array is readable — so the
    default is safe, and this switch lets a user turn it off outright."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("proximity_volume", True))
    except Exception:
        return True


def _resolve_honorific(hass: HomeAssistant, entry: ConfigEntry) -> str:
    from . import jarvis_config
    return jarvis_config.runtime_get(hass, entry, CONF_HONORIFIC, DEFAULT_HONORIFIC)


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
def _area_slug(s: str) -> str:
    """Normalize an area label for tolerant matching: lower-cased, whitespace
    and underscores collapsed (so 'Home Office', 'home office' and
    'home_office' all compare equal)."""
    return "_".join(str(s or "").strip().lower().replace("_", " ").split())


def _resolve_area_id(hass: HomeAssistant, target: str) -> str | None:
    """Resolve a `jarvis.speak` target to a canonical HA area_id.

    Accepts an area_id, an area name, an area **alias**, or a slug/spacing
    variant of any of those. HA's own `async_get_area_by_name` only matches the
    canonical name, so a request naming a room by its alias or by a
    slug/spacing variant (e.g. "office" for an area whose id is a ULID and whose
    name is "Office", or "home office" for "Home Office") previously fell
    through and the announcement was silently dropped (issue #77). Returns None
    only when nothing matches."""
    if not target:
        return None
    area_reg = ar.async_get(hass)
    # 1. exact area_id
    if area_reg.async_get_area(target) is not None:
        return target
    # 2. canonical name (HA normalizes case/whitespace itself)
    by_name = area_reg.async_get_area_by_name(target)
    if by_name is not None:
        return by_name.id
    # 3. tolerant fallback: slug of id / name / any alias, across all areas.
    tgt = _area_slug(target)
    if tgt:
        for area in area_reg.async_list_areas():
            if _area_slug(getattr(area, "id", "")) == tgt or \
                    _area_slug(getattr(area, "name", "")) == tgt:
                return area.id
            for alias in (getattr(area, "aliases", None) or ()):
                if _area_slug(alias) == tgt:
                    return area.id
    return None


def _known_area_labels(hass: HomeAssistant) -> list[str]:
    """Area names for a helpful 'unknown area' log (so the failure explains
    itself rather than just naming the unmatched target — issue #77)."""
    try:
        return sorted(a.name for a in ar.async_get(hass).async_list_areas() if a.name)
    except Exception:  # noqa: BLE001
        return []


def _audit_speak_target(hass: HomeAssistant) -> dict:
    """Decide where the infrastructure audit speaks.

    If `infra_audit_area` is configured and resolves to a real area, speak there;
    otherwise broadcast house-wide. An infra alert is important, so it is never
    dropped just because no (or a non-existent) area was configured — the old
    hardcoded "office" default silently discarded every alert on installs without
    that area."""
    try:
        from . import jarvis_config
        configured = (jarvis_config.get(CONF_INFRA_AUDIT_AREA, AUDIT_TARGET_AREA)
                      or "").strip()
    except Exception:  # noqa: BLE001
        configured = AUDIT_TARGET_AREA
    if configured and _resolve_area_id(hass, configured) is not None:
        return {"target_area": configured}
    return {"target_area": "", "broadcast": True}


def _infra_speak_warnings(hass: HomeAssistant) -> bool:
    """Whether the infra audit should *speak* warning-level findings (not just
    criticals). Default False — warnings are recorded but not announced, so
    JARVIS doesn't narrate minor/degraded-visibility health every cycle."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get(CONF_INFRA_SPEAK_WARNINGS, False))
    except Exception:  # noqa: BLE001
        return False


def _infra_announce_decision(
    verdict: dict,
    *,
    speak_warnings: bool,
    last_sig,
    last_ts: float,
    now: float,
    cooldown_s: float,
):
    """Decide whether an infra-audit verdict should be spoken now.

    Pure policy (so it is unit-testable): speak only critical findings unless
    ``speak_warnings``; never repeat the same signature within ``cooldown_s``;
    always allow a fresh critical that escalated from a prior warning. Returns
    ``(announce: bool, signature)`` — store the signature + ``now`` as the new
    last-spoken state when ``announce`` is True."""
    critical = bool(verdict.get("critical"))
    tags = tuple(sorted(verdict.get("tags", [])))
    sig = ("critical" if critical else "warn", tags)
    if not (critical or speak_warnings):
        return False, sig
    escalated = critical and last_sig is not None and last_sig[0] != "critical"
    recently_spoken = sig == last_sig and (now - last_ts) < cooldown_s
    return (escalated or not recently_spoken), sig


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
    from . import jarvis_config
    for entry in hass.config_entries.async_entries(DOMAIN):
        group = (
            jarvis_config.runtime_get(hass, entry, CONF_BROADCAST_GROUP, "")
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

    # Differential noise compensation: discount running-appliance noise so a loud
    # dishwasher doesn't push prosody to project unnecessarily.
    raw_db = max(db_vals) if db_vals else None
    ambient_db = NoiseGate(hass).compensated_db(raw_db)

    return {
        "critical_alert": critical,
        "ambient_lux": min(lux_vals) if lux_vals else None,
        "ambient_db": ambient_db,
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
    try:
        from .audio_routing import drop_display_targets
        targets = drop_display_targets(hass, targets, "proactive_audio")
    except Exception as exc:
        _LOGGER.warning("JARVIS: display-target filter failed (proactive TTS may reach a screen): %s", exc)
    if not targets:
        return
    payload = {
        "entity_id": _resolve_tts_entity(hass),
        "media_player_entity_id": targets,
        "message": message,
    }
    try:
        await hass.services.async_call(
            "tts", "speak", {**payload, "options": _tts_options(profile)}, blocking=True
        )
    except (vol.Invalid, HomeAssistantError):
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

    # Proximity dampening: when high-resolution mmWave distance arrays show the
    # listener is right next to the room's speaker, drop the volume rather than
    # projecting at them. Never applied to a critical alert (those must stay
    # authoritative) and a no-op wherever no distance array is readable, so it
    # only ever *reduces* a non-urgent announcement in rooms wired for it.
    if not critical and _proximity_enabled():
        try:
            distance_m = SpatialContextEngine(hass).nearest_distance_m(area_id)
            factor = spatial_volume_damping(distance_m)
            if factor < 1.0:
                new_volume = round(announce_volume * factor, 3)
                _LOGGER.debug(
                    "jarvis.speak proximity: area=%s distance=%.2fm factor=%.2f "
                    "vol %.2f→%.2f", area_id, distance_m or -1.0, factor,
                    announce_volume, new_volume,
                )
                announce_volume = new_volume
        except Exception:  # noqa: BLE001 - proximity is a nicety, never a blocker
            _LOGGER.debug("proximity dampening skipped", exc_info=True)

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


async def _run_predictor(
    hass: HomeAssistant, predictor: PredictiveHabitMatrix
) -> tuple[list[str], list[dict]]:
    """Sample current occupancy into the habit matrix and surface due
    pre-emptions. Execution is gated behind PREDICTOR_AUTOEXECUTE (default off) —
    until JARVIS has earned that autonomy, candidates are logged as suggestions.

    Returns ``(occupied_areas, due_preemptions)`` so the caller can fold the same
    sample and the predictor's verdict into the Phase P household shadow/parity
    check without re-reading presence or recomputing the pre-emptions.
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
    return list(occupied), list(due)


# ── Service registration ──────────────────────────────────────────────────────
# ── Shared singletons ─────────────────────────────────────────────────────────
def _state_ledger(hass: HomeAssistant) -> StateLedger:
    """One shared write-ahead recovery ledger per HA instance."""
    store = hass.data.setdefault(DOMAIN, {})
    ledger = store.get("_state_ledger")
    if ledger is None:
        ledger = StateLedger(path=config_path_str("jarvis", "state_ledger.jsonl", hass=hass))
        store["_state_ledger"] = ledger
    return ledger


def _entity_locks(hass: HomeAssistant) -> EntityLockRegistry:
    """One shared entity-concurrency registry per HA instance."""
    store = hass.data.setdefault(DOMAIN, {})
    registry = store.get("_entity_locks")
    if registry is None:
        registry = EntityLockRegistry()
        store["_entity_locks"] = registry
    return registry


def _intent_router(hass: HomeAssistant) -> LocalIntentRouter:
    """One shared router per HA instance so a feedback window opened by
    jarvis.speak survives until process_intent delivers the response."""
    store = hass.data.setdefault(DOMAIN, {})
    router = store.get("_intent_router")
    if router is None:
        router = LocalIntentRouter(
            hass, ledger=_state_ledger(hass), mutex=_entity_locks(hass)
        )
        store["_intent_router"] = router
    return router


async def _reconcile_state_ledger(hass: HomeAssistant) -> list[dict]:
    """During boot, replay outstanding high-stakes intents and check whether the
    physical device actually reached the desired state — surfacing actions a
    crash or power loss interrupted. File reads run off-loop; state reads on-loop."""
    ledger = _state_ledger(hass)
    pending = await hass.async_add_executor_job(ledger.pending_intents)
    discrepancies: list[dict] = []
    for intent in pending:
        st = hass.states.get(intent["entity_id"])
        actual = st.state if st is not None else None
        if str(actual) != intent["desired_state"]:
            discrepancies.append({**intent, "actual": actual})
    for d in discrepancies:
        _LOGGER.warning(
            "State ledger: %s was meant to be '%s' before shutdown but is '%s' — "
            "a prior action may have been interrupted",
            d.get("entity_id"), d.get("desired_state"), d.get("actual"),
        )
    # Resolved or stale intents shouldn't linger across boots.
    await hass.async_add_executor_job(ledger.compact)
    return discrepancies


# ── Boot guard + alert queue ──────────────────────────────────────────────────
# Until the integration finishes initialising (and after any config-entry reload),
# jarvis.speak calls are buffered rather than dropped or fired into a half-built
# system, then replayed in order once JARVIS reports ready.
ALERT_BUFFER_KEY = "_alert_buffer"


def _alert_buffer(hass: HomeAssistant) -> AlertBuffer:
    store = hass.data.setdefault(DOMAIN, {})
    buffer = store.get(ALERT_BUFFER_KEY)
    if buffer is None:
        buffer = AlertBuffer()
        store[ALERT_BUFFER_KEY] = buffer
    return buffer


def _boot_ready(hass: HomeAssistant) -> bool:
    return _alert_buffer(hass).ready


async def _dispatch_speak(hass: HomeAssistant, data: dict) -> None:
    """Resolve the target area and deliver one announcement. Shared by the live
    service handler and the boot-queue drainer."""
    message: str = data["message"]
    target: str = data["target_area"]
    critical: bool = data.get("critical", False)
    user_id: str | None = data.get("user_id")
    expect_response: bool = data.get("expect_response", False)
    confirm_intent: str | None = data.get("confirm_intent")
    broadcast: bool = data.get("broadcast", False)

    if broadcast:
        # House-wide: an empty area id makes _resolve_targets fall through to the
        # broadcast set, so the announcement is never dropped for a missing area.
        area_id = ""
    else:
        area_id = _resolve_area_id(hass, target)
        if area_id is None:
            known = _known_area_labels(hass)
            _LOGGER.warning(
                "jarvis.speak: unknown area %r — ignoring (known areas: %s)",
                target, ", ".join(known) if known else "none registered")
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


def _boot_begin(hass: HomeAssistant) -> None:
    """Mark the integration as initialising (gates jarvis.speak). Idempotent and
    reload-safe — resets readiness so a reload re-gates until ready again."""
    _alert_buffer(hass).begin()


async def mark_boot_ready(hass: HomeAssistant) -> None:
    """Flip to ready and replay any alerts buffered during initialisation, in the
    order they arrived."""
    buffer = _alert_buffer(hass)

    async def _cb(data: dict) -> None:
        await _dispatch_speak(hass, data)

    replayed = await buffer.mark_ready(_cb)
    if replayed:
        _LOGGER.info("JARVIS ready — replayed %d buffered alert(s)", replayed)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register jarvis.speak and jarvis.process_intent. Idempotent — safe across
    multiple config entries."""
    if hass.services.has_service(DOMAIN, SERVICE_SPEAK):
        return

    async def _handle_speak(call: ServiceCall) -> None:
        # Boot guard: buffer until the integration is fully initialised.
        buffer = _alert_buffer(hass)
        if not buffer.ready:
            buffer.enqueue(dict(call.data))
            _LOGGER.info("jarvis.speak buffered — JARVIS still initialising")
            return
        await _dispatch_speak(hass, call.data)

    async def _handle_process_intent(call: ServiceCall) -> None:
        phrase: str = call.data["phrase"]
        target: str = call.data["target_area"]
        user_id: str | None = call.data.get("user_id")

        area_id = _resolve_area_id(hass, target) or target
        router = _intent_router(hass)

        # Shadow mode (kernel Phase 1): publish the voice turn and open a
        # correlation scope so any decision recorded while routing links to it.
        # Entirely best-effort — intent handling is unaffected if it fails.
        import contextlib
        _scope = contextlib.nullcontext()
        try:
            import uuid
            from .events import publish as _publish_event
            from .kernel import correlation, from_voice_turn
            _corr = uuid.uuid4().hex
            _publish_event(hass, from_voice_turn(
                phrase, speaker=user_id, location=area_id, correlation_id=_corr))
            _scope = correlation.scope(_corr)
        except Exception:
            _scope = contextlib.nullcontext()

        with _scope:
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

    # Boot guard: gate jarvis.speak until this setup completes (reload-safe).
    _boot_begin(hass)

    honorific = _resolve_honorific(hass, entry)
    fault_log = FaultLog(path=config_path_str("jarvis", "fault_history.json", hass=hass))
    predictor = PredictiveHabitMatrix(path=config_path_str("jarvis", "habit_matrix.json", hass=hass))

    async def _run_audit(_now=None) -> None:
        if not _boot_ready(hass):
            return  # hold monitoring until the integration reports ready
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if entry_data.get("_audit_running"):
            return  # don't overlap a slow announcement with the next tick
        entry_data["_audit_running"] = True
        try:
            # Zorin/Linux host telemetry (CPU temp, memory pressure, NVMe I/O)
            # is read off the event loop — the reads hit /proc and /sys and the
            # NVMe probe samples twice — then folded into the audit verdict so
            # host stress affecting AI latency is spoken like any other fault.
            host_metrics = None
            try:
                from . import host_telemetry
                if host_telemetry.is_enabled():
                    host_metrics = await hass.async_add_executor_job(
                        host_telemetry.read_metrics
                    )
            except Exception:  # noqa: BLE001
                host_metrics = None
            verdict = InfrastructureTriage(hass, honorific=honorific).evaluate(host_metrics)
            if verdict["alert_required"]:
                message = verdict["message"]
                tags = verdict.get("tags", [])
                critical = bool(verdict["critical"])

                # Decide whether to SPEAK. JARVIS should not narrate minor
                # (warning-level) infrastructure health, nor re-announce the same
                # unresolved condition every cycle. By default only criticals are
                # spoken; a given alert is not repeated until it clears or
                # escalates (warning→critical), within a cooldown window.
                now = time.monotonic()
                announce, sig = _infra_announce_decision(
                    verdict,
                    speak_warnings=_infra_speak_warnings(hass),
                    last_sig=entry_data.get("_infra_last_sig"),
                    last_ts=entry_data.get("_infra_last_spoken_ts", 0.0),
                    now=now,
                    cooldown_s=INFRA_ALERT_REPEAT_COOLDOWN.total_seconds(),
                )

                if announce:
                    # Recall prior occurrences (file I/O off the loop) and fold
                    # them into the spoken warning.
                    matches = await hass.async_add_executor_job(
                        fault_log.query_related_faults, tags
                    )
                    spoken = message + (_history_phrase(matches, honorific) if matches else "")
                    _LOGGER.info("Infrastructure audit (announced): %s", spoken)
                    speak_data = _audit_speak_target(hass)
                    speak_data["message"] = spoken
                    speak_data["critical"] = critical
                    await hass.services.async_call(
                        DOMAIN, SERVICE_SPEAK, speak_data, blocking=False,
                    )
                    entry_data["_infra_last_sig"] = sig
                    entry_data["_infra_last_spoken_ts"] = now
                    # Persist this occurrence for future recall (only on announce,
                    # so the fault log isn't appended every quiet cycle).
                    await hass.async_add_executor_job(
                        fault_log.commit_event, verdict["message"], tags
                    )
                else:
                    _LOGGER.debug(
                        "Infrastructure audit (not announced — %s): %s",
                        "critical cooldown" if critical else "warning below speak threshold",
                        message,
                    )

            # Habit modelling: sample occupancy and surface likely upcoming actions.
            occupied_areas, due_preemptions = await _run_predictor(hass, predictor)
            # Phase P shadow+parity: fold the same occupancy sample into the kernel
            # household model, log what it would anticipate, and record a log-only
            # agreement tally vs the predictor's verdict (observe-only).
            _emit_household_shadow(entry_data, occupied_areas, due_preemptions)
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

    # Recover from any high-stakes action interrupted by a crash before opening
    # the gate, then replay anything buffered during initialisation.
    try:
        await _reconcile_state_ledger(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("State ledger reconciliation failed")
    await mark_boot_ready(hass)


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
        store = hass.data.get(DOMAIN, {})
        store.pop("_intent_router", None)
        store.pop("_state_ledger", None)
        store.pop("_entity_locks", None)
        store.pop(ALERT_BUFFER_KEY, None)
