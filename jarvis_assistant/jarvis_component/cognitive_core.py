"""
JARVIS — Cognitive Core (v5.8.03).

The autonomous AI brain. Runs continuously in the background,
monitoring home state, managing safety, learning patterns, and making
decisions — emulating Tony Stark's JARVIS from the MCU.

Architecture:
  - 30-second evaluation loop: reviews full home state each tick
  - Safety manager: pipe freeze prevention, unauthorized entry,
    nighttime lockdown
  - Ignore system: honors "ignore X for Y duration" commands
  - Outdoor event filter: only surfaces notable events
  - State logger: records every meaningful state change for
    pattern learning (separate module)
  - Suggestion engine: proposes automations based on observed patterns

Philosophy:
  - Suggest, don't act (initially) — earn trust first
  - Safety overrides: pipe freeze, intrusion → act immediately
  - Nighttime lockdown: locks/doors → act automatically
  - Everything else: observe, learn, suggest
  - Approved suggestions become automations over time
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

TICK_INTERVAL = 30  # seconds between evaluations
LOCKDOWN_CHECK_INTERVAL = 300  # 5 min between lockdown scans
# Formal lockdown state
ALARM_ARMED_STATES = {
    "armed_home", "armed_away", "armed_night", "armed_vacation",
    "armed_custom_bypass",
}
LOCKDOWN_DOOR_COVER_CLASSES = {"door", "garage", "garage_door"}
LOCKDOWN_BREACH_COOLDOWN = 120  # seconds between repeat breach announcements
LOCKDOWN_STATE_PATH = "/config/jarvis/lockdown_state.json"  # survives reboots/reloads
FREEZE_WARN_TEMP_F = 35  # outdoor temp (°F) that triggers pipe concern
FREEZE_CRITICAL_TEMP_F = 20  # act immediately
IGNORE_FILE = "/config/.jarvis_ignore_rules.json"

# ── Proactive intelligence (v5.9.07) ────────────────────────────────────────
PROACTIVE_CHECK_INTERVAL = 120   # 2 min between comfort/efficiency scans
PROACTIVE_OFFER_COOLDOWN = 1800  # 30 min before re-offering the same thing
DARK_LUX_THRESHOLD = 15          # below this lux + occupancy → offer lights
STALE_LIGHT_MINUTES = 90         # light on this long in an empty room → flag
HIGH_TEMP_AWAY_F = 78            # cooling running while away → efficiency flag
LOW_TEMP_AWAY_F = 62             # heating running while away → efficiency flag

# ── Graduated autonomy (v5.9.07) ────────────────────────────────────────────
# A suggestion that the user approves repeatedly earns the right to auto-apply.
AUTONOMY_TRUST_THRESHOLD = 3     # approvals of same pattern → auto-execute tier
AUTONOMY_MIN_CONFIDENCE = 0.80   # confidence floor for auto-execution
AUTONOMY_FILE = "/config/jarvis/autonomy_grants.json"


# ── Ignore System ───────────────────────────────────────────────────────────

@dataclass
class IgnoreRule:
    entity_pattern: str   # entity_id or glob pattern ("binary_sensor.garage*")
    reason: str
    expires_at: float     # unix timestamp, 0 = permanent until cleared
    created_at: float = 0.0

    def is_expired(self) -> bool:
        if self.expires_at == 0:
            return False
        return time.time() > self.expires_at

    def matches(self, entity_id: str) -> bool:
        import fnmatch
        return fnmatch.fnmatch(entity_id, self.entity_pattern)


class IgnoreManager:
    """Manages entity/event ignore rules with duration."""

    def __init__(self):
        self._rules: list[IgnoreRule] = []
        self._load()

    def _load(self):
        try:
            if os.path.exists(IGNORE_FILE):
                with open(IGNORE_FILE) as f:
                    data = json.load(f)
                self._rules = [
                    IgnoreRule(**r) for r in data
                    if not IgnoreRule(**r).is_expired()
                ]
        except Exception:
            self._rules = []

    def _save(self):
        try:
            data = [
                {
                    "entity_pattern": r.entity_pattern,
                    "reason": r.reason,
                    "expires_at": r.expires_at,
                    "created_at": r.created_at,
                }
                for r in self._rules if not r.is_expired()
            ]
            with open(IGNORE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            _LOGGER.warning("Failed to save ignore rules: %s", exc)

    def add(self, entity_pattern: str, duration_minutes: int = 0,
            reason: str = "") -> IgnoreRule:
        expires = (time.time() + duration_minutes * 60) if duration_minutes > 0 else 0
        rule = IgnoreRule(
            entity_pattern=entity_pattern,
            reason=reason,
            expires_at=expires,
            created_at=time.time(),
        )
        self._rules.append(rule)
        self._save()
        _LOGGER.info(
            "Cognitive: ignore '%s' for %s (%s)",
            entity_pattern,
            f"{duration_minutes}min" if duration_minutes else "indefinitely",
            reason,
        )
        return rule

    def remove(self, entity_pattern: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.entity_pattern != entity_pattern]
        if len(self._rules) < before:
            self._save()
            return True
        return False

    def clear_all(self):
        self._rules.clear()
        self._save()

    def is_ignored(self, entity_id: str) -> bool:
        self._rules = [r for r in self._rules if not r.is_expired()]
        return any(r.matches(entity_id) for r in self._rules)

    def list_rules(self) -> list[dict]:
        self._rules = [r for r in self._rules if not r.is_expired()]
        return [
            {
                "pattern": r.entity_pattern,
                "reason": r.reason,
                "expires_at": r.expires_at,
                "remaining_min": max(0, int((r.expires_at - time.time()) / 60))
                if r.expires_at > 0 else "permanent",
            }
            for r in self._rules
        ]


# ── Outdoor Event Filter ────────────────────────────────────────────────────

_NOTABLE_OUTDOOR = {
    "person": True,       # always notable
    "vehicle": False,     # only if driveway/property
    "package": True,
    "mail": True,
    "animal": False,      # usually not notable
    "damage": True,
}

_OUTDOOR_AREAS = {"backyard", "front_yard", "front_door", "driveway",
                   "patio", "porch", "side_yard", "deck"}


def is_outdoor_notable(entity_id: str, area_name: str,
                       detection_type: str = "motion") -> bool:
    """Decide if an outdoor event is worth surfacing."""
    area_lower = area_name.lower().replace(" ", "_")
    if area_lower not in _OUTDOOR_AREAS:
        return False  # Not recognized as outdoor

    # Person in backyard/property = always notable
    if detection_type in ("person", "package", "mail", "damage"):
        return True

    # Generic motion outdoors = not notable (wind, animals, cars passing)
    return False


# ── Safety Manager ──────────────────────────────────────────────────────────

class SafetyManager:
    """Monitors for safety-critical conditions and acts."""

    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self._last_freeze_alert = 0.0
        self._last_lockdown_check = 0.0
        self._last_intrusion_alert = 0.0
        self._freeze_warned = False

    async def tick(self, sleeping: bool, anyone_home: bool) -> list[dict]:
        """Run all safety checks. Returns list of actions taken."""
        actions = []
        now = time.time()

        # ── Pipe freeze prevention ──────────────────────────────────
        freeze_action = await self._check_freeze()
        if freeze_action:
            actions.append(freeze_action)

        # ── Unauthorized entry detection ────────────────────────────
        # Only when residents are CONFIDENTLY away (tracked away / armed-away) or
        # asleep — never on the mere absence of occupancy, which falsely fires when
        # someone is home but untracked.
        if self._residents_away() or sleeping:
            intrusion = await self._check_intrusion(anyone_home, sleeping)
            if intrusion:
                actions.append(intrusion)

        # ── Nighttime lockdown ──────────────────────────────────────
        # Skipped when a formal lockdown is already active (it handles securing).
        if (sleeping and not is_lockdown()
                and (now - self._last_lockdown_check) > LOCKDOWN_CHECK_INTERVAL):
            self._last_lockdown_check = now
            lockdown = await self._nighttime_lockdown()
            if lockdown:
                actions.extend(lockdown)

        return actions

    async def _check_freeze(self) -> Optional[dict]:
        """Monitor outdoor temperature for pipe freeze risk."""
        now = time.time()
        if (now - self._last_freeze_alert) < 3600:  # 1hr cooldown
            return None

        outdoor_temp = None
        # Check weather entity
        for state in self.hass.states.async_all("weather"):
            temp = state.attributes.get("temperature")
            if temp is not None:
                outdoor_temp = float(temp)
                break

        # Check outdoor temp sensors
        if outdoor_temp is None:
            for state in self.hass.states.async_all("sensor"):
                if state.attributes.get("device_class") != "temperature":
                    continue
                eid = state.entity_id.lower()
                fname = (state.attributes.get("friendly_name") or "").lower()
                if "outdoor" in eid or "outside" in eid or "outdoor" in fname:
                    try:
                        outdoor_temp = float(state.state)
                    except (ValueError, TypeError):
                        pass
                    break

        if outdoor_temp is None:
            return None

        honorific = self.config.get("honorific", "sir")

        if outdoor_temp <= FREEZE_CRITICAL_TEMP_F:
            self._last_freeze_alert = now
            return {
                "type": "freeze_critical",
                "urgency": "critical",
                "message": (
                    f"{honorific.title()}, outdoor temperature has dropped to "
                    f"{outdoor_temp}°F. Pipe freeze risk is severe. I recommend "
                    f"opening cabinet doors near exterior walls and confirming "
                    f"heat is set to at least 55°F."
                ),
                "auto_act": True,  # Safety override — act without approval
            }
        elif outdoor_temp <= FREEZE_WARN_TEMP_F and not self._freeze_warned:
            self._freeze_warned = True
            self._last_freeze_alert = now
            return {
                "type": "freeze_warning",
                "urgency": "high",
                "message": (
                    f"{honorific.title()}, outdoor temperature is {outdoor_temp}°F. "
                    f"I'm monitoring for pipe freeze risk."
                ),
                "auto_act": False,
            }
        elif outdoor_temp > FREEZE_WARN_TEMP_F + 5:
            self._freeze_warned = False

        return None

    async def _check_intrusion(self, anyone_home: bool,
                                sleeping: bool) -> Optional[dict]:
        """Check for unauthorized entry when away or asleep."""
        now = time.time()
        if (now - self._last_intrusion_alert) < 300:  # 5min cooldown
            return None

        honorific = self.config.get("honorific", "sir")

        # Check for motion in non-bedroom areas during sleep
        # or ANY motion when nobody's home
        for state in self.hass.states.async_all("binary_sensor"):
            dc = state.attributes.get("device_class", "")
            if dc not in ("motion", "occupancy", "presence"):
                continue
            if state.state != "on":
                continue

            eid = state.entity_id.lower()
            fname = (state.attributes.get("friendly_name") or "").lower()

            # Skip outdoor sensors
            if any(kw in eid or kw in fname for kw in
                   ("outdoor", "outside", "backyard", "front_yard",
                    "driveway", "porch")):
                continue

            # Skip bedroom sensors during sleep (that's expected)
            if sleeping:
                bedroom_areas = self.config.get("bedroom_areas", [])
                # Check if this sensor is in a bedroom area
                is_bedroom = False
                try:
                    from homeassistant.helpers import (
                        entity_registry as er, device_registry as dr,
                        area_registry as areg,
                    )
                    ent_reg = er.async_get(self.hass)
                    dev_reg = dr.async_get(self.hass)
                    entry = ent_reg.async_get(state.entity_id)
                    if entry:
                        area_id = entry.area_id
                        if not area_id and entry.device_id:
                            device = dev_reg.async_get(entry.device_id)
                            area_id = device.area_id if device else None
                        if area_id and area_id in bedroom_areas:
                            is_bedroom = True
                except Exception:
                    pass
                if is_bedroom:
                    continue

            away = self._residents_away()
            if away or sleeping:
                self._last_intrusion_alert = now
                where = state.attributes.get('friendly_name', state.entity_id)
                if away:
                    msg = (f"{honorific.title()}, motion detected at {where} "
                           f"while no one is home. Investigating.")
                    urg = "critical"
                else:  # asleep, motion outside the bedrooms
                    msg = (f"{honorific.title()}, motion detected at {where} "
                           f"while the household is asleep. Investigating.")
                    urg = "high"
                return {
                    "type": "intrusion_away" if away else "intrusion_sleep",
                    "urgency": urg,
                    "message": msg,
                    "auto_act": True,
                    "entity_id": state.entity_id,
                }

        return None

    async def _nighttime_lockdown(self) -> list[dict]:
        """Check and secure all locks and doors during sleep."""
        actions = []
        honorific = self.config.get("honorific", "sir")

        # Check locks
        unlocked = []
        for state in self.hass.states.async_all("lock"):
            if state.state == "unlocked":
                eid = state.entity_id
                fname = state.attributes.get("friendly_name", eid)
                # Auto-lock
                try:
                    await self.hass.services.async_call(
                        "lock", "lock", {"entity_id": eid}, blocking=True,
                    )
                    unlocked.append(fname)
                    _LOGGER.info("Cognitive lockdown: locked %s", eid)
                except Exception as exc:
                    _LOGGER.warning("Cognitive lockdown: failed to lock %s: %s", eid, exc)

        # Check covers/garage
        open_covers = []
        for state in self.hass.states.async_all("cover"):
            if state.state == "open":
                eid = state.entity_id
                fname = state.attributes.get("friendly_name", eid)
                try:
                    await self.hass.services.async_call(
                        "cover", "close_cover", {"entity_id": eid}, blocking=True,
                    )
                    open_covers.append(fname)
                    _LOGGER.info("Cognitive lockdown: closed %s", eid)
                except Exception as exc:
                    _LOGGER.warning("Cognitive lockdown: failed to close %s: %s", eid, exc)

        if unlocked or open_covers:
            parts = []
            if unlocked:
                parts.append(f"locked {', '.join(unlocked)}")
            if open_covers:
                parts.append(f"closed {', '.join(open_covers)}")
            actions.append({
                "type": "lockdown",
                "urgency": "low",
                "message": (
                    f"{honorific.title()}, nighttime lockdown: {' and '.join(parts)}. "
                    f"The house is secured."
                ),
                "auto_act": True,
            })

        return actions

    def _residents_away(self) -> bool:
        """Confident 'the residents are away' — for intrusion only.

        Based on tracked presence (person / device_tracker) or an explicitly
        armed-away alarm, NEVER on motion/occupancy: intrusion exists to judge
        motion, so motion cannot also be the signal that says whether anyone is
        home. Crucially, the ABSENCE of tracking is not 'away' — with no person/
        device_tracker entities we cannot claim the house is empty, so this returns
        False and motion is never treated as an intruder. That is what prevents the
        false "motion … while no one is home" alerts when someone is home but their
        phone isn't tracked."""
        # A resident's device/person reading 'home' wins outright.
        for st in self.hass.states.async_all("person"):
            if str(st.state).lower() == "home":
                return False
        for st in self.hass.states.async_all("device_tracker"):
            if str(st.state).lower() == "home":
                return False
        # An intentionally armed-away alarm is a strong 'away' signal.
        for st in self.hass.states.async_all("alarm_control_panel"):
            if str(st.state).lower() in ("armed_away", "armed_vacation"):
                return True
        # Otherwise, only 'away' if presence is actually tracked and reads away.
        tracked = False
        for st in self.hass.states.async_all("person"):
            tracked = True
        for st in self.hass.states.async_all("device_tracker"):
            if str(st.state).lower() in ("home", "not_home", "away"):
                tracked = True
        return tracked


