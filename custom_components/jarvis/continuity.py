"""Continuity-of-self live binder (roadmap Phase I, I2 — shadow).

Bridges the pure ``kernel.agency_state`` primitive to the live integration:

* on boot, :func:`boot_summary` reloads the last agency snapshot and logs what
  JARVIS was in the middle of before the restart — observe only;
* on a periodic tick (and at boot), :func:`capture_now` reads the live goals,
  open situations and mode and writes a fresh snapshot, so the next boot has
  something to resume from. It also captures the Phase I-B *cognitive context*
  — ``intent`` + chosen ``plan`` (primary active goal), salient ``beliefs``
  (WorldModel belief view), in-flight ``execution`` (kernel journal), a
  restricted ``autonomy`` posture (modes), ``learning`` suggestions pending
  review (pattern_analyzer) and the last-identified ``identity`` (recognition)
  — so the boot continuity line can say who JARVIS was serving and what it was
  thinking and doing, not just what it had committed to.

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

# ENFORCE (roadmap Phase I-B.4) — owner-gated, DEFAULT OFF. When True, a boot
# *resume* (via :func:`resume_summary`) is SOURCED from the cognitive snapshot —
# the intent / chosen plan / … JARVIS held before the restart. When False
# (default) resume falls back to the I-A commitment-only summary, so today's
# behaviour is unchanged. No boot path consumes ``resume_summary`` yet (the
# announce seam is I-A's own I4, also owner-gated); this ships the capability
# ready for the owner to flip — never flipped autonomously. agency_state's live
# adoption stage therefore stays `shadow` while this is OFF.
CONTINUITY_RESUME_ENFORCE = False

# I4 announce seam (roadmap Phase I-A.4) — owner-gated, DEFAULT OFF. When True,
# :func:`announce_resume` speaks the boot continuity line through the output seam
# at startup, so JARVIS says aloud what it was in the middle of before the
# restart instead of only logging it. When False (default) it is completely
# silent — behaviour-identical to today — so this ships the seam ready for the
# owner to flip, never flipped autonomously. The spoken line is sourced from
# :func:`resume_summary` (cognitive when CONTINUITY_RESUME_ENFORCE is on, else
# commitment-only). Fail-safe: any error is swallowed and the boot path proceeds.
CONTINUITY_RESUME_ANNOUNCE = False

# SHADOW (roadmap Phase V — Long-Horizon Agency). On each capture tick, model the
# live active goals as kernel.long_horizon goals (their steps → milestones) and
# log a progress roll-up — observe-only. Nothing persists or resumes them durably
# yet (that is the parity/enforce rung: a goal surviving a restart with correct
# progress); this quantifies goal progress in the primitive's terms. Flip
# LONG_HORIZON_SHADOW to False to silence it. Fail-safe: never raises into capture.
LONG_HORIZON_SHADOW = True

# Goal step status → long_horizon milestone status.
_GOAL_STEP_TO_MILESTONE = {
    "done": "done", "complete": "done", "completed": "done",
    "skipped": "skipped", "cancelled": "skipped",
    "failed": "blocked", "blocked": "blocked",
    "active": "active", "in_progress": "active",
}

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


def _live_execution(hass=None) -> str:
    """A short summary of what JARVIS was mid-executing — the kernel journal's
    in-flight steps (H4): steps started but not finished, which is exactly what a
    restart would need to resume. Empty when nothing is in flight or on any
    failure. Never raises. Runs only on the executor thread (capture_now is
    dispatched there), so the journal read never touches the event loop.
    """
    try:
        from .kernel.journal import ExecutionJournal
        jr = ExecutionJournal(config_path_str("jarvis", "journal.db", hass=hass))
        flight = jr.in_flight() or []
    except Exception:
        return ""
    try:
        if not flight:
            return ""
        actions = [str(getattr(s, "action", "") or "").strip() for s in flight]
        first = next((a for a in actions if a), "")
        n = len(flight)
        if n == 1:
            return f"running: {first}" if first else "1 step running"
        return f"{n} steps running; e.g. {first}" if first else f"{n} steps running"
    except Exception:  # pragma: no cover - defensive
        return ""


def _live_autonomy() -> str:
    """The active mode's autonomy posture, surfaced only when it is RESTRICTED —
    autonomy graduations may not auto-execute right now. That is the notable case
    worth resuming; in the default/permissive case (and on failure) this is
    empty, so an otherwise-bare snapshot stays byte-identical to before. Never
    raises."""
    try:
        from . import modes
        if not modes.mode_allows_auto_actions():
            return "auto-actions suppressed"
    except Exception:
        return ""
    return ""


def _live_learning() -> str:
    """How many learned automation suggestions are waiting on the user's review
    (pattern_analyzer). Empty when none or on failure. Never raises. Runs only on
    the executor thread (capture_now is dispatched there), so the sqlite read
    never touches the event loop."""
    try:
        from . import pattern_analyzer
        pending = pattern_analyzer.get_analyzer().get_pending_suggestions() or []
    except Exception:
        return ""
    n = len(pending)
    if not n:
        return ""
    return f"{n} suggestion{'s' if n != 1 else ''} pending review"


def _ago(age_seconds) -> str:
    """A compact ' (seen …)' suffix for a recency in seconds, or '' when there is
    no sane timestamp (sensor rows carry a sentinel age)."""
    try:
        age = int(age_seconds or 0)
    except Exception:
        return ""
    if age <= 0 or age >= 86400:   # no/implausible timestamp → omit
        return ""
    if age < 90:
        return f" (seen {age}s ago)"
    if age < 5400:
        return f" (seen {age // 60}m ago)"
    return f" (seen {age // 3600}h ago)"


def _live_identity(hass=None) -> str:
    """The last-identified principal — the most recently recognised KNOWN person
    (``recognition.recent_faces``, newest first), skipping unknown sightings and
    best-effort low-confidence guesses (a guess must never stand in for who
    JARVIS believes it is serving, matching the safety posture elsewhere).
    Rendered as the name (+ ' (resident)' when a flagged household resident),
    with a recency suffix when known. Empty without a live hass or when there is
    no known identification, and on any failure. Never raises. Runs only on the
    executor thread (capture_now is dispatched there)."""
    if hass is None:
        return ""
    try:
        from . import recognition
        rows = recognition.recent_faces(hass) or []
    except Exception:
        return ""
    try:
        for r in rows:  # newest first
            if r.get("is_unknown") or r.get("is_low_confidence"):
                continue
            name = str(r.get("name", "")).strip()
            if not name:
                continue
            tag = " (resident)" if r.get("is_resident") else ""
            return f"{name}{tag}{_ago(r.get('age_seconds'))}"
        return ""
    except Exception:  # pragma: no cover - defensive
        return ""


def _live_cognitive(hass=None) -> Optional["agency_state.CognitiveContext"]:
    """Build the Phase I-B cognitive context from live subsystems (I-B — shadow).

    Populated fields, each from an unambiguous live source:

    * ``intent`` / ``plan`` — JARVIS's primary active goal (a goal *is* an
      outcome pursued across time);
    * ``beliefs`` — the most confident salient knowledge beliefs, from the
      WorldModel belief view (E1);
    * ``execution`` — what was mid-flight, from the kernel execution journal (H4);
    * ``autonomy`` — the active mode's auto-action posture (``modes``);
    * ``learning`` — automation suggestions pending review (``pattern_analyzer``);
    * ``identity`` — the last-identified principal (``recognition.recent_faces``).

    ``attention`` is deliberately left for Phase K (Attention & Working Memory),
    which will build a durable focus model; the remaining fields map to
    subsystems that are still pure / not yet built (delegations → Phase O,
    uncertainty → pure, …). Those stay empty and are dropped by ``capture``.
    Returns None when nothing is populated, so a commitment-only snapshot stays
    byte-identical to before (behaviour-preserving). Never raises.
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
    execution = _live_execution(hass)
    autonomy = _live_autonomy()
    learning = _live_learning()
    identity = _live_identity(hass)
    try:
        cog = agency_state.CognitiveContext(
            intent=intent, plan=plan, beliefs=beliefs, execution=execution,
            autonomy=autonomy, learning=learning, identity=identity)
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


