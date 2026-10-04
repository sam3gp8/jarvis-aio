"""
JARVIS — Observer output gate.

The final line of defense before an announcement actually plays. Handles:

  - Rate limiting: max N announcements per rolling hour
  - Dedup: don't say the same thing within M minutes
  - Mute memory: entities the user has told JARVIS to stop announcing
  - Quiet hours integration (sleep_detection handles this mostly)
  - Announcement log for feedback learning

State lives in memory (dict). Simple and works across the reasoning loop
and service calls. Persistence across restarts is a future improvement —
for now mute preferences reset on restart, which is fine for early use.

The `jarvis.shush` service lets the user say "stop announcing that" and
pushes the entity_id (or category) of the most recent announcement into
the mute set.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import Optional

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

# Persist mute preferences so they survive a restart (#181). Entity/category
# mutes and the blanket switch are written here on every shush/unshush and
# loaded once on first use. Best-effort: a read/write failure never breaks the
# gate (mutes just fall back to in-memory for the session).
MUTES_PATH = config_path_str("jarvis", "output_mutes.json")
_mutes_loaded = False


def _load_mutes() -> None:
    """Load persisted mutes into _STATE once. Never raises."""
    global _mutes_loaded
    if _mutes_loaded:
        return
    _mutes_loaded = True
    try:
        if not os.path.exists(MUTES_PATH):
            return
        with open(MUTES_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            _STATE.muted_entities |= {str(e) for e in data.get("entities", []) if e}
            _STATE.muted_categories |= {str(c) for c in data.get("categories", []) if c}
            _STATE.mute_all = bool(data.get("all", False))
    except Exception as exc:
        _LOGGER.debug("output mutes load failed: %s", exc)


def _save_mutes() -> None:
    """Persist current mutes. Called after shush/unshush. Never raises."""
    try:
        os.makedirs(os.path.dirname(MUTES_PATH), exist_ok=True)
        tmp = MUTES_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "entities": sorted(_STATE.muted_entities),
                "categories": sorted(_STATE.muted_categories),
                "all": bool(_STATE.mute_all),
            }, f)
        os.replace(tmp, MUTES_PATH)
    except Exception as exc:
        _LOGGER.debug("output mutes persist failed: %s", exc)


# Rate-limit defaults — conservative to start, the user can tune later
DEFAULT_MAX_PER_HOUR = 6
DEFAULT_DEDUP_MINUTES = 10


@dataclass
class Announcement:
    """A record of something JARVIS said."""
    timestamp: float
    entity_id: str
    category: str
    urgency: str
    message: str
    was_spoken: bool = True          # False if suppressed by gate
    reservation_id: Optional[str] = None


@dataclass
class GateState:
    """Module-level state. One global instance per HA process."""
    history: deque = field(default_factory=lambda: deque(maxlen=100))
    muted_entities: set[str] = field(default_factory=set)
    muted_categories: set[str] = field(default_factory=set)
    recent_messages: deque = field(default_factory=lambda: deque(maxlen=20))
    reservations: list[Announcement] = field(default_factory=list)
    mute_all: bool = False   # blanket kill switch set by shush(all=True)


_STATE = GateState()
_ANNOUNCEMENT_LOCK = asyncio.Lock()


def _now() -> float:
    return time.time()


def _recent_within(history: deque, seconds: float) -> list[Announcement]:
    cutoff = _now() - seconds
    return [a for a in history if a.timestamp >= cutoff and a.was_spoken]


# ─── Public gate API ────────────────────────────────────────────────────────

# Adaptive interruption budget: when enabled, the recent rate of unwelcome
# proactive decisions (from decision_record) scales the hourly cap down, so
# JARVIS interrupts less after a run of dismissed/false alarms. Opt-in and
# cached (60s) so we never query the store on every gate check.
_BUDGET_CACHE = {"ts": 0.0, "mult": 1.0}


def _budget_multiplier() -> float:
    """Multiplier (<= 1.0) applied to the hourly cap when the adaptive
    interruption budget is on. 1.0 (no change) when disabled or on any error."""
    try:
        from . import jarvis_config
        if not jarvis_config.get("adaptive_interruption_budget", False):
            return 1.0
    except Exception:
        return 1.0
    now = _now()
    if now - _BUDGET_CACHE["ts"] < 60.0:
        return _BUDGET_CACHE["mult"]
    try:
        from . import decision_record
        mult = float(decision_record.interruption_budget().get("multiplier", 1.0))
    except Exception:
        mult = 1.0
    _BUDGET_CACHE.update(ts=now, mult=mult)
    return mult


def _attention_shadow(*, category: str, urgency: str, reason: str,
                      budget_multiplier: float, max_per_hour: int,
                      allowed: bool) -> None:
    """MCU Phase E (E2): compute the kernel attention arbitration ALONGSIDE the
    legacy gate decision and log any disagreement. SHADOW — the kernel result is
    ignored and ``output_gate`` stays authoritative; best-effort, never affects
    the gate. Establishes ``kernel.attention`` as a live-wired primitive."""
    try:
        from .kernel import attention as A
        pri = (A.CRITICAL if urgency == "critical"
               else A.HIGH if urgency == "high" else A.NORMAL)
        eff_max = max(1, int(round(max_per_hour * budget_multiplier)))
        recent = len(_recent_within(_STATE.history, 3600) + _STATE.reservations)
        ctx = A.AttentionContext(
            budget_remaining=max(0.0, 1.0 - recent / eff_max),
            recent_interruptions=recent, max_recent=eff_max,
            shushed=_STATE.mute_all,
            duplicate=reason.startswith("duplicate"),
        )
        dec = A.arbitrate(A.AttentionRequest(category=category, priority=pri), ctx)
        if dec.allowed != allowed:
            _LOGGER.debug(
                "attention shadow divergence: kernel=%s (%s) vs legacy allowed=%s (%s)",
                dec.decision, dec.reason, allowed, reason)
    except Exception:  # pragma: no cover - defensive
        pass


def _can_announce_with_multiplier(
    budget_multiplier: float,
    *,
    entity_id: str,
    category: str,
    urgency: str,
    message: str,
    max_per_hour: int = DEFAULT_MAX_PER_HOUR,
    dedup_minutes: int = DEFAULT_DEDUP_MINUTES,
    reservation_id: Optional[str] = None,
) -> tuple[bool, str]:
    """Decide whether this announcement can proceed, then (E2) run the kernel
    attention arbitration in shadow alongside it."""
    allowed, reason = _gate_decision(
        budget_multiplier, entity_id=entity_id, category=category,
        urgency=urgency, message=message, max_per_hour=max_per_hour,
        dedup_minutes=dedup_minutes, reservation_id=reservation_id)
    _attention_shadow(category=category, urgency=urgency, reason=reason,
                      budget_multiplier=budget_multiplier,
                      max_per_hour=max_per_hour, allowed=allowed)
    return allowed, reason


def _gate_decision(
    budget_multiplier: float,
    *,
    entity_id: str,
    category: str,
    urgency: str,
    message: str,
    max_per_hour: int = DEFAULT_MAX_PER_HOUR,
    dedup_minutes: int = DEFAULT_DEDUP_MINUTES,
    reservation_id: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Decide whether this announcement can proceed.

    Blanket mute (`_STATE.mute_all`) blocks EVERYTHING including critical.
    Use sparingly. Cleared by unshush().

    Otherwise critical urgency bypasses every gate check.
    """
    _load_mutes()
    if _STATE.mute_all:
        return False, "blanket shush active"

    if urgency == "critical":
        return True, "critical bypass"

    # Explicit mute check
    if entity_id in _STATE.muted_entities:
        return False, f"entity {entity_id} is muted"
    if category in _STATE.muted_categories:
        return False, f"category {category} is muted"

    # Rate limit — tightened by the adaptive interruption budget when enabled.
    eff_max = max(1, int(round(max_per_hour * budget_multiplier)))
    now = _now()
    _STATE.reservations[:] = [
        a for a in _STATE.reservations if a.timestamp >= now - 3600
    ]
    recent = _recent_within(_STATE.history, 3600) + _STATE.reservations
    if len(recent) >= eff_max and urgency not in ("high",):
        return False, f"rate limit ({len(recent)}/{eff_max}/hour)"

    # Dedup: is this message (or a substring) close to something recent?
    dedup_cutoff = _now() - dedup_minutes * 60
    recent_messages = list(_STATE.recent_messages) + [
        {"timestamp": a.timestamp, "message": a.message}
        for a in _STATE.reservations
        if a.reservation_id != reservation_id
    ]
    for past in recent_messages:
        if past["timestamp"] < dedup_cutoff:
            continue
        if _messages_similar(past["message"], message):
            return False, "duplicate of recent message"

    return True, "ok"