# ── Lockdown (v5.9.36) ──────────────────────────────────────────────────────

class LockdownManager:
    """
    Formal lockdown state — engaged when the alarm is armed or on explicit
    request. On engage it LOCKS every lock and CLOSES every open door/garage
    cover, and snapshots the windows that are already open so they're IGNORED
    for the duration (knowingly left open). While active it actively re-secures
    any door reopened or lock unlocked (a breach), and flags any NEW window that
    opens (it can't close a window sensor, but it warns). Auto-disengages when
    the alarm is disarmed — but only if it was the alarm that engaged it; a
    manually-requested lockdown stays until explicitly lifted.
    """

    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self.active = False
        self.since = 0.0
        self.reason = ""
        self.auto = False                 # engaged by the alarm (auto-lift on disarm)
        self.exempt_windows: set = set()   # windows open at engage / adopted as intentional
        self._window_alerted: set = set()  # windows already warned about this lockdown
        self._last_breach_alert = 0.0
        # When the user manually lifts lockdown while the alarm is still armed,
        # this suppresses auto re-engage until the alarm is disarmed and re-armed
        # — so "exit lockdown" from the UI actually keeps you out.
        self._auto_suppressed = False
        # Restore across restarts so a pre-existing exempt window (or a manual
        # exit) isn't lost on a reboot/integration reload.
        self._load_state()

    def _load_state(self) -> None:
        try:
            if not os.path.exists(LOCKDOWN_STATE_PATH):
                return
            with open(LOCKDOWN_STATE_PATH) as f:
                d = json.load(f)
            self._auto_suppressed = bool(d.get("auto_suppressed", False))
            if d.get("active"):
                self.active = True
                self.since = d.get("since", time.time())
                self.reason = d.get("reason", "restored")
                self.auto = bool(d.get("auto", False))
                self.exempt_windows = set(d.get("exempt_windows", []))
                _LOGGER.warning(
                    "Lockdown state RESTORED (auto=%s, %d exempt windows)",
                    self.auto, len(self.exempt_windows))
        except Exception as exc:
            _LOGGER.warning("Lockdown state restore failed: %s", exc)

    def _persist_sync(self) -> None:
        try:
            os.makedirs(os.path.dirname(LOCKDOWN_STATE_PATH), exist_ok=True)
            tmp = LOCKDOWN_STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({
                    "active": self.active,
                    "since": self.since,
                    "reason": self.reason,
                    "auto": self.auto,
                    "exempt_windows": sorted(self.exempt_windows),
                    "auto_suppressed": self._auto_suppressed,
                }, f)
            os.replace(tmp, LOCKDOWN_STATE_PATH)
        except Exception as exc:
            _LOGGER.debug("Lockdown state persist failed: %s", exc)

    async def _persist(self) -> None:
        try:
            await self.hass.async_add_executor_job(self._persist_sync)
        except Exception:
            pass

    def status(self) -> dict:
        return {
            "active": self.active,
            "since": self.since,
            "reason": self.reason,
            "auto": self.auto,
            "exempt_windows": len(self.exempt_windows),
        }

    def _alarm_armed(self) -> bool:
        for st in self.hass.states.async_all("alarm_control_panel"):
            if st.state in ALARM_ARMED_STATES:
                return True
        return False

    def _anyone_home(self) -> bool:
        """True if anyone is home — by tracked presence OR live occupancy. Used by
        lockdown / efficiency checks, where active occupancy legitimately means
        'someone is home' (these checks are not motion-triggered, so counting
        occupancy here is safe). Intrusion deliberately does NOT use this — it uses
        _residents_away, because an intruder's own motion would otherwise mask the
        alarm."""
        for st in self.hass.states.async_all("person"):
            if str(st.state).lower() == "home":
                return True
        for st in self.hass.states.async_all("device_tracker"):
            if str(st.state).lower() == "home":
                return True
        occ_on = ("on", "detected", "occupied", "home", "true")
        for st in self.hass.states.async_all("binary_sensor"):
            if (st.attributes.get("device_class") in ("occupancy", "motion", "presence")
                    and str(st.state).lower() in occ_on):
                return True
        return False

    def _open_windows(self) -> set:
        out = set()
        for st in self.hass.states.async_all("binary_sensor"):
            if st.attributes.get("device_class") == "window" and st.state == "on":
                out.add(st.entity_id)
        for st in self.hass.states.async_all("cover"):
            if st.attributes.get("device_class") == "window" and st.state == "open":
                out.add(st.entity_id)
        return out

    async def _lock_all(self) -> list:
        locked = []
        for st in self.hass.states.async_all("lock"):
            if st.state == "unlocked":
                eid = st.entity_id
                fname = st.attributes.get("friendly_name", eid)
                try:
                    await self.hass.services.async_call(
                        "lock", "lock", {"entity_id": eid}, blocking=True)
                    locked.append(fname)
                    _LOGGER.info("Lockdown: locked %s", eid)
                except Exception as exc:
                    _LOGGER.warning("Lockdown: failed to lock %s: %s", eid, exc)
        return locked

    async def _close_doors(self) -> list:
        closed = []
        for st in self.hass.states.async_all("cover"):
            dc = st.attributes.get("device_class")
            if st.state == "open" and dc in LOCKDOWN_DOOR_COVER_CLASSES:
                eid = st.entity_id
                fname = st.attributes.get("friendly_name", eid)
                try:
                    await self.hass.services.async_call(
                        "cover", "close_cover", {"entity_id": eid}, blocking=True)
                    closed.append(fname)
                    _LOGGER.info("Lockdown: closed %s", eid)
                except Exception as exc:
                    _LOGGER.warning("Lockdown: failed to close %s: %s", eid, exc)
        return closed

    async def engage(self, reason: str, auto: bool = False) -> Optional[dict]:
        if self.active:
            return None
        self.active = True
        self.since = time.time()
        self.reason = reason
        self.auto = auto
        self.exempt_windows = self._open_windows()   # leave these as-is
        self._window_alerted = set()
        self._last_breach_alert = 0.0
        honorific = self.config.get("honorific", "sir")
        locked = await self._lock_all()
        closed = await self._close_doors()
        parts = []
        if locked:
            parts.append(f"locked {', '.join(locked)}")
        if closed:
            parts.append(f"closed {', '.join(closed)}")
        secured = (" — " + " and ".join(parts) + ".") if parts else " — all doors already secure."
        ignored = ""
        if self.exempt_windows:
            n = len(self.exempt_windows)
            ignored = f" {n} window{'s' if n != 1 else ''} already open will be left as-is."
        _LOGGER.warning(
            "Lockdown ENGAGED (%s): locked=%s closed=%s exempt_windows=%d",
            reason, locked, closed, len(self.exempt_windows))
        await self._persist()
        return {
            "type": "lockdown_engaged",
            "urgency": "high",
            "message": f"{honorific.title()}, lockdown engaged{secured}{ignored}",
            "auto_act": True,
        }

    async def disengage(self, reason: str, manual: bool = False) -> Optional[dict]:
        if not self.active:
            return None
        # If the user manually lifts lockdown while the alarm is still armed,
        # remember not to auto re-engage until the alarm is disarmed/re-armed.
        if manual and self._alarm_armed():
            self._auto_suppressed = True
        self.active = False
        self.reason = ""
        self.auto = False
        self.exempt_windows = set()
        self._window_alerted = set()
        honorific = self.config.get("honorific", "sir")
        _LOGGER.warning("Lockdown DISENGAGED (%s, manual=%s, auto_suppressed=%s)",
                        reason, manual, self._auto_suppressed)
        await self._persist()
        return {
            "type": "lockdown_disengaged",
            "urgency": "low",
            "message": f"{honorific.title()}, lockdown lifted. The house is back to normal.",
            "auto_act": True,
        }

    async def tick(self) -> list[dict]:
        """Drive auto engage/disengage from the alarm, then enforce while active.

        Enforcement (re-securing breaches and breach alerts) only runs when NO
        ONE is home — the away/intrusion posture. While someone is home, an
        armed lockdown is passive: it secured the house on engage, but it won't
        nag about (or fight) intentional activity like a window opened to sleep.
        """
        actions = []
        armed = self._alarm_armed()
        auto_on_arm = self.config.get("lockdown_auto_on_arm", True)

        # Clear a manual-exit suppression once the alarm is no longer armed, so
        # the next arming engages lockdown normally again.
        if not armed and self._auto_suppressed:
            self._auto_suppressed = False
            await self._persist()

        if auto_on_arm and armed and not self.active and not self._auto_suppressed:
            a = await self.engage("alarm armed", auto=True)
            if a:
                actions.append(a)
        elif self.active and self.auto and not armed:
            a = await self.disengage("alarm disarmed")
            if a:
                actions.append(a)

        if not self.active:
            return actions

        # Passive while someone is home — secured on engage, but no re-securing
        # and no breach nagging (open windows / activity are intentional).
        if self._anyone_home():
            return actions

        honorific = self.config.get("honorific", "sir")
        now = time.time()

        # Re-secure breaches: anything unlocked or any door reopened during lockdown
        relocked = await self._lock_all()
        reclosed = await self._close_doors()
        if (relocked or reclosed) and (now - self._last_breach_alert) > LOCKDOWN_BREACH_COOLDOWN:
            self._last_breach_alert = now
            parts = []
            if relocked:
                parts.append(f"re-locked {', '.join(relocked)}")
            if reclosed:
                parts.append(f"re-closed {', '.join(reclosed)}")
            actions.append({
                "type": "lockdown_breach",
                "urgency": "critical",
                "message": (f"{honorific.title()}, a secured entry point was opened during "
                            f"lockdown — {' and '.join(parts)}."),
                "auto_act": True,
            })

        # New windows opened since engage (not pre-existing) — warn once each,
        # then adopt as the intentional baseline so they never nag again this
        # session (prevents repeats from sensor flap or re-engage).
        open_now = self._open_windows()
        new_windows = open_now - self.exempt_windows
        if new_windows:
            names = []
            for eid in sorted(new_windows):
                st = self.hass.states.get(eid)
                names.append(st.attributes.get("friendly_name", eid) if st else eid)
            self.exempt_windows |= new_windows
            await self._persist()
            actions.append({
                "type": "lockdown_breach",
                "urgency": "critical",
                "message": (f"{honorific.title()}, a window was opened during lockdown: "
                            f"{', '.join(names)}. I can't close it remotely."),
                "auto_act": True,
            })

        return actions