def _long_horizon_shadow(hass=None) -> None:
    """Model the live active goals as kernel.long_horizon goals and log a progress
    roll-up (Phase V — shadow). Observe-only: nothing persists or resumes them
    durably yet (that is the parity/enforce rung — a goal surviving a restart with
    correct progress). This quantifies goal progress in the primitive's terms,
    naming each goal's milestones from its steps. Never raises."""
    if not LONG_HORIZON_SHADOW:
        return
    try:
        import time as _time
        from .kernel import long_horizon as LH
        from . import goals as _goals
        now = _time.time()
        built = []
        for g in (_goals.active() or []):
            gid = g.get("id")
            if gid is None:
                continue
            milestones = []
            for s in (g.get("steps") or []):
                if not isinstance(s, dict):
                    continue
                label = str(s.get("step", "") or "").strip()
                if not label:
                    continue
                status = _GOAL_STEP_TO_MILESTONE.get(
                    str(s.get("status", "") or "").lower(), LH.PENDING)
                milestones.append({"label": label, "status": status})
            title = str(g.get("title") or g.get("outcome") or "").strip()
            built.append(LH.plan_goal(title, milestones, id=f"goal:{gid}", now=now))
        if built:
            st = LH.summarize(built, now)
            _LOGGER.debug(
                "long_horizon(shadow): %d goal(s) — %d complete, avg progress %.2f",
                st.count, st.complete, st.avg_progress)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("long_horizon shadow failed: %s", exc)


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
        _long_horizon_shadow(hass)   # Phase V: observe-only, never affects capture
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