def can_announce(
    *,
    entity_id: str,
    category: str,
    urgency: str,
    message: str,
    max_per_hour: int = DEFAULT_MAX_PER_HOUR,
    dedup_minutes: int = DEFAULT_DEDUP_MINUTES,
) -> tuple[bool, str]:
    return _can_announce_with_multiplier(
        _budget_multiplier(), entity_id=entity_id, category=category,
        urgency=urgency, message=message, max_per_hour=max_per_hour,
        dedup_minutes=dedup_minutes)


async def _budget_multiplier_async(hass) -> float:
    try:
        from . import jarvis_config
        enabled = await hass.async_add_executor_job(
            jarvis_config.get, "adaptive_interruption_budget", False
        )
        if not enabled:
            return 1.0
    except Exception:
        return 1.0
    now = _now()
    if now - _BUDGET_CACHE["ts"] < 60.0:
        return _BUDGET_CACHE["mult"]
    try:
        from . import decision_record
        budget = await hass.async_add_executor_job(
            decision_record.interruption_budget
        )
        mult = float(budget.get("multiplier", 1.0))
    except Exception:
        mult = 1.0
    _BUDGET_CACHE.update(ts=now, mult=mult)
    return mult


async def async_reserve_announcement(hass, **kwargs) -> tuple[bool, str, Optional[str]]:
    """Reserve a slot for an announcement and return its reservation token.

    Callers MUST pass the returned token to `async_record_announcement` (on
    success) or `release_reservation` (on every other exit path, ideally in a
    `finally` block) so a rejected/aborted caller can never release a
    different caller's reservation and so slots aren't held for up to an
    hour when a caller returns or raises before recording.
    """
    multiplier = await _budget_multiplier_async(hass)
    async with _ANNOUNCEMENT_LOCK:
        reservation_id = f"{time.monotonic_ns()}-{kwargs['entity_id']}-{len(_STATE.reservations)}"
        allowed, reason = _can_announce_with_multiplier(
            multiplier, reservation_id=reservation_id, **kwargs
        )
        if allowed:
            _STATE.reservations.append(Announcement(
                timestamp=_now(), entity_id=kwargs["entity_id"],
                category=kwargs["category"], urgency=kwargs["urgency"],
                message=kwargs["message"], was_spoken=True,
                reservation_id=reservation_id,
            ))
        return allowed, reason, reservation_id if allowed else None


