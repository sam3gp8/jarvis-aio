"""
JARVIS — Intrusion snapshots & false-alarm call-off (v6.68.0).

Two additions to the existing SafetyManager intrusion flow:

  1. Snapshot: when an intrusion is confirmed on camera, grab a still from that
     camera and make it available with the alert — so the notification can show
     WHO/what triggered it, and the panel can display it.

  2. Call-off: let the user declare a false alarm. dismiss_intrusion() clears the
     active investigation, suppresses further escalation for a cooldown, and
     records the false alarm so repeated benign triggers can be learned from.

Snapshots are written under /config/www/jarvis/intrusion (served at
/local/jarvis/intrusion/...) so HA can render them in notifications and the
panel. Everything here is defensive and never raises to the caller.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
import json
from pathlib import Path
from typing import Optional

from .paths import config_path, config_path_str, has_hass_config_path

_LOGGER = logging.getLogger(__name__)

# Servable snapshot dir: /config/www/... is exposed at /local/...
SNAPSHOT_DIR = config_path_str("www", "jarvis", "intrusion")
SNAPSHOT_URL_BASE = "/local/jarvis/intrusion"
_MAX_SNAPSHOTS = 40           # keep the last N, prune older

# Call-off state (module-level; the investigation itself lives in SafetyManager)
_called_off_until = 0.0       # suppress escalation until this ts
_CALLOFF_COOLDOWN = 600.0     # 10 min quiet after a false-alarm call-off
_acknowledged_ts = 0.0        # user said "I see it, stand by" (not a false alarm)
_ACK_WINDOW = 300.0           # an acknowledgement holds the auto-escalation this long
_last_snapshot: dict = {}     # {path, url, camera, ts} of the most recent capture
_false_alarms: list = []      # recent {ts, camera, area} for learning
_last_decision_id: Optional[int] = None  # decision_record row id of the latest intrusion
_decision_generation = 0
_pending_decision_generations: set[int] = set()
_dismissed_decision_generations: set[int] = set()

# ── Kernel situation mirror (Phase 3 → MCU Phase D/D1, PARITY, log-only) ─────────
# The intrusion lifecycle is mirrored into kernel.situation ALONGSIDE the existing
# authoritative path, so the generalised state machine can be proven to track the
# same episodes before anything flips onto it. Entirely best-effort: a mirror
# failure never affects intrusion handling. Lifecycle mapping:
#   investigating → open + INVESTIGATING   confirmed  → CONFIRMED
#   unresolved    → RESOLVED               dismissed  → BENIGN → RESOLVED
#
# D1 earns *parity* (not just shadow): after each mirrored event, the kernel
# situation's resulting state is compared against what the legacy intrusion
# verdict implies and the agreement is recorded + logged (``_record_parity``). A
# divergence (verdict says confirmed but the kernel situation lags) is logged at
# WARNING so a mirror bug is visible. Still LOG-ONLY and owner-gated: nothing
# here is authoritative — the legacy SafetyManager path owns the intrusion
# decision. Flipping the kernel situation to authoritative is a separate,
# owner-approved step and is deliberately NOT taken here.
_situation_mgr = None            # lazily-built kernel.SituationManager
_situation_id: Optional[str] = None   # the current open intrusion situation, if any
_last_parity: Optional[dict] = None   # most recent parity comparison (observability/tests)

# Lifecycle rank: the kernel situation must have reached AT LEAST the stage the
# legacy verdict implies. (Values mirror kernel.situation's state constants.)
_STAGE_RANK = {"possible": 1, "investigating": 2, "confirmed": 3,
               "response": 4, "benign": 4, "resolved": 5}
# Minimum kernel-situation rank expected for each legacy intrusion verdict.
_EXPECTED_RANK = {"investigating": 2, "confirmed": 3, "unresolved": 5,
                  "dismissed": 5}


def _record_parity(mgr, action: str, episode_id: Optional[str]) -> None:
    """Compare the kernel situation's resulting state against the legacy verdict
    and record/log agreement. Log-only: never affects intrusion handling.

    Agreement = the kernel situation reached at least the lifecycle stage the
    verdict implies (monotonic ranks). A verdict with no open episode to compare
    (e.g. a dismiss with nothing active) is simply not scored."""
    global _last_parity
    expected = _EXPECTED_RANK.get(action)
    if expected is None or episode_id is None:
        return
    try:
        sit = mgr.get(episode_id)
    except Exception:
        sit = None
    actual_state = sit.state if sit is not None else None
    actual_rank = _STAGE_RANK.get(actual_state, 0)
    agree = actual_rank >= expected
    _last_parity = {
        "action": action, "episode_id": episode_id,
        "expected_rank": expected, "actual_state": actual_state,
        "actual_rank": actual_rank, "agree": agree,
    }
    if agree:
        _LOGGER.debug("intrusion situation parity OK: verdict %r → kernel %r",
                      action, actual_state)
    else:
        _LOGGER.warning(
            "intrusion situation parity DIVERGENCE: verdict %r but kernel "
            "situation is %r (episode %s)", action, actual_state, episode_id)


def _get_situation_manager(hass):
    global _situation_mgr
    if _situation_mgr is None:
        from .kernel import SituationManager
        _situation_mgr = SituationManager(
            config_path_str("jarvis", "situations.db", hass=hass))
    return _situation_mgr


def _mirror_situation_sync(hass, action: str, *, reason: str = "",
                           breach_area: Optional[str] = None,
                           camera: Optional[str] = None) -> None:
    """Mirror one intrusion lifecycle event into kernel.situation.

    SYNC — run via the executor (it does SQLite I/O). Best-effort: never raises
    into the caller. Tracks the current episode in ``_situation_id``.
    """
    global _situation_id
    try:
        from .kernel import situation as S
        mgr = _get_situation_manager(hass)
        cur = mgr.get(_situation_id) if _situation_id else None
        episode_id: Optional[str] = None   # the situation this verdict acted on

        if action == "investigating":
            if cur is None or cur.terminal:
                sit = mgr.open("intrusion", subject=(breach_area or camera),
                               location=breach_area, data={"reason": reason})
                _situation_id = sit.id
                mgr.transition(sit.id, S.INVESTIGATING, reason=reason or "investigating")
            elif cur.state == S.POSSIBLE:
                mgr.transition(cur.id, S.INVESTIGATING, reason=reason or "investigating")
            # already INVESTIGATING/CONFIRMED → nothing to do
            episode_id = _situation_id

        elif action == "confirmed":
            if cur is None or cur.terminal:
                sit = mgr.open("intrusion", subject=(breach_area or camera),
                               location=breach_area)
                _situation_id = sit.id
                cur = mgr.transition(sit.id, S.INVESTIGATING, reason="confirmed")
            if cur.state == S.POSSIBLE:
                cur = mgr.transition(cur.id, S.INVESTIGATING, reason="confirmed")
            if cur.state == S.INVESTIGATING:
                mgr.transition(cur.id, S.CONFIRMED, reason=reason or "confirmed")
            episode_id = _situation_id

        elif action == "unresolved":
            if cur is not None and not cur.terminal:
                episode_id = cur.id
                mgr.transition(cur.id, S.RESOLVED, reason=reason or "unresolved")
            _situation_id = None

        elif action == "dismissed":
            if cur is not None and not cur.terminal:
                episode_id = cur.id
                if cur.state in (S.POSSIBLE, S.INVESTIGATING, S.CONFIRMED):
                    cur = mgr.transition(cur.id, S.BENIGN, reason=reason or "false alarm")
                if not cur.terminal:
                    mgr.transition(cur.id, S.RESOLVED, reason="false alarm")
            _situation_id = None

        # Parity (log-only): verify the kernel situation agrees with the verdict.
        _record_parity(mgr, action, episode_id)
        # D4: publish the transition as a canonical JarvisEvent (parity — it
        # enters the stream + ledger; no consumer reacts yet). Best-effort.
        if episode_id:
            sit_after = mgr.get(episode_id)
            if sit_after is not None:
                from . import events
                events.publish_situation(hass, sit_after, action=action)
    except Exception as exc:
        _LOGGER.debug("intrusion: situation mirror failed (%s): %s", action, exc)


def begin_decision_generation() -> int:
    """Start a fresh intrusion decision cycle and invalidate stale record IDs."""
    global _decision_generation, _last_decision_id
    _decision_generation += 1
    _last_decision_id = None
    _pending_decision_generations.add(_decision_generation)
    return _decision_generation


def clear_pending_generation(generation: int) -> None:
    """Clear a decision generation after its producer exits or is cancelled."""
    _pending_decision_generations.discard(generation)


def acknowledge(reason: str = "") -> dict:
    """User acknowledges the alert ('I see it', 'I'm looking', 'standby') WITHOUT
    declaring it a false alarm. This holds the no-response auto-escalation for a
    window — the user is handling it — but does NOT suppress evidence-based
    escalation (if a person appears on camera, JARVIS still alerts). Never
    raises."""
    global _acknowledged_ts
    _acknowledged_ts = time.time()
    _LOGGER.info("JARVIS: intrusion acknowledged by user%s — holding auto-escalation",
                 f" ({reason})" if reason else "")
    return {"ok": True, "held_seconds": int(_ACK_WINDOW)}


def is_acknowledged() -> bool:
    """Whether a recent user acknowledgement is holding the no-response timeout."""
    return (time.time() - _acknowledged_ts) < _ACK_WINDOW


async def capture_snapshot(hass, camera_entity: str,
                           tag: str = "intrusion") -> Optional[dict]:
    """Grab a still from camera_entity and write it to the servable dir. Returns
    {path, url, camera, ts} or None. Never raises."""
    if not camera_entity:
        return None
    try:
        global SNAPSHOT_DIR
        if has_hass_config_path(hass):
            SNAPSHOT_DIR = config_path_str("www", "jarvis", "intrusion", hass=hass)
        from homeassistant.components.camera import async_get_image as _get_image
        image = await _get_image(hass, camera_entity, timeout=10)
        content = getattr(image, "content", None)
        if not content:
            return None
        ts = int(time.time())
        slug = camera_entity.split(".", 1)[-1]
        fname = f"{tag}_{slug}_{ts}.jpg"
        path = os.path.join(SNAPSHOT_DIR, fname)
        await hass.async_add_executor_job(_store_snapshot, path, content)
        info = {
            "path": path,
            "url": f"{SNAPSHOT_URL_BASE}/{fname}",
            "camera": camera_entity,
            "ts": ts,
        }
        global _last_snapshot
        _last_snapshot = info
        _LOGGER.info("JARVIS: intrusion snapshot saved from %s → %s",
                     camera_entity, info["url"])
        return info
    except Exception as exc:
        _LOGGER.debug("intrusion snapshot failed for %s: %s", camera_entity, exc)
        return None


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _store_snapshot(path: str, data: bytes) -> None:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    _write_bytes(path, data)
    _prune_old()


def _prune_old() -> None:
    try:
        files = [
            os.path.join(SNAPSHOT_DIR, f)
            for f in os.listdir(SNAPSHOT_DIR)
            if f.endswith(".jpg")
        ]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in files[_MAX_SNAPSHOTS:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def last_snapshot() -> Optional[dict]:
    """The most recent intrusion snapshot info, or None."""
    return _last_snapshot or None


# ── false-alarm call-off ─────────────────────────────────────────────────────

def set_last_decision_id(record_id, *, generation: Optional[int] = None) -> Optional[int]:
    """Remember the decision_record row id of the most recent intrusion, so a
    later call-off attaches its outcome to *that* record rather than guessing
    the most-recent-of-kind (which mis-attributes when intrusions overlap).

    A stale task can resume after a dismissal. When that generation was dismissed
    while its insert was pending, return its record ID so the caller can persist
    the outcome against the exact row instead of publishing it as active.
    """
    global _last_decision_id
    if record_id is None:
        if generation is not None:
            _pending_decision_generations.discard(generation)
            _dismissed_decision_generations.discard(generation)
        return None
    try:
        decision_id = int(record_id)
    except Exception:
        if generation is not None:
            _pending_decision_generations.discard(generation)
            _dismissed_decision_generations.discard(generation)
        return None
    if generation is not None:
        _pending_decision_generations.discard(generation)
        if generation in _dismissed_decision_generations:
            _dismissed_decision_generations.discard(generation)
            return decision_id
        if generation != _decision_generation:
            return None
    _last_decision_id = decision_id
    return None


def _dismiss_intrusion_state(reason: str = "") -> tuple[dict, int | None, bool]:
    """Declare the current/last intrusion a false alarm. Sets a suppression
    window so the SafetyManager stops escalating, and records it. The
    SafetyManager consults is_called_off() and clears its investigation. Never
    raises."""
    global _called_off_until, _last_decision_id, _decision_generation
    dismissed_generation = _decision_generation
    decision_pending = dismissed_generation in _pending_decision_generations
    if decision_pending:
        _dismissed_decision_generations.add(dismissed_generation)
    _decision_generation += 1
    _called_off_until = time.time() + _CALLOFF_COOLDOWN
    rec = {
        "ts": int(time.time()),
        "reason": str(reason or ""),
        "camera": (_last_snapshot or {}).get("camera", ""),
    }
    _false_alarms.append(rec)
    if len(_false_alarms) > 50:
        del _false_alarms[:-50]
    _LOGGER.info("JARVIS: intrusion called off by user%s — suppressing escalation "
                 "for %ds", f" ({reason})" if reason else "", int(_CALLOFF_COOLDOWN))
    decision_id = _last_decision_id
    _last_decision_id = None
    return ({"ok": True, "suppressed_seconds": int(_CALLOFF_COOLDOWN),
             "recorded": rec}, decision_id, decision_pending)


def _persist_dismissal(decision_id: int | None) -> None:
    try:  # Decision Record outcome: a called-off intrusion was a false alarm.
        from . import decision_record
        if decision_id is not None:
            # Attach the verdict to the exact record for this intrusion.
            if not decision_record.set_outcome(
                    decision_id, "wrong", "dismiss_intrusion"):
                # Already judged or gone — fall back to most-recent-of-kind.
                decision_record.set_outcome_recent(
                    "intrusion", "wrong", "dismiss_intrusion", max_age=7200.0)
        else:
            decision_record.set_outcome_recent(
                "intrusion", "wrong", "dismiss_intrusion", max_age=7200.0)
    except Exception:
        pass


def dismiss_intrusion(reason: str = "") -> dict:
    result, decision_id, decision_pending = _dismiss_intrusion_state(reason)
    if not decision_pending:
        _persist_dismissal(decision_id)
    return result


async def async_dismiss_intrusion(hass, reason: str = "") -> dict:
    result, decision_id, decision_pending = _dismiss_intrusion_state(reason)
    if decision_pending:
        return result
    persist_task = hass.async_add_executor_job(
        _persist_dismissal, decision_id
    )
    try:
        await asyncio.shield(persist_task)
    except asyncio.CancelledError:
        await asyncio.shield(persist_task)
        raise
    # Shadow: resolve the mirrored situation as a false alarm — AFTER the shielded
    # persist so it never changes the critical path's cancellation semantics.
    try:
        await hass.async_add_executor_job(functools.partial(
            _mirror_situation_sync, hass, "dismissed", reason=reason))
    except Exception:
        pass
    return result


def is_called_off() -> bool:
    """Whether a user call-off is currently suppressing escalation."""
    return time.time() < _called_off_until


def clear_calloff() -> None:
    """Reset the suppression (e.g. on a genuinely new, unrelated trigger)."""
    global _called_off_until, _acknowledged_ts
    _called_off_until = 0.0
    _acknowledged_ts = 0.0


def false_alarm_count(within_seconds: float = 86400.0) -> int:
    """How many false alarms the user has called off recently (for learning /
    threshold tuning). Default window: 24h."""
    cutoff = time.time() - within_seconds
    return sum(1 for r in _false_alarms if r.get("ts", 0) >= cutoff)


def status() -> dict:
    """Snapshot + call-off status for the panel/agent."""
    return {
        "last_snapshot": _last_snapshot or None,
        "called_off": is_called_off(),
        "acknowledged": is_acknowledged(),
        "suppressed_for": max(0, int(_called_off_until - time.time())),
        "false_alarms_24h": false_alarm_count(),
    }


# ── event log + labeling + learning (v6.76.0) ────────────────────────────────
# A reviewable history of every intrusion event with its snapshot, which the
# user can label real/false. Those labels feed a narrow suppression: a pattern
# repeatedly labelled a false alarm stops firing the LOW-CONFIDENCE alerts (the
# initial "investigating" ping and the unanswered "unresolved" notice).
#
# HARD SAFETY RULE: learning may NEVER suppress a CONFIRMED intrusion — a person
# confirmed on camera by vision, or motion tracing a real inward route, always
# alerts regardless of how many times a pattern was called a false alarm. The
# learning only damps the noisy, unconfirmed path.

LOG_PATH = config_path("jarvis", "intrusion_log.json")
_MAX_LOG = 200                 # keep the last N events
_LEARN_WINDOW = 30 * 86400.0   # labels older than this stop counting
_LEARN_MIN_FALSE = 3           # this many false labels ⇒ damp the weak alerts
_log: list = []
_log_loaded = False


def _load_log() -> list:
    """Read the persisted event log (once per run). Never raises."""
    global _log, _log_loaded
    if _log_loaded:
        return _log
    _log_loaded = True
    try:
        if LOG_PATH.exists():
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _log = data[-_MAX_LOG:]
    except Exception as exc:
        _LOGGER.debug("intrusion log: load failed: %s", exc)
        _log = []
    return _log


def _write_log(data: list) -> None:
    """Blocking write of an event-log snapshot. Never raises."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as exc:
        _LOGGER.debug("intrusion log: save failed: %s", exc)


def _save_log() -> None:
    """Persist the event log (blocking). Never raises."""
    _write_log(list(_log[-_MAX_LOG:]))


async def async_load(hass) -> None:
    """Do the one-time log read off the event loop, so later sync reads
    (get_log, learning_summary, should_damp_weak_alert) never touch disk."""
    if not _log_loaded:
        await hass.async_add_executor_job(_load_log)


async def _async_persist(hass) -> None:
    # Snapshot on the loop (where the log is mutated); write in the executor.
    await hass.async_add_executor_job(_write_log, list(_log[-_MAX_LOG:]))


def _pattern_key(area: Optional[str], camera: Optional[str],
                 ts: Optional[float] = None) -> str:
    """Group events by where + roughly when, so 'the kitchen window in the
    afternoon' is one learnable pattern."""
    where = (area or camera or "unknown").strip().lower()
    hour = time.localtime(ts or time.time()).tm_hour
    bucket = hour // 3           # 8 three-hour buckets across the day
    return f"{where}|{bucket}"


def record_event(kind: str, reason: str = "", breach: Optional[str] = None,
                 breach_area: Optional[str] = None, camera: Optional[str] = None,
                 snapshot: Optional[dict] = None, zones: Optional[list] = None,
                 max_depth: Optional[int] = None, save: bool = True) -> dict:
    """Append an intrusion event to the reviewable log. kind is one of
    'investigating' | 'unresolved' | 'confirmed' | 'false_alarm'. Never raises.
    Event-loop callers use :func:`async_record_event`."""
    _load_log()
    ts = time.time()
    ev = {
        "id": f"evt_{int(ts)}_{len(_log)}",
        "ts": ts,
        "kind": str(kind or "investigating"),
        "reason": reason or "",
        "breach": breach or "",
        "breach_area": breach_area or "",
        "camera": camera or (snapshot or {}).get("camera") or "",
        "snapshot_url": (snapshot or {}).get("url") or "",
        "snapshot_path": (snapshot or {}).get("path") or "",
        "zones": list(zones or []),
        "max_depth": max_depth,
        "label": None,
        "pattern": _pattern_key(breach_area, camera, ts),
    }
    _log.append(ev)
    if len(_log) > _MAX_LOG:
        del _log[:-_MAX_LOG]
    if save:
        _save_log()
    return ev


async def async_record_event(hass, kind: str, **kwargs) -> dict:
    """:func:`record_event` for event-loop callers (file I/O in the executor)."""
    await async_load(hass)
    ev = record_event(kind, save=False, **kwargs)
    await _async_persist(hass)
    # Shadow: mirror the lifecycle into kernel.situation, off-loop, best-effort.
    try:
        await hass.async_add_executor_job(functools.partial(
            _mirror_situation_sync, hass, kind,
            reason=kwargs.get("reason", "") or "",
            breach_area=kwargs.get("breach_area"),
            camera=kwargs.get("camera")))
    except Exception:
        pass
    return ev


def get_log(limit: int = 50) -> list:
    """Most recent events first, for the panel."""
    _load_log()
    try:
        limit = max(1, min(int(limit), _MAX_LOG))
    except (ValueError, TypeError):
        limit = 50
    return list(reversed(_log[-limit:]))


def label_event(event_id: str, label: str, save: bool = True) -> dict:
    """Mark an event 'real' or 'false' (or None to clear). This is the training
    signal. Never raises."""
    _load_log()
    if label not in ("real", "false", None, ""):
        return {"ok": False, "error": "label must be 'real' or 'false'"}
    label = label or None
    for ev in _log:
        if ev.get("id") == event_id:
            ev["label"] = label
            ev["labeled_ts"] = time.time()
            if save:
                _save_log()
            return {"ok": True, "id": event_id, "label": label}
    return {"ok": False, "error": "event not found"}


async def async_label_event(hass, event_id: str, label: str) -> dict:
    """:func:`label_event` for event-loop callers (file I/O in the executor)."""
    await async_load(hass)
    res = label_event(event_id, label, save=False)
    if res.get("ok"):
        await _async_persist(hass)
    return res


def pattern_verdict(area: Optional[str], camera: Optional[str],
                    ts: Optional[float] = None) -> dict:
    """How this location/time pattern has been labelled historically.
    {false_count, real_count, damp} — damp=True means the weak alerts for this
    pattern should stay quiet. A single 'real' label anywhere in the window
    cancels damping outright: if it was ever genuinely an intruder here, we do
    not learn to ignore it."""
    _load_log()
    key = _pattern_key(area, camera, ts)
    cutoff = time.time() - _LEARN_WINDOW
    false_n = real_n = 0
    for ev in _log:
        if ev.get("pattern") != key or ev.get("ts", 0) < cutoff:
            continue
        if ev.get("label") == "false":
            false_n += 1
        elif ev.get("label") == "real":
            real_n += 1
    damp = (real_n == 0) and (false_n >= _LEARN_MIN_FALSE)
    return {"false_count": false_n, "real_count": real_n, "damp": damp,
            "pattern": key}


def should_damp_weak_alert(area: Optional[str], camera: Optional[str]) -> bool:
    """True when the low-confidence alerts for this pattern should stay quiet.
    NEVER consulted for a confirmed intrusion — only for the initial
    'investigating' ping and the unanswered 'unresolved' notice."""
    try:
        return bool(pattern_verdict(area, camera).get("damp"))
    except Exception:
        return False


def learning_summary() -> dict:
    """Panel summary of what JARVIS has learned from labels."""
    _load_log()
    labeled = [e for e in _log if e.get("label")]
    patterns: dict = {}
    for e in labeled:
        p = e.get("pattern") or "?"
        d = patterns.setdefault(p, {"false": 0, "real": 0})
        d[e["label"]] = d.get(e["label"], 0) + 1
    damped = [p for p, d in patterns.items()
              if d.get("real", 0) == 0 and d.get("false", 0) >= _LEARN_MIN_FALSE]
    return {
        "events": len(_log),
        "labeled": len(labeled),
        "patterns": patterns,
        "damped_patterns": damped,
        "min_false_to_damp": _LEARN_MIN_FALSE,
    }
