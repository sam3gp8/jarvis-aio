"""JARVIS — Sentinel: proactive entity monitoring and anomaly alerting."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAST_SPEAKERS,
    CONF_TTS_ENGINE,
    ALL_SPEAKERS_VALUE,
    DEFAULT_TTS_ENGINE,
    JARVIS_PERSONA,
)
from .database import save_sentinel_event, save_message
from .directive_helper import build_system_prompt
from .tts_helper import resolve_tts_entity, async_announce

_LOGGER = logging.getLogger(__name__)

SENTINEL_MODEL = "llama-3.3-70b-versatile"

DEFAULT_RULES = [
    {
        "id": "door_left_open",
        "domain": "binary_sensor",
        "device_class": "door",
        "state": "on",
        "for_minutes": 10,
        "message": "{honorific}, {friendly_name} has been open for {minutes} minutes.",
    },
    {
        "id": "window_left_open",
        "domain": "binary_sensor",
        "device_class": "window",
        "state": "on",
        "for_minutes": 30,
        "message": "{honorific}, {friendly_name} has been open for {minutes} minutes.",
    },
    {
        "id": "garage_left_open",
        "domain": "binary_sensor",
        "device_class": "garage_door",
        "state": "on",
        "for_minutes": 15,
        "message": "{honorific}, {friendly_name} has been open for {minutes} minutes.",
    },
    {
        "id": "lock_unlocked",
        "domain": "lock",
        "state": "unlocked",
        "for_minutes": 20,
        "message": "{honorific}, {friendly_name} has been unlocked for {minutes} minutes.",
    },
    # NOTE: motion_after_hours / occupancy_after_hours rules REMOVED in v5.4.7.
    # They fired every time a human walked to the bathroom at night. If you
    # want night-motion alerts, add the rule explicitly in addon config under
    # sentinel_rules. Default is QUIET.
]


class JarvisSentinel:
    """Monitors entity states and triggers Jarvis announcements on anomalies."""

    def __init__(
        self,
        hass: HomeAssistant,
        groq_client,
        honorific: str,
        rules: list[dict] | None = None,
        entry: ConfigEntry | None = None,
    ) -> None:
        self.hass        = hass
        self._groq       = groq_client
        self._honorific  = honorific
        self._rules      = rules or DEFAULT_RULES
        self._entry      = entry
        self._active     = False
        self._state_start: dict[str, datetime] = {}
        self._unsubs: list = []
        self._entity_cache: list[str] = []

    # ── TTS helpers ───────────────────────────────────────────────────────────

    def _tts_entity(self) -> str | None:
        if not self._entry:
            return None
        configured = self._entry.options.get(CONF_TTS_ENGINE, DEFAULT_TTS_ENGINE)
        return resolve_tts_entity(self.hass, configured)

    def _speakers(self) -> list[str]:
        """Sentinel speakers — v5.3 uses broadcast_target from HA area registry."""
        if not self._entry:
            return []
        # v5.3: read broadcast_group, fall back to all media_players
        from .audio_routing import broadcast_target
        broadcast_group = self._entry.options.get(
            "broadcast_group", self._entry.data.get("broadcast_group", "")
        ) or None
        return broadcast_target(self.hass, broadcast_group=broadcast_group)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Begin monitoring."""
        if self._active:
            return
        self._active = True
        self._entity_cache = self._collect_entity_ids()
        self._refresh_subscription()  # sets up state-change listener

        # Periodic duration check (for rules with for_minutes)
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._check_durations, timedelta(seconds=60)
            )
        )

        # Every 5 minutes, rescan for new entities (auto-enroll new sensors).
        # When a door/window/motion sensor is paired after HA start, Sentinel
        # picks it up without requiring a restart.
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._refresh_entities, timedelta(minutes=5)
            )
        )

        _LOGGER.info("JARVIS Sentinel active — watching %d entities (auto-enroll enabled)",
                     len(self._entity_cache))

    def _refresh_subscription(self) -> None:
        """(Re)install the state change listener with the current entity list."""
        # Drop any existing state-change subscription
        for unsub in list(self._unsubs):
            try:
                if getattr(unsub, "__sentinel_state_sub", False):
                    unsub()
                    self._unsubs.remove(unsub)
            except Exception:
                pass

        if self._entity_cache:
            unsub = async_track_state_change_event(
                self.hass, self._entity_cache, self._handle_state_change
            )
            # Tag it so we can find and remove it on re-subscription
            try:
                unsub.__sentinel_state_sub = True
            except AttributeError:
                pass
            self._unsubs.append(unsub)

    @callback
    def _refresh_entities(self, now) -> None:  # noqa: ARG002
        """Periodically rescan — enroll newly paired sensors without restart."""
        new_list = self._collect_entity_ids()
        added   = set(new_list) - set(self._entity_cache)
        removed = set(self._entity_cache) - set(new_list)

        if added or removed:
            self._entity_cache = new_list
            self._refresh_subscription()
            if added:
                _LOGGER.info("JARVIS Sentinel: enrolled %d new entities: %s",
                             len(added), ", ".join(list(added)[:5]))
            if removed:
                _LOGGER.info("JARVIS Sentinel: de-enrolled %d entities", len(removed))

    async def async_stop(self) -> None:
        """Stop all listeners."""
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # pylint: disable=broad-except
                pass
        self._unsubs.clear()
        self._active = False
        _LOGGER.info("JARVIS Sentinel stopped.")

    # ── State tracking ────────────────────────────────────────────────────────

    @callback
    def _handle_state_change(self, event) -> None:
        """Called from the HA event loop — use async_create_task, not run_coroutine_threadsafe."""
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        state_val = new_state.state
        now = dt_util.utcnow().replace(tzinfo=None)

        for rule in self._rules:
            key = f"{entity_id}:{rule['id']}"
            if self._entity_matches_rule(entity_id, rule) and state_val == rule.get("state"):
                self._state_start.setdefault(key, now)
                # Instant time-window rules (no duration)
                if "time_window" in rule and "for_minutes" not in rule:
                    if self._in_time_window(rule["time_window"]):
                        self.hass.async_create_task(
                            self._announce_rule(entity_id, rule, minutes=0)
                        )
            else:
                self._state_start.pop(key, None)

    @callback
    def _check_durations(self, now) -> None:  # noqa: ARG002
        """Called every 60 s — check duration-based rules."""
        utcnow = dt_util.utcnow().replace(tzinfo=None)
        for rule in self._rules:
            threshold = rule.get("for_minutes")
            if not threshold:
                continue
            for entity_id in self._entity_cache:
                key = f"{entity_id}:{rule['id']}"
                started = self._state_start.get(key)
                if started is None:
                    continue
                elapsed = (utcnow - started).total_seconds() / 60
                if elapsed >= threshold:
                    self.hass.async_create_task(
                        self._announce_rule(entity_id, rule, minutes=int(elapsed))
                    )
                    self._state_start[key] = utcnow  # reset to avoid spam

    # ── Announcement ──────────────────────────────────────────────────────────

    async def _announce_rule(self, entity_id: str, rule: dict, minutes: int) -> None:
        # v5.5.1: Check runtime_config (panel toggles) first, then entry.options,
        # then entry.data. runtime_config is set by the panel's Settings toggles
        # and takes immediate effect without entry reload.
        if self._entry:
            from .const import DOMAIN
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
            rc = data.get("runtime_config", {}) if data else {}

            announcements_enabled = bool(
                rc.get("announcements_enabled",
                       self._entry.options.get("announcements_enabled",
                       self._entry.data.get("announcements_enabled", True)))
            )
            sentinel_enabled = bool(
                rc.get("sentinel_enabled",
                       self._entry.options.get("sentinel_enabled",
                       self._entry.data.get("sentinel_enabled", True)))
            )
            if not announcements_enabled:
                _LOGGER.debug(
                    "Sentinel: announcements globally disabled, skipping rule %s for %s",
                    rule.get("id"), entity_id,
                )
                return
            if not sentinel_enabled:
                _LOGGER.debug(
                    "Sentinel: sentinel_enabled=false, skipping rule %s for %s",
                    rule.get("id"), entity_id,
                )
                return

            # v5.6.0: Per-rule disable check
            try:
                disabled_raw = rc.get("disabled_sentinel_rules",
                    self._entry.options.get("disabled_sentinel_rules",
                    self._entry.data.get("disabled_sentinel_rules", "[]")))
                if isinstance(disabled_raw, str):
                    import json as _json
                    disabled_list = _json.loads(disabled_raw)
                elif isinstance(disabled_raw, list):
                    disabled_list = disabled_raw
                else:
                    disabled_list = []
                if rule.get("id") in disabled_list:
                    _LOGGER.debug("Sentinel: rule %s is individually disabled", rule.get("id"))
                    return
            except Exception:
                pass

        state = self.hass.states.get(entity_id)
        friendly_name = state.attributes.get("friendly_name", entity_id) if state else entity_id

        if "message" in rule:
            text = rule["message"].format(
                friendly_name=friendly_name,
                minutes=minutes,
                honorific=self._honorific,
            )
        else:
            text = await self._groq_line(entity_id, friendly_name, rule, minutes)

        save_sentinel_event(entity_id, rule["id"], text)
        save_message("assistant", f"[Sentinel] {text}", device_id="sentinel")
        # v5.4.8: persist to activity log for panel
        try:
            from .database import save_activity
            save_activity(
                entity_id=entity_id,
                category=rule.get("id", "sentinel"),
                urgency="medium",
                message=text,
                was_spoken=True,
                source="sentinel",
            )
        except Exception:
            pass
        await async_announce(self.hass, text, self._tts_entity(), self._speakers())

        # v5.6.5: Also send phone push notification for sentinel alerts
        try:
            notify_svc = None
            # Check runtime_config first (panel dropdown)
            from .const import DOMAIN
            for eid, rdata in self.hass.data.get(DOMAIN, {}).items():
                if isinstance(rdata, dict):
                    rc = rdata.get("runtime_config", {})
                    if rc.get("notify_service"):
                        notify_svc = rc["notify_service"]
                        break
            if not notify_svc and self._entry:
                notify_svc = self._entry.options.get(
                    "notify_service",
                    self._entry.data.get("notify_service", ""),
                )
            if notify_svc:
                domain, service = notify_svc.split(".", 1)
                await self.hass.services.async_call(
                    domain, service,
                    {"title": "JARVIS", "message": text},
                    blocking=False,
                )
        except Exception as exc:
            _LOGGER.debug("Sentinel phone notify failed: %s", exc)

    async def _groq_line(
        self, entity_id: str, friendly_name: str, rule: dict, minutes: int
    ) -> str:
        """Generate a Jarvis-voiced alert via Groq."""
        prompt = (
            f"Generate one concise JARVIS alert (under 25 words) telling "
            f"{self._honorific} that '{friendly_name}' has been "
            f"'{rule.get('state', 'unknown')}' for {minutes} minutes. "
            f"Speak as JARVIS — calm, precise, one dry remark if warranted."
        )
        try:
            task = (
                "You are generating a proactive sentinel alert. "
                "Your prime directive guides what deserves flagging — protect, steward, "
                "anticipate. Deliver the alert directly, no preamble."
            )
            system = build_system_prompt(self.hass, self._honorific, task)
            result = await self.hass.async_add_executor_job(
                lambda: self._groq.chat(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=60,
                )
            )
            return result["text"].strip()
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("JARVIS Sentinel LLM error: %s", exc)
            return f"{self._honorific}, {friendly_name} requires your attention."

    # ── Entity helpers ────────────────────────────────────────────────────────

    def _collect_entity_ids(self) -> list[str]:
        """Resolve entity IDs from rules — explicit or by domain/device_class scan."""
        ids: set[str] = set()
        for rule in self._rules:
            if rule.get("entity_id"):
                ids.add(rule["entity_id"])
            else:
                domain       = rule.get("domain")
                device_class = rule.get("device_class")
                for state in self.hass.states.async_all():
                    if domain and not state.entity_id.startswith(domain + "."):
                        continue
                    if device_class and state.attributes.get("device_class") != device_class:
                        continue
                    ids.add(state.entity_id)
        return list(ids)

    def _entity_matches_rule(self, entity_id: str, rule: dict) -> bool:
        if rule.get("entity_id") and rule["entity_id"] != entity_id:
            return False
        if rule.get("domain") and not entity_id.startswith(rule["domain"] + "."):
            return False
        if rule.get("device_class"):
            state = self.hass.states.get(entity_id)
            if state and state.attributes.get("device_class") != rule["device_class"]:
                return False
        return True

    def _in_time_window(self, window: dict) -> bool:
        now   = datetime.now().strftime("%H:%M")
        start = window["start"]
        end   = window["end"]
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end  # spans midnight
