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


def _build_long_horizon_goals(now: float) -> list:
    """The live active goals modeled as ``kernel.long_horizon`` goals (steps →
    milestones). Reads ``goals.active()``; returns a list of ``LongHorizonGoal``,
    or ``[]`` on any error. The single builder both the shadow roll-up and the
    durable-ledger parity read, so they never diverge."""
    try:
        from .kernel import long_horizon as LH
        from . import goals as _goals
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
        return built
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("long_horizon build failed: %s", exc)
        return []


def _long_horizon_shadow(hass=None) -> None:
    """Model the live active goals as kernel.long_horizon goals and log a progress
    roll-up (Phase V — shadow). Observe-only. Never raises."""
    if not LONG_HORIZON_SHADOW:
        return
    try:
        import time as _time
        from .kernel import long_horizon as LH
        now = _time.time()
        built = _build_long_horizon_goals(now)
        if built:
            st = LH.summarize(built, now)
            _LOGGER.debug(
                "long_horizon(shadow): %d goal(s) — %d complete, avg progress %.2f",
                st.count, st.complete, st.avg_progress)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("long_horizon shadow failed: %s", exc)


# PARITY (roadmap Phase V — Long-Horizon Agency). Beyond the shadow roll-up, the
# live goals are persisted to a durable LEDGER on each capture, and on boot they
# are RESUMED from that ledger and compared against the goals re-derived from the
# live store — proving a long-horizon goal (AND its milestone progress, which the
# agency_state commitment deliberately does not carry) survives a restart with
# correct progress. Observe-only: the agreement is logged; nothing yet resumes
# FROM the ledger (that is the owner-gated LONG_HORIZON_ENFORCE rung, whose
# fail-safe is session-scoped goals). Kill-switch LONG_HORIZON_PARITY. The ledger
# write never affects capture; the parity read never raises into boot.
LONG_HORIZON_PARITY = True

# ENFORCE (roadmap Phase V — owner-authorized). At boot, the durable ledger (the
# pre-restart snapshot) becomes the AUTHORITATIVE source of a long-horizon
# continuity view: boot_summary surfaces the in-flight multi-day goals it is
# resuming — their titles, progress and next milestone — which nothing surfaced
# before (the parity only logs a match count at debug). This is non-redundant with
# the durable goals store (that silently persists the goals; this gives JARVIS a
# spoken-at-boot continuity view of what it is picking back up) and strictly
# non-actuating — it only surfaces a continuity line, never drives an action.
# Kill-switch back to parity: LONG_HORIZON_ENFORCE / the `long_horizon_enforce`
# config key. FAIL-SAFE: no ledger, nothing in flight, or any error → no line
# (session-scoped, today's silent boot), so a fault can only restore today.
LONG_HORIZON_ENFORCE = True

_LH_LEDGER_KEEP = 50  # cap on goals retained in the durable ledger


def _long_horizon_enforce_enabled() -> bool:
    if not LONG_HORIZON_ENFORCE:
        return False
    try:
        from . import jarvis_config
        if jarvis_config.get("long_horizon_enforce", True) is False:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _long_horizon_resume_line(hass=None) -> str:
    """The authoritative boot continuity view of the multi-day goals being resumed
    FROM the durable ledger (the pre-restart snapshot) — their titles, progress and
    next milestone. Returns ``""`` when enforce is off, nothing is in flight, or on
    any error (fail-safe = today's silent boot). Never raises."""
    if not _long_horizon_enforce_enabled():
        return ""
    try:
        import time as _time
        now = _time.time()
        resumed = [g for g in _long_horizon_load(now, hass) if not g.is_complete]
        if not resumed:
            return ""
        parts = []
        for g in resumed[:5]:
            title = g.title or g.id
            nxt = g.next_milestone()
            tail = f", next: {nxt.label}" if nxt is not None and nxt.label else ""
            parts.append(f"'{title}' ({int(round(g.progress * 100))}%{tail})")
        more = f" (+{len(resumed) - 5} more)" if len(resumed) > 5 else ""
        return f"resuming {len(resumed)} multi-day goal(s): " + "; ".join(parts) + more
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("long_horizon resume line failed: %s", exc)
        return ""


def _lh_ledger_path(hass=None) -> str:
    return config_path_str("jarvis", "long_horizon_ledger.json", hass=hass)


def _long_horizon_persist(hass=None) -> int:
    """Write the live long_horizon goals to the durable ledger so the next boot
    can resume them. Returns the number of goals written; 0 on any error. Atomic
    (temp + replace); never raises."""
    try:
        import json
        import os
        import time as _time
        now = _time.time()
        goals = _build_long_horizon_goals(now)[: _LH_LEDGER_KEEP]
        payload = {"saved_at": now, "goals": [g.to_dict() for g in goals]}
        path = _lh_ledger_path(hass)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
        return len(goals)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("long_horizon ledger persist failed: %s", exc)
        return 0