def resume_summary(hass=None) -> str:
    """The continuity line a boot *resume* would announce (I-B.4 — enforce path).

    Sourced from the cognitive snapshot (intent / chosen plan / …) when
    :data:`CONTINUITY_RESUME_ENFORCE` is on; otherwise the commitment-only
    summary — the I-A fail-safe. **Observe-only for now:** no boot path consumes
    this yet (the announce seam is I-A's I4, owner-gated), so with the switch OFF
    (default) it is behaviour-identical to today and merely ready to flip. Returns
    the string (also useful for tests); never raises.
    """
    if not AGENCY_CAPTURE_ENABLED:
        return ""
    try:
        state = _store(hass).load_latest()
        return agency_state.continuity_summary(
            state, include_cognitive=CONTINUITY_RESUME_ENFORCE)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("agency resume summary failed: %s", exc)
        return ""


async def announce_resume(hass=None, entry=None) -> bool:
    """I4 — speak the boot continuity line through the output seam, if enabled.

    Owner-gated and **default OFF** (:data:`CONTINUITY_RESUME_ANNOUNCE`): with the
    switch off this returns immediately and says nothing, so startup is unchanged.
    When on, it resolves JARVIS's TTS engine + announcement speakers the same way
    Sentinel does and speaks :func:`resume_summary` once. Entirely best-effort —
    any failure (no snapshot, no speakers, TTS error) is swallowed so it can never
    affect the boot path. Returns True only if a line was actually handed to a
    speaker.
    """
    if not (CONTINUITY_RESUME_ANNOUNCE and AGENCY_CAPTURE_ENABLED):
        return False
    try:
        line = resume_summary(hass)
        if not line:
            return False
        from . import jarvis_config
        from .const import CONF_TTS_ENGINE, DEFAULT_TTS_ENGINE
        from .tts_helper import resolve_tts_entity, async_announce
        from .audio_routing import broadcast_target

        tts_entity = resolve_tts_entity(
            hass, jarvis_config.runtime_get(hass, entry, CONF_TTS_ENGINE,
                                            DEFAULT_TTS_ENGINE))
        speakers = broadcast_target(
            hass,
            broadcast_group=(jarvis_config.runtime_get(
                hass, entry, "broadcast_group", "") or None),
            announcement_speakers=jarvis_config.runtime_get(
                hass, entry, "announcement_speakers", None),
        )
        if not tts_entity or not speakers:
            _LOGGER.debug("continuity resume announce: no TTS/speakers configured")
            return False
        return bool(await async_announce(hass, line, tts_entity, speakers,
                                         context="sentinel"))
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("continuity resume announce failed: %s", exc)
        return False


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
        # I-B parity: compare the reloaded *cognitive* snapshot (what JARVIS was
        # thinking before the restart) against live cognitive state now, and log
        # which fields persisted vs changed. Observe-only; drives nothing, never
        # raises into startup.
        try:
            cog_report = agency_state.reconcile_cognitive(
                state.cognitive, _live_cognitive(hass))
            if cog_report.agreed or cog_report.changed:
                _LOGGER.info(
                    "JARVIS: cognitive continuity — %d field(s) unchanged, "
                    "%d changed since restart%s",
                    len(cog_report.agreed), len(cog_report.changed),
                    (f" ({', '.join(cog_report.changed)})"
                     if cog_report.changed else ""),
                )
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("agency cognitive reconcile failed: %s", exc)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("agency boot reconcile failed: %s", exc)
        return None