async def release_reservation(hass, reservation_id: Optional[str]) -> None:
    """Release a reserved slot without recording an announcement.

    Use this in the `finally` of every post-admit path that doesn't end in
    `async_record_announcement` (e.g. an early return or an exception),
    otherwise the slot stays counted against the rate limit for up to an
    hour.
    """
    if reservation_id is None:
        return
    async with _ANNOUNCEMENT_LOCK:
        _STATE.reservations[:] = [
            a for a in _STATE.reservations if a.reservation_id != reservation_id
        ]


def _record_announcement_state(
    *,
    entity_id: str,
    category: str,
    urgency: str,
    message: str,
    was_spoken: bool,
) -> None:
    """Log an announcement for history + dedup + future feedback learning."""
    ann = Announcement(
        timestamp=_now(),
        entity_id=entity_id,
        category=category,
        urgency=urgency,
        message=message,
        was_spoken=was_spoken,
    )
    _STATE.history.append(ann)
    if was_spoken:
        _STATE.recent_messages.append({
            "timestamp": _now(),
            "message": message,
        })
def _save_announcement_activity(
    *, entity_id: str, category: str, urgency: str, message: str,
    was_spoken: bool,
) -> None:
    # v5.4.8: persist to SQLite for panel activity log
    try:
        from .database import save_activity
        save_activity(
            entity_id=entity_id,
            category=category,
            urgency=urgency,
            message=message,
            was_spoken=was_spoken,
            source="observer",
        )
    except Exception:
        pass  # DB write failure is non-fatal


def record_announcement(
    *,
    entity_id: str,
    category: str,
    urgency: str,
    message: str,
    was_spoken: bool,
) -> None:
    _record_announcement_state(
        entity_id=entity_id, category=category, urgency=urgency,
        message=message, was_spoken=was_spoken)
    _save_announcement_activity(
        entity_id=entity_id, category=category, urgency=urgency,
        message=message, was_spoken=was_spoken)