# ── Proactive Intelligence (v5.9.07) ────────────────────────────────────────

class ProactiveManager:
    """
    Pursues comfort & efficiency opportunities — not just safety.

    Where SafetyManager prevents harm, ProactiveManager reduces friction:
    it notices when a small action would help (dark room with someone in it,
    a light left on in an empty room, HVAC fighting an empty house) and
    OFFERS to act. It never forces — offers are spoken/pushed suggestions the
    user can accept by voice. Graduated autonomy (see AutonomyManager) can
    later promote a repeatedly-approved offer to silent auto-execution.

    All offers respect: the global proactive kill-switch, quiet hours/sleep,
    ignore rules, and a per-opportunity cooldown so JARVIS never nags.
    """

    def __init__(self, hass: HomeAssistant, config: dict):
        self.hass = hass
        self.config = config
        self._last_check = 0.0
        self._offer_cooldowns: dict[str, float] = {}  # opportunity_key -> ts

    def _on_cooldown(self, key: str) -> bool:
        last = self._offer_cooldowns.get(key, 0.0)
        return (time.time() - last) < PROACTIVE_OFFER_COOLDOWN

    def _mark_offered(self, key: str) -> None:
        self._offer_cooldowns[key] = time.time()

    async def tick(self, sleeping: bool, anyone_home: bool) -> list[dict]:
        """
        Evaluate comfort/efficiency opportunities. Returns a list of offer
        actions (same dict shape SafetyManager uses, with offer=True).
        """
        now = time.time()
        if (now - self._last_check) < PROACTIVE_CHECK_INTERVAL:
            return []
        self._last_check = now

        # Proactive offers are silent during sleep — comfort can wait.
        if sleeping:
            return []

        offers: list[dict] = []

        try:
            dark = await self._check_dark_occupied_room(anyone_home)
            if dark:
                offers.append(dark)
        except Exception as exc:
            _LOGGER.debug("Proactive dark-room check error: %s", exc)

        try:
            stale = await self._check_stale_lights(anyone_home)
            if stale:
                offers.append(stale)
        except Exception as exc:
            _LOGGER.debug("Proactive stale-light check error: %s", exc)

        try:
            hvac = await self._check_hvac_efficiency(anyone_home)
            if hvac:
                offers.append(hvac)
        except Exception as exc:
            _LOGGER.debug("Proactive HVAC check error: %s", exc)

        return offers

    async def _check_dark_occupied_room(self, anyone_home: bool) -> Optional[dict]:
        """Someone present in a room that's dark and has lights off → offer."""
        if not anyone_home:
            return None
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)

        # Find lux sensors that read dark
        for s in self.hass.states.async_all("sensor"):
            if s.attributes.get("device_class") != "illuminance":
                continue
            try:
                lux = float(s.state)
            except (ValueError, TypeError):
                continue
            if lux > DARK_LUX_THRESHOLD:
                continue

            # Determine the area of this sensor
            entry = ent_reg.async_get(s.entity_id)
            area_id = entry.area_id if entry else None
            if not area_id:
                continue

            # Is there occupancy (motion/presence) in this area?
            occupied = self._area_has_presence(area_id, ent_reg)
            if not occupied:
                continue

            # Are the lights in this area already off?
            lights = self._area_lights(area_id, ent_reg)
            if not lights:
                continue
            if any(self.hass.states.get(l).state == "on" for l in lights if self.hass.states.get(l)):
                continue  # already lit

            key = f"dark:{area_id}"
            if self._on_cooldown(key):
                return None
            # Cooldown is marked by the tick only when this offer is actually
            # delivered (see _tick), so deferred offers re-surface naturally.

            honorific = self.config.get("honorific", "sir")
            area_name = self._area_name(area_id)
            return {
                "type": "proactive_lights",
                "urgency": "low",
                "offer": True,
                "offer_key": key,
                "message": (
                    f"{honorific.title()}, it's quite dark in the {area_name} "
                    f"and someone's in there. Shall I turn the lights on?"
                ),
                "action_data": {"domain": "light", "service": "turn_on",
                                "entity_ids": lights},
                "pattern_key": f"lights_on_when_dark:{area_id}",
            }
        return None

    async def _check_stale_lights(self, anyone_home: bool) -> Optional[dict]:
        """Light on a long time in an unoccupied area → offer to turn off."""
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)
        now = dt_util.utcnow()

        for s in self.hass.states.async_all("light"):
            if s.state != "on":
                continue
            # How long has it been on?
            last_changed = s.last_changed
            if not last_changed:
                continue
            mins_on = (now - last_changed).total_seconds() / 60.0
            if mins_on < STALE_LIGHT_MINUTES:
                continue

            entry = ent_reg.async_get(s.entity_id)
            area_id = entry.area_id if entry else None
            if not area_id:
                continue

            # Only flag if the area has NO presence
            if self._area_has_presence(area_id, ent_reg):
                continue

            key = f"stale:{s.entity_id}"
            if self._on_cooldown(key):
                return None

            honorific = self.config.get("honorific", "sir")
            name = s.attributes.get("friendly_name", s.entity_id)
            area_name = self._area_name(area_id)
            return {
                "type": "proactive_stale_light",
                "urgency": "low",
                "offer": True,
                "offer_key": key,
                "message": (
                    f"{honorific.title()}, the {name} has been on for "
                    f"{int(mins_on)} minutes in the {area_name}, which appears "
                    f"empty. Shall I turn it off?"
                ),
                "action_data": {"domain": "light", "service": "turn_off",
                                "entity_ids": [s.entity_id]},
                "pattern_key": f"lights_off_when_empty:{area_id}",
            }
        return None

    async def _check_hvac_efficiency(self, anyone_home: bool) -> Optional[dict]:
        """Climate actively heating/cooling while the house is empty → flag."""
        if anyone_home:
            return None
        for s in self.hass.states.async_all("climate"):
            action = s.attributes.get("hvac_action")
            if action not in ("heating", "cooling"):
                continue

            key = f"hvac:{s.entity_id}"
            if self._on_cooldown(key):
                return None

            honorific = self.config.get("honorific", "sir")
            name = s.attributes.get("friendly_name", s.entity_id)
            return {
                "type": "proactive_hvac",
                "urgency": "low",
                "offer": True,
                "offer_key": key,
                "message": (
                    f"{honorific.title()}, the {name} is {action} but no one's "
                    f"home. Would you like me to set it back to save energy?"
                ),
                "action_data": {"domain": "climate", "service": "set_preset_mode",
                                "entity_ids": [s.entity_id],
                                "service_data": {"preset_mode": "eco"}},
                "pattern_key": f"hvac_eco_when_away",
            }
        return None

    # ── Area helpers ─────────────────────────────────────────────────
    def _area_has_presence(self, area_id: str, ent_reg) -> bool:
        """True if any motion/occupancy/presence sensor in the area is active."""
        for s in self.hass.states.async_all("binary_sensor"):
            dc = s.attributes.get("device_class")
            if dc not in ("motion", "occupancy", "presence"):
                continue
            entry = ent_reg.async_get(s.entity_id)
            if entry and entry.area_id == area_id and s.state == "on":
                return True
        return False

    def _area_lights(self, area_id: str, ent_reg) -> list[str]:
        """All light entity_ids in the given area."""
        out = []
        for s in self.hass.states.async_all("light"):
            entry = ent_reg.async_get(s.entity_id)
            if entry and entry.area_id == area_id:
                out.append(s.entity_id)
        return out

    def _area_name(self, area_id: str) -> str:
        try:
            from homeassistant.helpers import area_registry as ar
            reg = ar.async_get(self.hass)
            area = reg.async_get_area(area_id)
            return area.name if area else area_id
        except Exception:
            return area_id


