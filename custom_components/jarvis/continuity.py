"""Continuity-of-self live binder (roadmap Phase I, I2 — shadow).

Bridges the pure ``kernel.agency_state`` primitive to the live integration:

* on boot, :func:`boot_summary` reloads the last agency snapshot and logs what
  JARVIS was in the middle of before the restart — observe only;
* on a periodic tick (and at boot), :func:`capture_now` reads the live goals,
  open situations and mode and writes a fresh snapshot, so the next boot has
  something to resume from. It also captures the Phase I-B *cognitive context*
  (``intent`` + chosen ``plan`` from the primary active goal, and the salient
  ``beliefs`` from the WorldModel belief view) so the boot continuity line can
  say what JARVIS was thinking, not just what it had committed to.

**Shadow:** it reads live state and writes its own snapshot DB + a log line; it
drives nothing and changes no behaviour. Everything is defensive (a read failure
yields an empty field, never an exception into the boot path). Kill-switch:
:data:`AGENCY_CAPTURE_ENABLED` — set False to disable capture and the boot log.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .kernel import agency_state
from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

# Kill-switch (roadmap Phase I). When False, no snapshot is captured and the boot
# continuity line is suppressed — the primitive sits idle, fully behaviour-safe.
AGENCY_CAPTURE_ENABLED = True

# Cap on retained snapshots (newest-wins); matches the store default.
_SNAPSHOT_KEEP = 20


def _store(hass=None) -> agency_state.AgencyStore:
    return agency_state.AgencyStore(
        config_path_str("jarvis", "agency.db", hass=hass), keep=_SNAPSHOT_KEEP)


def _live_goals() -> List[dict]:
    """Active goals as plain capture rows; empty on any failure."""
    try:
        from . import goals
    except Exception:
        return []
    rows: List[dict] = []
    try:
        for g in goals.active():
            gid = g.get("id")
            if gid is None:
                continue
            rows.append({
                "id": gid,
                "label": (g.get("title") or g.get("outcome") or "").strip(),
                "status": g.get("status") or "active",
            })
    except Exception:
        return []
    return rows


def _live_mode() -> Optional[str]:
    try:
        from . import modes
        return modes.active_mode()
    except Exception:
        return None


_DONE_STEP_STATES = frozenset({"done", "complete", "completed", "skipped", "cancelled"})


def _plan_summary(goal: dict) -> str:
    """A compact 'chosen-plan' line for a goal: progress + the next pending step.
    Empty when the goal carries no steps. Never raises."""
    try:
        steps = goal.get("steps") or []
        if not steps:
            return ""
        total = len(steps)
        done = sum(1 for s in steps
                   if str((s or {}).get("status", "")).lower() in _DONE_STEP_STATES)
        nxt = next((str((s or {}).get("step", "")).strip() for s in steps
                    if str((s or {}).get("status", "")).lower() not in _DONE_STEP_STATES), "")
        return f"{done}/{total} done; next: {nxt}" if nxt else f"{done}/{total} done"
    except Exception:
        return ""


_MAX_BELIEFS = 5


def _live_beliefs(hass=None) -> tuple:
    """The most confident salient knowledge beliefs as short summaries, from the
    WorldModel belief view (E1). Drops the generic identity self-belief, ranks
    the rest by confidence and caps the count. Empty when there is no hass (the
    belief view needs live HA state) or on any failure — defensive, never raises.
    Runs only on the executor thread (capture_now is dispatched there), so the
    knowledge-store read never touches the event loop.
    """
    if hass is None:
        return ()
    try:
        from .kernel.world_model import WorldModel
        raw = WorldModel(hass).beliefs() or []
    except Exception:
        return ()
    try:
        # beliefs() always leads with JARVIS's identity self-assertion; the
        # salient knowledge facts follow. Rank those by confidence, highest first.
        facts = [b for b in raw[1:] if getattr(b, "proposition", "")]
        facts.sort(key=lambda b: float(getattr(b, "probability", 0.0) or 0.0),
                   reverse=True)
        return tuple(
            f"{b.proposition} (p={float(getattr(b, 'probability', 0.0) or 0.0):.2f})"
            for b in facts[:_MAX_BELIEFS]
        )
    except Exception:  # pragma: no cover - defensive
        return ()


def _live_cognitive(hass=None) -> Optional["agency_state.CognitiveContext"]:
    """Build the Phase I-B cognitive context from live subsystems (I-B — shadow).

    Populated fields, each from an unambiguous live source:

    * ``intent`` / ``plan`` — JARVIS's primary active goal (a goal *is* an
      outcome pursued across time);
    * ``beliefs`` — the most confident salient knowledge beliefs, from the
      WorldModel belief view (E1).

    The remaining CognitiveContext fields map to subsystems that are still pure /
    not yet built (delegations → Phase O, uncertainty → pure, …), so they are
    left empty and dropped by ``capture``. Returns None when nothing is
    populated, so a commitment-only snapshot stays byte-identical to before
    (behaviour-preserving). Never raises.
    """
    intent, plan = "", ""
    try:
        from . import goals
        active = goals.active()
        if active:
            primary = active[0] or {}
            intent = (primary.get("outcome") or primary.get("title") or "").strip()
            plan = _plan_summary(primary)
    except Exception:
        intent, plan = "", ""
    beliefs = _live_beliefs(hass)
    try:
        cog = agency_state.CognitiveContext(intent=intent, plan=plan, beliefs=beliefs)
        return cog if (cog is not None and not cog.is_empty()) else None
    except Exception:  # pragma: no cover - defensive
        return None


def _live_situations(hass=None) -> List[dict]:
    """Open kernel situations as plain capture rows; empty on any failure."""
    try:
        from .kernel.situation import SituationManager
        store = SituationManager(config_path_str("jarvis", "situations.db", hass=hass))
        rows: List[dict] = []
        for s in store.open_situations():
            label = s.kind + (f" · {s.subject}" if getattr(s, "subject", None) else "")
            rows.append({"id": s.id, "label": label, "status": s.state})
        return rows
    except Exception:
        return []


def capture_now(hass=None) -> Optional[agency_state.AgencyState]:
    """Capture + persist the current agency snapshot. Never raises."""
    if not AGENCY_CAPTURE_ENABLED:
        return None
    try:
        state = agency_state.capture(
            mode=_live_mode(),
            goals=_live_goals(),
            situations=_live_situations(hass),
            cognitive=_live_cognitive(hass),  # I-B: intent + plan, dropped if empty
        )
        _store(hass).save(state)
        return state
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("agency capture failed: %s", exc)
        return None


def boot_summary(hass=None) -> str:
    """Log the continuity summary for the snapshot from before this restart.

    Returns the summary string (also useful for tests). Observe-only; a failure
    is swallowed so it can never affect startup.
    """
    if not AGENCY_CAPTURE_ENABLED:
        return ""
    try:
        state = _store(hass).load_latest()
        msg = agency_state.continuity_summary(state)
        _LOGGER.info("JARVIS: %s", msg)
        return msg
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("agency boot summary failed: %s", exc)
        return ""


def boot_reconcile(hass=None):
    """Reconcile the pre-restart snapshot against what is still live (I3 — parity).

    Loads the snapshot from before this restart and checks each remembered goal /
    situation against the ids that are still live now, logging how many JARVIS
    could resume versus how many vanished while it was down. **Observe-only** — it
    computes and logs the reconciliation, drives nothing, and never raises into
    startup. Must run *before* the boot seed capture, so ``load_latest`` still
    returns the pre-restart snapshot rather than a freshly-written one.

    Returns the ``ReconcileReport`` (or None if nothing to reconcile / disabled).
    """
    if not AGENCY_CAPTURE_ENABLED:
        return None
    try:
        state = _store(hass).load_latest()
        if state is None:
            return None
        live_goal_ids = [g["id"] for g in _live_goals()]
        live_situation_ids = [s["id"] for s in _live_situations(hass)]
        report = agency_state.reconcile(
            state,
            live_goal_ids=live_goal_ids,
            live_situation_ids=live_situation_ids,
        )
        _LOGGER.info(
            "JARVIS: continuity reconcile — %d still live, %d vanished while down",
            len(report.still_live), len(report.vanished),
        )
        return report
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("agency boot reconcile failed: %s", exc)
        return None