def _long_horizon_load(now: float, hass=None) -> list:
    """Reconstruct the long_horizon goals persisted in the ledger (the snapshot
    from before this restart). Returns ``[]`` on any error / no ledger."""
    try:
        import json
        from .kernel import long_horizon as LH
        with open(_lh_ledger_path(hass), encoding="utf-8") as f:
            payload = json.load(f)
        out = []
        for d in (payload.get("goals") or []):
            gid = d.get("id")
            if not gid:
                continue
            out.append(LH.plan_goal(
                d.get("title", ""), d.get("milestones") or [], id=gid, now=now))
        return out
    except Exception:
        return []


def _long_horizon_parity(hass=None) -> None:
    """Phase V parity: RESUME the long_horizon goals from the durable ledger and
    compare their progress against the goals re-derived from the live store. Logs
    how many goals resumed with IDENTICAL progress — the resume-after-restart
    proof. Observe-only; nothing resumes FROM the ledger yet. Never raises."""
    if not LONG_HORIZON_PARITY:
        return
    try:
        import time as _time
        now = _time.time()
        live = {g.id: g for g in _build_long_horizon_goals(now)}
        persisted = {g.id: g for g in _long_horizon_load(now, hass)}
        shared = set(live) & set(persisted)
        resumed = sum(
            1 for gid in shared
            if live[gid].resolved == persisted[gid].resolved
            and abs(live[gid].progress - persisted[gid].progress) < 1e-6)
        _LOGGER.debug(
            "long_horizon(parity): ledger=%d goal(s), live=%d, resumed=%d/%d "
            "matched (new=%d, dropped=%d)",
            len(persisted), len(live), resumed, len(shared),
            len(set(live) - set(persisted)), len(set(persisted) - set(live)))
        # Phase AE shadow: a long_horizon scenario (objective → days → restart →
        # resume → completion) — observed only when goals spanned a restart
        # (shared non-empty); it closes iff at least one resumed with identical
        # progress (the resume-after-restart proof).
        if shared:
            _emit_long_horizon_shadow(resumed=bool(resumed))
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("long_horizon parity failed: %s", exc)


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
        # Phase V parity: compare the live goals against the ledger from the
        # PREVIOUS capture/boot (proving durable resume), THEN rewrite the ledger
        # for the next resume. Order matters — parity reads the old ledger first.
        _long_horizon_parity(hass)
        _long_horizon_persist(hass)
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
        # Phase V parity: at boot, prove the long-horizon goals resumed from the
        # ledger written before this restart match the live store. Observe-only.
        _long_horizon_parity(hass)
        # Phase V enforce (owner-authorized): surface the authoritative boot
        # continuity view of the multi-day goals being resumed from the ledger.
        # Non-actuating, fail-safe (empty line → nothing surfaced).
        resume_line = _long_horizon_resume_line(hass)
        if resume_line:
            _LOGGER.info("JARVIS (long-horizon): %s", resume_line)
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


def _emit_long_horizon_shadow(*, resumed: bool) -> None:
    """Phase AE (MCU Certification — shadow): observe a `long_horizon` scenario
    (objective → days → restart → resume → completion) into the ONE shared
    certification ledger via cognitive_core. Closed iff at least one goal resumed
    from the durable ledger with identical progress across the restart; observed
    only when goals actually spanned a restart. Observe-only; never raises."""
    try:
        from . import cognitive_core
        from .kernel import certification as CERT
        cognitive_core.certification_observe(
            CERT.LONG_HORIZON, closed_loop=bool(resumed),
            note="goal(s) resumed across restart with identical progress" if resumed
                 else "goals spanned a restart but none resumed cleanly")
    except Exception:
        pass


def _emit_restart_shadow(*, resumed: bool) -> None:
    """Phase AE (MCU Certification — shadow): observe a `restart` scenario
    (mid-agency restart → reconciliation → resume) into the ONE shared
    certification ledger via cognitive_core. Closed when reconciliation found live
    agency to resume; exercised-but-open when a snapshot existed but nothing was
    still live. Observe-only; kill-switched inside cognitive_core; never raises."""
    try:
        from . import cognitive_core
        from .kernel import certification as CERT
        cognitive_core.certification_observe(
            CERT.RESTART, closed_loop=bool(resumed),
            note="agency resumed after restart" if resumed
                 else "reconciled, nothing still live to resume")
    except Exception:
        pass


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
        # Phase AE shadow: a restart scenario — it closes iff something was still
        # live to resume (a snapshot existed, so this IS a reconciliation pass).
        _emit_restart_shadow(resumed=bool(report.still_live))
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