# ── Graduated Autonomy (v5.9.07) ────────────────────────────────────────────

class AutonomyManager:
    """
    Tracks which proactive offers the user trusts JARVIS to perform alone.

    Graduated trust model:
      Tier 0 (default) — JARVIS OFFERS; user must accept each time.
      Tier 1 (trusted) — after the user accepts the same offer
                         AUTONOMY_TRUST_THRESHOLD times, JARVIS may perform
                         it silently (still logs it; user can revoke).

    A "pattern_key" identifies the kind of action (e.g.
    'lights_on_when_dark:living_room'). Each acceptance increments a counter;
    once it crosses the threshold the key is granted autonomy. Revoking
    resets it to Tier 0. Grants persist across restarts.
    """

    def __init__(self):
        self._grants: dict[str, dict] = {}  # pattern_key -> {approvals,granted,...}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(AUTONOMY_FILE):
                with open(AUTONOMY_FILE, "r", encoding="utf-8") as f:
                    self._grants = json.load(f) or {}
        except Exception as exc:
            _LOGGER.warning("Autonomy grants load failed: %s", exc)
            self._grants = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(AUTONOMY_FILE), exist_ok=True)
            with open(AUTONOMY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._grants, f, indent=2)
        except Exception as exc:
            _LOGGER.warning("Autonomy grants save failed: %s", exc)

    def record_acceptance(self, pattern_key: str, confidence: float = 1.0) -> dict:
        """
        User accepted an offer. Increment its trust counter; may promote to
        autonomous. Returns the updated grant record.
        """
        if not pattern_key:
            return {}
        g = self._grants.get(pattern_key, {
            "approvals": 0, "granted": False, "confidence": confidence,
            "first_seen": dt_util.utcnow().isoformat(),
        })
        g["approvals"] = g.get("approvals", 0) + 1
        g["confidence"] = max(g.get("confidence", 0.0), confidence)
        g["last_accepted"] = dt_util.utcnow().isoformat()
        if (not g["granted"]
                and g["approvals"] >= AUTONOMY_TRUST_THRESHOLD
                and g["confidence"] >= AUTONOMY_MIN_CONFIDENCE):
            g["granted"] = True
            g["granted_at"] = dt_util.utcnow().isoformat()
            _LOGGER.info(
                "Autonomy GRANTED for '%s' after %d acceptances",
                pattern_key, g["approvals"],
            )
        self._grants[pattern_key] = g
        self._save()
        return g

    def record_rejection(self, pattern_key: str) -> None:
        """User declined an offer — reset trust toward this pattern."""
        if pattern_key in self._grants:
            self._grants[pattern_key]["approvals"] = 0
            self._grants[pattern_key]["granted"] = False
            self._grants[pattern_key]["last_rejected"] = dt_util.utcnow().isoformat()
            self._save()

    def is_autonomous(self, pattern_key: str) -> bool:
        """True if JARVIS may perform this action without asking."""
        g = self._grants.get(pattern_key)
        return bool(g and g.get("granted"))

    def revoke(self, pattern_key: str) -> bool:
        """Manually revoke autonomy for a pattern (back to offer-only)."""
        if pattern_key in self._grants:
            self._grants[pattern_key]["granted"] = False
            self._grants[pattern_key]["approvals"] = 0
            self._grants[pattern_key]["revoked_at"] = dt_util.utcnow().isoformat()
            self._save()
            return True
        return False

    def list_grants(self) -> list[dict]:
        """All tracked patterns with their trust state."""
        out = []
        for key, g in self._grants.items():
            out.append({
                "pattern_key": key,
                "approvals": g.get("approvals", 0),
                "granted": g.get("granted", False),
                "threshold": AUTONOMY_TRUST_THRESHOLD,
                "confidence": round(g.get("confidence", 0.0), 2),
            })
        return out