async def async_record_announcement(hass, *, reservation_id: Optional[str] = None, **kwargs) -> None:
    """Update gate state on the loop and persist activity off the event loop.

    `reservation_id` must be the token returned by `async_reserve_announcement`
    for this announcement, so ownership of the reservation being released is
    always token-based (never guessed from matching fields).
    """
    async with _ANNOUNCEMENT_LOCK:
        if reservation_id is not None:
            _STATE.reservations[:] = [
                a for a in _STATE.reservations if a.reservation_id != reservation_id
            ]
        _record_announcement_state(**kwargs)
    await hass.async_add_executor_job(
        partial(_save_announcement_activity, **kwargs)
    )


def shush(
    entity_id: Optional[str] = None,
    category: Optional[str] = None,
    all: bool = False,
) -> dict:
    """
    Mute an entity and/or category.

    Arguments:
      - entity_id: mute only this entity
      - category: mute only this category
      - all: mute EVERYTHING — blanket kill switch until unshush is called
      - (no args): mute the most recent announcement's entity (targeted)
    """
    _load_mutes()
    result = {"muted_entities": [], "muted_categories": [], "all": False}

    if all:
        _STATE.mute_all = True
        result["all"] = True
        _save_mutes()
        _LOGGER.warning(
            "JARVIS BLANKET SHUSH engaged — all announcements suppressed "
            "until jarvis.unshush is called"
        )
        return result

    if entity_id is None and category is None:
        # Use the most recent spoken announcement
        spoken = [a for a in _STATE.history if a.was_spoken]
        if spoken:
            last = spoken[-1]
            entity_id = last.entity_id
            _LOGGER.info("Shushing last announcement: %s (%s)", entity_id, last.category)

    if entity_id:
        _STATE.muted_entities.add(entity_id)
        result["muted_entities"].append(entity_id)
    if category:
        _STATE.muted_categories.add(category)
        result["muted_categories"].append(category)

    _save_mutes()
    return result


def unshush(entity_id: Optional[str] = None, category: Optional[str] = None) -> dict:
    """Reverse a mute. If neither given, clear ALL mutes including blanket shush."""
    _load_mutes()
    if entity_id is None and category is None:
        cleared_e = list(_STATE.muted_entities)
        cleared_c = list(_STATE.muted_categories)
        was_blanket = _STATE.mute_all
        _STATE.muted_entities.clear()
        _STATE.muted_categories.clear()
        _STATE.mute_all = False
        _save_mutes()
        return {
            "cleared_entities": cleared_e,
            "cleared_categories": cleared_c,
            "blanket_cleared": was_blanket,
        }

    result = {"cleared_entities": [], "cleared_categories": []}
    if entity_id and entity_id in _STATE.muted_entities:
        _STATE.muted_entities.discard(entity_id)
        result["cleared_entities"].append(entity_id)
    if category and category in _STATE.muted_categories:
        _STATE.muted_categories.discard(category)
        result["cleared_categories"].append(category)
    _save_mutes()
    return result


def current_mutes() -> dict:
    """The current mute preferences — for the panel's Muted Announcements card
    and the jarvis/mutes websocket (#181). Loads persisted mutes first."""
    _load_mutes()
    return {
        "entities": sorted(_STATE.muted_entities),
        "categories": sorted(_STATE.muted_categories),
        "all": bool(_STATE.mute_all),
    }


def status() -> dict:
    """Return current gate state — for jarvis.observer_status service."""
    _load_mutes()
    recent = _recent_within(_STATE.history, 3600)
    spoken = [a for a in recent if a.was_spoken]
    suppressed = [a for a in recent if not a.was_spoken]
    return {
        "announcements_last_hour": len(spoken),
        "suppressed_last_hour": len(suppressed),
        "muted_entities": sorted(_STATE.muted_entities),
        "muted_categories": sorted(_STATE.muted_categories),
        "last_announcement": (
            {
                "message": spoken[-1].message,
                "seconds_ago": int(_now() - spoken[-1].timestamp),
            } if spoken else None
        ),
    }


def recent_announcements(n: int = 5) -> list[str]:
    """Return the last N spoken announcement messages — for reasoning dedup."""
    spoken = [a.message for a in _STATE.history if a.was_spoken]
    return spoken[-n:]


# ─── Internal helpers ──────────────────────────────────────────────────────

def _messages_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """
    Crude similarity check — two messages are "similar" if they share a
    high fraction of their non-trivial words. No NLP, no dependencies.
    """
    def tokens(s: str) -> set[str]:
        return {
            w.lower().strip(".,!?:;")
            for w in s.split()
            if len(w) > 3  # ignore "the", "and", "is", etc.
        }

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    smaller = min(len(ta), len(tb))
    return (overlap / smaller) >= threshold