# ── State Change Logger ─────────────────────────────────────────────────────

class StateLogger:
    """Logs meaningful state changes for pattern learning."""

    def __init__(self):
        self._last_states: dict[str, str] = {}
        self._db_path = "/config/jarvis/patterns.db"
        self._init_db()

    def _init_db(self):
        import sqlite3
        from pathlib import Path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS state_changes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        old_state TEXT,
                        new_state TEXT NOT NULL,
                        area_id TEXT,
                        hour INTEGER,
                        day_of_week INTEGER,
                        triggered_by TEXT DEFAULT 'system'
                    );
                    CREATE INDEX IF NOT EXISTS idx_sc_entity
                        ON state_changes(entity_id);
                    CREATE INDEX IF NOT EXISTS idx_sc_ts
                        ON state_changes(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_sc_hour_dow
                        ON state_changes(hour, day_of_week);

                    CREATE TABLE IF NOT EXISTS commands (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        text TEXT NOT NULL,
                        handled_by TEXT DEFAULT 'agent',
                        entity_ids TEXT DEFAULT '[]',
                        person TEXT DEFAULT 'unknown',
                        hour INTEGER,
                        day_of_week INTEGER
                    );
                    CREATE INDEX IF NOT EXISTS idx_cmd_ts
                        ON commands(timestamp);

                    CREATE TABLE IF NOT EXISTS suggestions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created TEXT NOT NULL,
                        description TEXT NOT NULL,
                        automation_yaml TEXT,
                        status TEXT DEFAULT 'pending',
                        confidence REAL DEFAULT 0.0,
                        pattern_count INTEGER DEFAULT 0,
                        approved_at TEXT,
                        dismissed_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS person_patterns (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        person TEXT NOT NULL,
                        pattern_type TEXT NOT NULL,
                        description TEXT NOT NULL,
                        data TEXT DEFAULT '{}',
                        confidence REAL DEFAULT 0.0,
                        last_seen TEXT,
                        occurrences INTEGER DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS idx_pp_person
                        ON person_patterns(person);
                """)
        except Exception as exc:
            _LOGGER.warning("Pattern DB init failed: %s", exc)

    def log_state_change(self, entity_id: str, old_state: str,
                          new_state: str, area_id: str = "",
                          triggered_by: str = "system"):
        """Record a state change for pattern analysis."""
        import sqlite3
        # Skip noisy domains
        domain = entity_id.split(".")[0]
        if domain in ("sensor", "binary_sensor", "weather", "sun",
                       "update", "device_tracker"):
            return  # Too noisy for pattern learning

        if domain in ("automation", "script", "scene", "input_boolean",
                       "input_number"):
            return  # Meta entities, not useful for patterns

        now = datetime.now()
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO state_changes "
                    "(timestamp, entity_id, domain, old_state, new_state, "
                    "area_id, hour, day_of_week, triggered_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(), entity_id, domain, old_state,
                     new_state, area_id, now.hour, now.weekday(),
                     triggered_by),
                )
        except Exception:
            pass

    def log_command(self, text: str, handled_by: str = "agent",
                     entity_ids: list = None, person: str = "unknown"):
        """Record a voice/text command for pattern analysis."""
        import sqlite3
        now = datetime.now()
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO commands "
                    "(timestamp, text, handled_by, entity_ids, person, "
                    "hour, day_of_week) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(), text, handled_by,
                     json.dumps(entity_ids or []), person,
                     now.hour, now.weekday()),
                )
        except Exception:
            pass

    def get_pattern_stats(self) -> dict:
        """Return learning statistics."""
        import sqlite3
        stats = {"state_changes": 0, "commands": 0, "suggestions": 0,
                 "patterns": 0, "days_of_data": 0}
        try:
            with sqlite3.connect(self._db_path) as conn:
                stats["state_changes"] = conn.execute(
                    "SELECT COUNT(*) FROM state_changes").fetchone()[0]
                stats["commands"] = conn.execute(
                    "SELECT COUNT(*) FROM commands").fetchone()[0]
                stats["suggestions"] = conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='pending'"
                ).fetchone()[0]
                stats["patterns"] = conn.execute(
                    "SELECT COUNT(*) FROM person_patterns").fetchone()[0]
                oldest = conn.execute(
                    "SELECT MIN(timestamp) FROM state_changes"
                ).fetchone()[0]
                if oldest:
                    days = (datetime.now() - datetime.fromisoformat(oldest)).days
                    stats["days_of_data"] = days
        except Exception:
            pass
        return stats


# ── Core State ──────────────────────────────────────────────────────────────

class _CoreState:
    def __init__(self):
        self.hass: Optional[HomeAssistant] = None
        self.config: dict = {}
        self.running: bool = False
        self.task: Optional[asyncio.Task] = None
        self.unsub: Optional[object] = None
        self.ignore_mgr: Optional[IgnoreManager] = None
        self.safety_mgr: Optional[SafetyManager] = None
        self.lockdown_mgr: Optional["LockdownManager"] = None
        self.proactive_mgr: Optional[ProactiveManager] = None
        self.autonomy_mgr: Optional[AutonomyManager] = None
        self.state_logger: Optional[StateLogger] = None
        self.tick_count: int = 0
        self.actions_taken: int = 0
        self.offers_made: int = 0
        self.autonomous_actions: int = 0
        self.last_tick: float = 0.0
        self.startup_time: float = 0.0
        # Pending offer awaiting a yes/no from the user (set when an offer is
        # spoken, consumed by the conversation layer on "yes"/"no").
        self.pending_offer: Optional[dict] = None

_CORE = _CoreState()


# ── State Change Listener ──────────────────────────────────────────────────

@callback
def _on_state_changed(event: Event) -> None:
    """Log state changes and check ignore rules."""
    if not _CORE.running:
        return

    entity_id = event.data.get("entity_id", "")
    old_state = event.data.get("old_state")
    new_state = event.data.get("new_state")

    if not new_state:
        return

    # Check ignore rules
    if _CORE.ignore_mgr and _CORE.ignore_mgr.is_ignored(entity_id):
        return

    old_val = old_state.state if old_state else "unknown"
    new_val = new_state.state

    # Skip unavailable/unknown transitions
    if new_val in ("unavailable", "unknown") or old_val == new_val:
        return

    # Get area
    area_id = ""
    try:
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        ent_reg = er.async_get(_CORE.hass)
        dev_reg = dr.async_get(_CORE.hass)
        entry = ent_reg.async_get(entity_id)
        if entry:
            area_id = entry.area_id or ""
            if not area_id and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                area_id = device.area_id if device else ""
    except Exception:
        pass

    # Log for pattern learning
    if _CORE.state_logger:
        _CORE.state_logger.log_state_change(
            entity_id, old_val, new_val, area_id,
        )


# ── Main Evaluation Loop ───────────────────────────────────────────────────

async def _tick():
    """Single evaluation tick — reviews home state and decides actions."""
    hass = _CORE.hass
    config = _CORE.config
    _CORE.tick_count += 1
    _CORE.last_tick = time.time()

    # Determine home state
    anyone_home = any(
        s.state == "home" for s in hass.states.async_all("person")
    )

    from . import sleep_detection
    bedroom_areas = config.get("bedroom_areas", []) or []
    sleeping, _ = sleep_detection.is_sleeping(
        hass,
        bedroom_area_ids=bedroom_areas,
        quiet_start=config.get("observer_quiet_start", "22:00"),
        quiet_end=config.get("observer_quiet_end", "07:00"),
    )

    # Lockdown runs first so the nighttime sweep can defer to it when active.
    actions = []
    if _CORE.lockdown_mgr:
        try:
            actions.extend(await _CORE.lockdown_mgr.tick())
        except Exception as exc:
            _LOGGER.debug("Lockdown tick error: %s", exc)

    # Run safety checks
    actions.extend(await _CORE.safety_mgr.tick(sleeping, anyone_home))

    # ── Proactive comfort/efficiency offers (v5.9.07) ───────────────
    # Gated by the global proactive kill-switch; safety always runs but
    # comfort offers must be allowed.
    proactive_enabled = _CORE.config.get("observer_proactive", True)
    if proactive_enabled and _CORE.proactive_mgr:
        try:
            offers = await _CORE.proactive_mgr.tick(sleeping, anyone_home)
            spoke_offer = False  # only ONE spoken offer per tick (avoid stacking
                                 # questions when only one pending_offer is tracked)
            for offer in offers:
                pkey = offer.get("pattern_key", "")
                # Graduated autonomy: trusted actions execute silently — all of
                # them, since they don't need a yes/no.
                if pkey and _CORE.autonomy_mgr and _CORE.autonomy_mgr.is_autonomous(pkey):
                    ok = await _execute_action_data(_CORE.hass, offer.get("action_data", {}))
                    if ok:
                        _CORE.autonomous_actions += 1
                        # Mark cooldown so the same autonomous action doesn't
                        # re-fire every proactive cycle.
                        okey = offer.get("offer_key")
                        if okey:
                            _CORE.proactive_mgr._mark_offered(okey)
                        done_msg = _autonomous_done_message(offer)
                        actions.append({
                            "type": offer.get("type", "proactive") + "_auto",
                            "urgency": "low",
                            "message": done_msg,
                            "auto_act": True,
                        })
                        from .websocket import jarvis_log
                        jarvis_log("AUTO", f"autonomous: {pkey} → {done_msg[:60]}")
                elif not spoke_offer:
                    # Offer the FIRST non-autonomous opportunity; remaining ones
                    # wait for a later tick (their cooldown isn't marked, so they
                    # re-surface naturally next cycle).
                    _CORE.offers_made += 1
                    _CORE.pending_offer = offer
                    okey = offer.get("offer_key")
                    if okey:
                        _CORE.proactive_mgr._mark_offered(okey)
                    actions.append(offer)
                    spoke_offer = True
        except Exception as exc:
            _LOGGER.debug("Proactive tick error: %s", exc)

    # Run pattern analysis periodically
    try:
        from .pattern_analyzer import get_analyzer, set_thresholds
        analyzer = get_analyzer()
        if analyzer.should_analyze():
            # Loosened-reins defaults (occurrences 4, confidence 0.55) — API spend
            # is no longer the constraint; user can tune via panel-saved keys.
            try:
                _occ = int(config.get("pattern_min_occurrences", 4) or 4)
            except Exception:
                _occ = 4
            try:
                _conf = float(config.get("pattern_confidence", 0.55) or 0.55)
            except Exception:
                _conf = 0.55
            set_thresholds(_occ, _conf)
            patterns = await analyzer.analyze(hass)
            if patterns:
                from .websocket import jarvis_log
                jarvis_log("LEARN", f"Pattern analysis: {len(patterns)} patterns found")
                # Notify about new high-confidence suggestions
                pending = analyzer.get_pending_suggestions()
                if pending:
                    honorific = config.get("honorific", "sir")
                    jarvis_log(
                        "LEARN",
                        f"{len(pending)} automation suggestion(s) pending review",
                    )
    except Exception as exc:
        _LOGGER.debug("Pattern analysis tick error: %s", exc)

    # ── Local cognition: anticipation (v5.9.30) ─────────────────────────────
    # Every ~15 min, sample occupancy and flag entities in a state that's
    # unusual for this time of day ("garage usually closed by now"). Gated by
    # the proactive kill-switch + the cognition toggle. Predictions are appended
    # as actions and flow through the same gated announce path below (so they
    # push-instead-of-speak while you're asleep). Model is persisted each cycle.
    try:
        from . import cognition
        cog_on = True
        try:
            from . import observer as _obs
            cog_on = _obs._cognition_enabled()
        except Exception:
            cog_on = bool(config.get("cognition_enabled", True))

        if proactive_enabled and cog_on:
            now_t = time.time()
            if now_t - getattr(_CORE, "_last_cog_cycle", 0.0) >= cognition.OCC_SAMPLE_INTERVAL:
                _CORE._last_cog_cycle = now_t
                cognition.sample_occupancy(hass, now_t)
                cognition.sample_presence(hass, now_t)
                preds = (cognition.predict(hass, now_t)
                         + cognition.predict_overdue(hass, now_t)
                         + cognition.predict_presence(hass, now_t)
                         + cognition.predict_proximity(hass, now_t))
                for pred in preds:
                    actions.append(pred)
                    from .websocket import jarvis_log
                    jarvis_log("LEARN", f"anticipation: {pred.get('message','')[:80]}")
                await hass.async_add_executor_job(
                    cognition.save_to_db, "/config/jarvis/patterns.db"
                )
    except Exception as exc:
        _LOGGER.debug("Cognition anticipation tick error: %s", exc)

    # Process actions
    for action in actions:
        await _emit_action(hass, config, action, sleeping)


async def _emit_action(hass, config, action, sleeping):
    """Announce / push a single cognitive action via the standard routing."""
    _CORE.actions_taken += 1
    message = action.get("message", "")
    urgency = action.get("urgency", "medium")
    action_type = action.get("type", "unknown")

    _LOGGER.info(
        "Cognitive action [%s] urgency=%s: %s",
        action_type, urgency, message[:100],
    )

    # Route announcement
    try:
        from .tts_helper import resolve_tts_for_context, async_announce
        from .audio_routing import observer_speak_target

        # Quiet hours: only CRITICAL may speak. Non-critical → phone push only.
        # Time-based (independent of bedroom presence), so nothing slips through.
        in_quiet = False
        try:
            from . import sleep_detection
            in_quiet = sleep_detection._in_quiet_hours(
                config.get("observer_quiet_start", "22:00"),
                config.get("observer_quiet_end", "07:00"),
            )
        except Exception:
            in_quiet = False

        if (sleeping or in_quiet) and urgency != "critical":
            # Push to phone only (no spoken announcement)
            await _push_notification(hass, config, message, action_type)
        else:
            # Get announcement speakers from config
            ann_speakers = None
            try:
                from .const import DOMAIN
                for eid, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict):
                        rc = data.get("runtime_config", {})
                        raw = rc.get("announcement_speakers")
                        if raw:
                            parsed = json.loads(raw) if isinstance(raw, str) else raw
                            if isinstance(parsed, list) and parsed:
                                ann_speakers = parsed
                                break
            except Exception:
                pass

            broadcast_group = config.get("broadcast_group") or None
            targets, mode = observer_speak_target(
                hass, urgency=urgency,
                broadcast_group=broadcast_group,
                announcement_speakers=ann_speakers,
                is_sleeping=sleeping,
            )

            if targets and mode not in ("suppressed",):
                tts_entity = resolve_tts_for_context(
                    hass, "sentinel",
                    config.get("tts_engine", "auto"),
                    config.get("tts_premium_engine") or None,
                    config.get("tts_premium_contexts") or [],
                )
                if tts_entity:
                    await async_announce(
                        hass, message, tts_entity, targets,
                        context="sentinel",
                    )

            # Also push critical alerts to phone
            if urgency in ("critical", "high"):
                await _push_notification(hass, config, message, action_type)

    except Exception as exc:
        _LOGGER.warning("Cognitive: action routing failed: %s", exc)


def is_lockdown() -> bool:
    """True if a formal lockdown is currently active."""
    return bool(_CORE.lockdown_mgr and _CORE.lockdown_mgr.active)


def lockdown_status() -> dict:
    """Lockdown state snapshot for the panel / observability."""
    if _CORE.lockdown_mgr:
        return _CORE.lockdown_mgr.status()
    return {"active": False, "since": 0.0, "reason": "", "auto": False, "exempt_windows": 0}


async def request_lockdown(on: bool, reason: str = "requested") -> bool:
    """
    Manual lockdown entry point (service / voice / panel). Engages or lifts the
    lockdown and announces the result through the normal routing. Returns True
    if the cognitive core was available to handle it.
    """
    if not _CORE.lockdown_mgr or not _CORE.hass:
        return False
    mgr = _CORE.lockdown_mgr
    action = await (mgr.engage(reason, auto=False) if on else mgr.disengage(reason, manual=True))
    if action:
        try:
            from . import sleep_detection
            cfg = _CORE.config
            sleeping, _ = sleep_detection.is_sleeping(
                _CORE.hass,
                bedroom_area_ids=cfg.get("bedroom_areas", []) or [],
                quiet_start=cfg.get("observer_quiet_start", "22:00"),
                quiet_end=cfg.get("observer_quiet_end", "07:00"),
            )
        except Exception:
            sleeping = False
        await _emit_action(_CORE.hass, _CORE.config, action, sleeping)
    return True


async def _push_notification(hass, config, message, action_type):
    """Push notification to phone."""
    notify_svc = config.get("notify_service", "")
    if not notify_svc:
        return
    try:
        svc_domain, svc_name = notify_svc.split(".", 1)
        titles = {
            "freeze_critical": "JARVIS — Freeze Warning",
            "freeze_warning": "JARVIS — Temperature Alert",
            "intrusion_away": "JARVIS — Security Alert",
            "intrusion_sleep": "JARVIS — Motion Detected",
            "lockdown": "JARVIS — House Secured",
        }
        await hass.services.async_call(
            svc_domain, svc_name,
            {"message": message, "title": titles.get(action_type, "JARVIS")},
            blocking=False,
        )
    except Exception as exc:
        _LOGGER.debug("Cognitive: push notification failed: %s", exc)


async def _execute_action_data(hass, action_data: dict) -> bool:
    """
    Execute a proactive action's service call.

    action_data shape:
      {"domain": "light", "service": "turn_on",
       "entity_ids": ["light.x", ...], "service_data": {...optional...}}
    """
    if not action_data:
        return False
    domain = action_data.get("domain")
    service = action_data.get("service")
    entity_ids = action_data.get("entity_ids", [])
    extra = action_data.get("service_data", {}) or {}
    if not domain or not service or not entity_ids:
        return False
    try:
        await hass.services.async_call(
            domain, service,
            {"entity_id": entity_ids, **extra},
            blocking=True,
        )
        return True
    except Exception as exc:
        _LOGGER.warning("Proactive action failed (%s.%s): %s", domain, service, exc)
        return False


def _autonomous_done_message(offer: dict) -> str:
    """Convert an offer into a past-tense 'I did this' notification."""
    t = offer.get("type", "")
    ad = offer.get("action_data", {})
    n = len(ad.get("entity_ids", []))
    if t == "proactive_lights":
        return f"I turned the lights on for you — it was dark and you were there."
    if t == "proactive_stale_light":
        return f"I turned off a light left on in an empty room to save energy."
    if t == "proactive_hvac":
        return f"I set the climate back to eco — no one's home."
    return f"I handled {n} device(s) for you automatically."


async def _loop():
    """Main cognitive loop — runs every TICK_INTERVAL seconds."""
    _LOGGER.info("Cognitive Core loop started")
    # Yield before the first tick. Even as a background task, running a full
    # state-scanning tick synchronously at entry would do real work while HA is
    # still bringing entities up — both wasteful (state is incomplete) and
    # needless load during boot. A short delay lets startup settle first.
    try:
        await asyncio.sleep(min(TICK_INTERVAL, 30))
    except asyncio.CancelledError:
        return
    while _CORE.running:
        try:
            await _tick()
        except Exception as exc:
            _LOGGER.warning("Cognitive tick error: %s", exc)
        await asyncio.sleep(TICK_INTERVAL)


# ── Public API ──────────────────────────────────────────────────────────────

def ignore(entity_pattern: str, duration_minutes: int = 0,
           reason: str = "") -> dict:
    """Add an ignore rule. Called by the agent's 'ignore' tool."""
    if _CORE.ignore_mgr:
        rule = _CORE.ignore_mgr.add(entity_pattern, duration_minutes, reason)
        return {
            "success": True,
            "pattern": rule.entity_pattern,
            "duration": duration_minutes,
            "reason": reason,
        }
    return {"success": False, "error": "Cognitive core not running"}


def unignore(entity_pattern: str) -> dict:
    """Remove an ignore rule."""
    if _CORE.ignore_mgr:
        removed = _CORE.ignore_mgr.remove(entity_pattern)
        return {"success": removed, "pattern": entity_pattern}
    return {"success": False, "error": "Cognitive core not running"}


def list_ignores() -> list[dict]:
    """List all active ignore rules."""
    if _CORE.ignore_mgr:
        return _CORE.ignore_mgr.list_rules()
    return []


def is_ignored(entity_id: str) -> bool:
    """Check if an entity is currently ignored."""
    if _CORE.ignore_mgr:
        return _CORE.ignore_mgr.is_ignored(entity_id)
    return False


def log_command(text: str, handled_by: str = "agent",
                entity_ids: list = None, person: str = "unknown"):
    """Record a command for pattern learning."""
    if _CORE.state_logger:
        _CORE.state_logger.log_command(text, handled_by, entity_ids, person)


def status() -> dict:
    """Return cognitive core status for diagnostics."""
    stats = {}
    if _CORE.state_logger:
        stats = _CORE.state_logger.get_pattern_stats()
    return {
        "running": _CORE.running,
        "tick_count": _CORE.tick_count,
        "actions_taken": _CORE.actions_taken,
        "offers_made": _CORE.offers_made,
        "autonomous_actions": _CORE.autonomous_actions,
        "autonomy_grants": _CORE.autonomy_mgr.list_grants() if _CORE.autonomy_mgr else [],
        "uptime_hours": round((time.time() - _CORE.startup_time) / 3600, 1)
        if _CORE.startup_time else 0,
        "last_tick_ago": round(time.time() - _CORE.last_tick, 1)
        if _CORE.last_tick else 0,
        "ignore_rules": len(list_ignores()),
        "learning": stats,
    }


# ── Proactive offer API (v5.9.07) ───────────────────────────────────────────

def get_pending_offer() -> Optional[dict]:
    """Return the offer currently awaiting a yes/no, if any."""
    return _CORE.pending_offer


async def accept_pending_offer() -> dict:
    """
    User said yes to the pending proactive offer. Execute it and record the
    acceptance toward graduated autonomy. Returns a result dict.
    """
    offer = _CORE.pending_offer
    if not offer:
        return {"ok": False, "reason": "no pending offer"}
    _CORE.pending_offer = None
    ok = await _execute_action_data(_CORE.hass, offer.get("action_data", {}))
    pkey = offer.get("pattern_key", "")
    if ok and pkey and _CORE.autonomy_mgr:
        grant = _CORE.autonomy_mgr.record_acceptance(pkey, confidence=0.9)
        _CORE.actions_taken += 1
        return {
            "ok": True, "pattern_key": pkey,
            "approvals": grant.get("approvals", 0),
            "now_autonomous": grant.get("granted", False),
        }
    return {"ok": ok}


def decline_pending_offer() -> dict:
    """User said no. Clear the offer and reset trust toward that pattern."""
    offer = _CORE.pending_offer
    _CORE.pending_offer = None
    if offer and _CORE.autonomy_mgr:
        pkey = offer.get("pattern_key", "")
        if pkey:
            _CORE.autonomy_mgr.record_rejection(pkey)
    return {"ok": True}


def revoke_autonomy(pattern_key: str) -> dict:
    """Revoke a previously-granted autonomous action."""
    if _CORE.autonomy_mgr and _CORE.autonomy_mgr.revoke(pattern_key):
        return {"ok": True, "pattern_key": pattern_key}
    return {"ok": False, "reason": "no such grant"}


# ── Start / Stop ────────────────────────────────────────────────────────────

async def start(hass: HomeAssistant, config: dict) -> None:
    """Start the cognitive core."""
    if _CORE.running:
        await stop()

    _CORE.hass = hass
    _CORE.config = config
    _CORE.running = True
    _CORE.startup_time = time.time()
    _CORE.tick_count = 0
    _CORE.actions_taken = 0
    _CORE.offers_made = 0
    _CORE.autonomous_actions = 0
    _CORE.pending_offer = None

    _CORE.ignore_mgr = IgnoreManager()
    _CORE.safety_mgr = SafetyManager(hass, config)
    _CORE.lockdown_mgr = LockdownManager(hass, config)
    _CORE.proactive_mgr = ProactiveManager(hass, config)
    _CORE.autonomy_mgr = await hass.async_add_executor_job(AutonomyManager)
    _CORE.state_logger = await hass.async_add_executor_job(StateLogger)

    # Restore the cognition model (per-entity rhythm) so anticipation survives
    # restarts and keeps accumulating across days.
    try:
        from . import cognition
        await hass.async_add_executor_job(cognition.load_from_db, "/config/jarvis/patterns.db")
    except Exception as exc:
        _LOGGER.debug("cognition load on start failed: %s", exc)

    _CORE.unsub = hass.bus.async_listen("state_changed", _on_state_changed)
    # The cognitive loop runs for the lifetime of the integration. It MUST be a
    # *background* task — a plain async_create_task is tracked as part of config-
    # entry setup, so HA's bootstrap waits on it to finish before completing
    # startup. Since the loop never returns, that wait runs to the full timeout
    # and HA logs "Something is blocking Home Assistant from wrapping up the
    # start up phase … waiting for tasks: _loop()". Background tasks are exempt
    # from that wait by design. (Fallback for cores predating the helper.)
    if hasattr(hass, "async_create_background_task"):
        _CORE.task = hass.async_create_background_task(_loop(), "jarvis_cognitive_loop")
    else:
        _CORE.task = hass.async_create_task(_loop())

    stats = await hass.async_add_executor_job(
        _CORE.state_logger.get_pattern_stats
    )
    _LOGGER.info(
        "JARVIS Cognitive Core started — %d days of data, "
        "%d state changes logged, %d patterns learned, "
        "%d active ignore rules, %d autonomy grants",
        stats.get("days_of_data", 0),
        stats.get("state_changes", 0),
        stats.get("patterns", 0),
        len(_CORE.ignore_mgr.list_rules()),
        len(_CORE.autonomy_mgr.list_grants()),
    )


async def stop() -> None:
    """Stop the cognitive core."""
    _CORE.running = False
    _CORE.pending_offer = None  # don't let a stale offer survive a restart
    if _CORE.task:
        _CORE.task.cancel()
        try:
            await _CORE.task
        except (asyncio.CancelledError, Exception):
            pass
    if _CORE.unsub:
        try:
            _CORE.unsub()
        except Exception:
            pass
    _LOGGER.info(
        "JARVIS Cognitive Core stopped — %d ticks, %d actions taken",
        _CORE.tick_count, _CORE.actions_taken,
    )
