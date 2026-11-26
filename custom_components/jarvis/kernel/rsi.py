"""Recursive Self-Improvement (RSI) engine — Phase 1: data models & guardrails.

> Spec: a closed-loop, constitutionally-gated, shadow-verified self-improvement
> engine that leverages existing kernel primitives without introducing unbounded
> self-modification or event-loop instability.

This module is the **foundation only**: the proposal record, the three mutation
tiers, and the three hard guardrails every proposal must clear *before* anything
is ever applied — the immutable-governance denylist, the budget ceiling, and the
anti-oscillation lock. It performs **no mutation** of any kind; promotion, shadow
replay, probation and rollback arrive in later phases on top of these rails.

Design, mirroring the rest of the kernel:
  * Pure and deterministic — ``now`` is passed in, there is no wall clock, no
    Home Assistant import, and no blocking I/O in the guardrail logic, so it is
    trivially unit-testable (see loop_detect / budget for the same discipline).
  * Durable state (proposals + the rolled-back-mutation blacklist) goes through
    ``kernel.persistence`` — the one canonical SQLite seam — in its own database
    file; no existing store is touched.

Hard safety invariants enforced here (non-negotiable):
  1. **Immutable governance core.** No tier may ever target a file in
     ``IMMUTABLE_TARGETS`` — the Constitution, this engine, authority,
     enforcement, certification and boot_guard. A proposal that does is rejected
     as a hard security violation, recorded, and never evaluated.
  2. **Budget ceilings.** Every cycle consumes from a daily mutation + token
     budget with an off-peak execution window; over-budget proposals are refused.
  3. **Anti-oscillation.** A mutation whose hash was previously rolled back, or
     that would close an A→B→A flip inside the sliding window, is rejected and
     the target locked.

Tier 3 (structural source changes) is, by construction, **never live-applied** —
it can only ever be exported as a reviewable patch proposal. Phase 1 encodes the
tier and status vocabulary that guarantee that; the export path lands in Phase 4.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from . import persistence

_LOGGER = logging.getLogger(__name__)

# ── Tier vocabulary ──────────────────────────────────────────────────────────
# Parametric: numeric thresholds / cooldowns / TTLs — autonomously promotable.
# Behavioral: template & routing heuristics — autonomous after N clean replays.
# Structural: core Python source — NEVER live-applied, patch-proposal only.
TIER_PARAMETRIC = 1
TIER_BEHAVIORAL = 2
TIER_STRUCTURAL = 3
_VALID_TIERS = (TIER_PARAMETRIC, TIER_BEHAVIORAL, TIER_STRUCTURAL)

# ── Proposal lifecycle ───────────────────────────────────────────────────────
PROPOSED = "proposed"        # constructed, guardrails not yet run
REJECTED = "rejected"        # failed a guardrail (see reject_reason)
LOCKED = "locked"            # rejected AND the target is locked (oscillation/immutable)
EVALUATING = "evaluating"    # in shadow replay (Phase 2+)
PROMOTED = "promoted"        # Tier 1/2 applied, entering probation (Phase 2+)
PROBATION = "probation"      # applied, watchdog window open (Phase 3+)
CERTIFIED = "certified"      # survived probation (Phase 3+)
ROLLED_BACK = "rolled_back"  # probation failed, checkpoint restored (Phase 3+)
EXPORTED = "exported"        # Tier 3 diff written for human review (Phase 4+)

# ── Immutable governance core (non-overridable denylist) ─────────────────────
# Spelled as repo-relative POSIX paths under the integration root. normalize_target
# collapses any spelling (absolute, custom_components-prefixed, back-slashed) onto
# these so the guard can't be dodged by an alternate path. This set is the one
# thing no tier, no config, and no trust level can ever edit.
IMMUTABLE_TARGETS = frozenset({
    "docs/JARVIS_CONSTITUTION.md",
    "custom_components/jarvis/kernel/rsi.py",
    "custom_components/jarvis/kernel/authority.py",
    "custom_components/jarvis/kernel/enforcement.py",
    "custom_components/jarvis/kernel/certification.py",
    "custom_components/jarvis/boot_guard.py",
    "custom_components/jarvis/enforcement.py",
})

_INTEGRATION_ROOT = "custom_components/jarvis"


def normalize_target(target: str) -> str:
    """Canonicalise a mutation target (file path or config key) for comparison.

    File paths collapse to a repo-relative POSIX path so every spelling of a
    protected file maps onto its ``IMMUTABLE_TARGETS`` entry; config keys (no
    path separator) pass through trimmed. Never raises."""
    s = str(target or "").strip().replace("\\", "/")
    if not s:
        return ""
    # a bare config key (no separators) is returned as-is
    if "/" not in s:
        return s
    # strip a leading ./ and any absolute prefix up to the integration root
    while s.startswith("./"):
        s = s[2:]
    marker = _INTEGRATION_ROOT + "/"
    idx = s.find(marker)
    if idx != -1:
        return s[idx:]
    # a docs/ path (the Constitution) kept relative
    didx = s.find("docs/")
    if didx != -1:
        return s[didx:]
    return s.lstrip("/")


def is_immutable(target: str) -> bool:
    """True if ``target`` resolves onto a protected governance-core file."""
    return normalize_target(target) in IMMUTABLE_TARGETS


def mutation_hash(target: str, proposed_value: Any) -> str:
    """Canonical SHA-256 of (normalized target + proposed change). Stable across
    processes: the value is JSON-canonicalised (sorted keys) so equal mutations
    hash equally and the anti-oscillation guard can recognise a repeat."""
    payload = json.dumps(
        {"t": normalize_target(target), "v": proposed_value},
        sort_keys=True, default=str, ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Records ──────────────────────────────────────────────────────────────────

@dataclass
class MutationProposal:
    """A single proposed self-improvement, before any evaluation or application."""
    proposal_id: str
    tier: int
    target_subsystem: str
    target_key_or_path: str
    current_value: Any
    proposed_value: Any
    hypothesis: str = ""
    evidence_refs: list = field(default_factory=list)
    mutation_hash: str = ""
    status: str = PROPOSED
    reject_reason: str = ""
    created_ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.mutation_hash:
            self.mutation_hash = mutation_hash(self.target_key_or_path, self.proposed_value)

    def as_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "tier": self.tier,
            "target_subsystem": self.target_subsystem,
            "target_key_or_path": self.target_key_or_path,
            "current_value": self.current_value,
            "proposed_value": self.proposed_value,
            "hypothesis": self.hypothesis,
            "evidence_refs": list(self.evidence_refs or []),
            "mutation_hash": self.mutation_hash,
            "status": self.status,
            "reject_reason": self.reject_reason,
            "created_ts": self.created_ts,
        }


@dataclass(frozen=True)
class ReplayEvaluation:
    """Result of a shadow-replay fitness comparison (Baseline vs Candidate).
    Populated in Phase 2; defined here so the vocabulary is stable."""
    proposal_id: str
    delta_fitness: float = 0.0
    delta_outcome: float = 0.0
    delta_latency: float = 0.0
    delta_tokens: float = 0.0
    delta_overrides: float = 0.0
    safety_violations: int = 0
    passed: bool = False
    detail: str = ""


@dataclass
class ProbationRecord:
    """Tracks an applied Tier 1/2 mutation through its watchdog window.
    Populated in Phase 3; defined here so the vocabulary is stable."""
    proposal_id: str
    mutation_hash: str
    applied_ts: float
    window_s: float
    baseline_fault_rate: float = 0.0
    checkpoint_ref: str = ""
    status: str = PROBATION


# ── Budget ceiling ───────────────────────────────────────────────────────────

@dataclass
class RSIBudget:
    """Daily mutation + token ceiling with an off-peak execution window.

    Pure and deterministic: ``now`` (epoch seconds) is passed to every call. The
    window is an inclusive [start_hour, end_hour) in local-less UTC hours; a
    window that wraps midnight (start > end) is honoured. This complements
    kernel.budget (which caps live actuations) — RSI spends from its own, far
    tighter, change budget so a self-improvement storm is impossible."""
    max_mutations_per_day: int = 8
    max_tokens_per_day: int = 200_000
    offpeak_start_hour: int = 2
    offpeak_end_hour: int = 6

    _day: str = ""
    _mutations_today: int = 0
    _tokens_today: int = 0

    def _roll(self, now: float) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if day != self._day:
            self._day = day
            self._mutations_today = 0
            self._tokens_today = 0

    def in_window(self, now: float) -> bool:
        hour = time.gmtime(now).tm_hour
        s, e = self.offpeak_start_hour, self.offpeak_end_hour
        if s == e:
            return True  # degenerate window = always open
        if s < e:
            return s <= hour < e
        return hour >= s or hour < e  # wraps midnight

    def can_spend(self, now: float, tokens: int = 0) -> tuple[bool, str]:
        """(allowed, reason). Checks the off-peak window first, then the daily
        mutation and token ceilings, without consuming anything."""
        self._roll(now)
        if not self.in_window(now):
            return False, "outside the RSI off-peak execution window"
        if self._mutations_today >= self.max_mutations_per_day:
            return False, f"daily mutation cap reached ({self.max_mutations_per_day})"
        if tokens and self._tokens_today + int(tokens) > self.max_tokens_per_day:
            return False, f"daily token cap would be exceeded ({self.max_tokens_per_day})"
        return True, ""

    def record_spend(self, now: float, tokens: int = 0) -> None:
        self._roll(now)
        self._mutations_today += 1
        self._tokens_today += int(tokens or 0)

    def snapshot(self) -> dict:
        return {"day": self._day, "mutations": self._mutations_today,
                "tokens": self._tokens_today}


# ── Anti-oscillation guard ───────────────────────────────────────────────────

@dataclass
class OscillationGuard:
    """Blocks a mutation that was previously rolled back, or that would close an
    A→B→A flip of the same target inside the sliding window.

    Pure and deterministic (``now`` passed in), mirroring loop_detect's
    discipline. The blacklist is the durable part (a rolled-back hash is locked
    for good until an operator clears it); the per-target history is the sliding
    A→B→A detector."""
    window_s: float = 86_400.0  # 24h: self-improvement oscillates slowly
    _blacklist: set = field(default_factory=set)
    # normalized target -> deque of (ts, value_hash) recent applications
    _history: dict = field(default_factory=dict)
    _max_history_per_target: int = 16

    def blacklist(self, mutation_hash_: str) -> None:
        """Permanently lock a mutation hash (called on rollback in Phase 3)."""
        if mutation_hash_:
            self._blacklist.add(mutation_hash_)

    def is_blacklisted(self, mutation_hash_: str) -> bool:
        return mutation_hash_ in self._blacklist

    def _prune(self, dq: Deque, now: float) -> None:
        cutoff = now - self.window_s
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def check(self, proposal: "MutationProposal", now: float) -> tuple[bool, str]:
        """(oscillating, reason). True ⇒ reject + lock. Does not record; call
        ``record`` on an application that actually proceeds."""
        if self.is_blacklisted(proposal.mutation_hash):
            return True, "mutation was previously rolled back and is locked"
        target = normalize_target(proposal.target_key_or_path)
        dq = self._history.get(target)
        if dq:
            self._prune(dq, now)
            # A→B→A: the value we're proposing now equals an earlier value for
            # this target, with a *different* value applied in between.
            vhash = mutation_hash(target, proposed_value_of(proposal))
            seen_values = [v for _, v in dq]
            if vhash in seen_values and seen_values and seen_values[-1] != vhash:
                return True, "A→B→A oscillation on this target within the window"
        return False, ""

    def record(self, proposal: "MutationProposal", now: float) -> None:
        """Note an application so a later flip back is caught as oscillation."""
        target = normalize_target(proposal.target_key_or_path)
        dq = self._history.setdefault(target, deque())
        dq.append((now, mutation_hash(target, proposed_value_of(proposal))))
        self._prune(dq, now)
        overflow = len(dq) - self._max_history_per_target
        while overflow > 0:
            dq.popleft()
            overflow -= 1


def proposed_value_of(proposal: "MutationProposal") -> Any:
    """The value-side of a proposal for hashing (a Tier-3 diff hashes by its text)."""
    return proposal.proposed_value


# ── Persistence (durable proposals + blacklist) ──────────────────────────────

_MIGRATIONS = [
    lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS rsi_proposals ("
        " proposal_id TEXT PRIMARY KEY,"
        " ts REAL NOT NULL,"
        " tier INTEGER NOT NULL,"
        " target TEXT NOT NULL,"
        " mutation_hash TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " reject_reason TEXT,"
        " payload TEXT NOT NULL DEFAULT '{}')"
    ),
    lambda conn: conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsi_status ON rsi_proposals (status)"),
    lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS rsi_blacklist ("
        " mutation_hash TEXT PRIMARY KEY,"
        " ts REAL NOT NULL,"
        " reason TEXT)"
    ),
]


class RSIStore:
    """Durable sink for RSI proposals and the rolled-back-mutation blacklist,
    through the canonical persistence seam. Its own database file; blocking I/O
    (call off the event loop), mirroring EventLedger."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if not self._schema_ready:
            persistence.run_migrations(self._db_path, _MIGRATIONS)
            self._schema_ready = True

    def save_proposal(self, p: "MutationProposal") -> None:
        self._ensure_schema()
        with persistence.transaction(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rsi_proposals "
                "(proposal_id, ts, tier, target, mutation_hash, status, reject_reason, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p.proposal_id, p.created_ts, p.tier,
                 normalize_target(p.target_key_or_path), p.mutation_hash,
                 p.status, p.reject_reason,
                 json.dumps(p.as_dict(), default=str, sort_keys=True)),
            )

    def blacklist(self, mutation_hash_: str, now: float, reason: str = "") -> None:
        self._ensure_schema()
        with persistence.transaction(self._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO rsi_blacklist (mutation_hash, ts, reason) "
                "VALUES (?, ?, ?)", (mutation_hash_, now, reason))

    def load_blacklist(self) -> set:
        try:
            self._ensure_schema()
            conn = persistence.connect(self._db_path)
            try:
                rows = conn.execute("SELECT mutation_hash FROM rsi_blacklist").fetchall()
            finally:
                conn.close()
            return {r[0] for r in rows}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("rsi store: blacklist load failed: %s", exc)
            return set()


# ── Coordinator (Phase 1 surface: propose + guardrails) ──────────────────────

class RSICoordinator:
    """The RSI state machine. Phase 1 implements the Observe→Propose entry and
    the three hard guardrails; Evaluate/Act/Stabilize land in later phases.

    ``propose`` is the single choke point: every candidate mutation is checked
    against the immutable denylist, the anti-oscillation guard and the budget
    ceiling *before* it can advance. It mutates nothing — it returns the proposal
    stamped with its status and, on refusal, the reason."""

    def __init__(
        self,
        *,
        store: Optional[RSIStore] = None,
        budget: Optional[RSIBudget] = None,
        oscillation: Optional[OscillationGuard] = None,
    ) -> None:
        self._store = store
        self._budget = budget or RSIBudget()
        self._oscillation = oscillation or OscillationGuard()
        if store is not None:
            try:
                for h in store.load_blacklist():
                    self._oscillation.blacklist(h)
            except Exception:  # noqa: BLE001
                pass

    @property
    def budget(self) -> RSIBudget:
        return self._budget

    @property
    def oscillation(self) -> OscillationGuard:
        return self._oscillation

    def propose(
        self,
        *,
        proposal_id: str,
        tier: int,
        target_subsystem: str,
        target_key_or_path: str,
        current_value: Any,
        proposed_value: Any,
        hypothesis: str = "",
        evidence_refs: Optional[list] = None,
        now: Optional[float] = None,
        est_tokens: int = 0,
    ) -> MutationProposal:
        """Build and guard a proposal. Returns it stamped PROPOSED (cleared all
        guardrails, ready for Phase-2 evaluation), REJECTED, or LOCKED."""
        now = time.time() if now is None else now
        if tier not in _VALID_TIERS:
            raise ValueError(f"invalid RSI tier: {tier!r}")
        p = MutationProposal(
            proposal_id=proposal_id,
            tier=int(tier),
            target_subsystem=target_subsystem,
            target_key_or_path=target_key_or_path,
            current_value=current_value,
            proposed_value=proposed_value,
            hypothesis=hypothesis,
            evidence_refs=list(evidence_refs or []),
            created_ts=now,
        )

        # Guardrail 1 — immutable governance core. A hard security violation:
        # reject, LOCK, and persist the attempt for the ledger/audit trail.
        if is_immutable(target_key_or_path):
            p.status = LOCKED
            p.reject_reason = (
                f"immutable governance target may never be mutated: "
                f"{normalize_target(target_key_or_path)}")
            _LOGGER.warning("RSI SECURITY: blocked mutation of immutable target %s (proposal %s)",
                            normalize_target(target_key_or_path), proposal_id)
            self._persist(p)
            return p

        # Guardrail 2 — anti-oscillation (blacklist / A→B→A). Reject + lock.
        oscillating, why = self._oscillation.check(p, now)
        if oscillating:
            p.status = LOCKED
            p.reject_reason = why
            self._oscillation.blacklist(p.mutation_hash)
            if self._store is not None:
                try:
                    self._store.blacklist(p.mutation_hash, now, why)
                except Exception:  # noqa: BLE001
                    pass
            self._persist(p)
            return p

        # Guardrail 3 — budget ceiling (off-peak window + daily caps).
        allowed, why = self._budget.can_spend(now, est_tokens)
        if not allowed:
            p.status = REJECTED
            p.reject_reason = why
            self._persist(p)
            return p

        p.status = PROPOSED
        self._persist(p)
        return p

    # ── Phase 2: Tier-1 parametric evaluate → act ────────────────────────────
    def promote_parametric(
        self,
        proposal: "MutationProposal",
        records,
        *,
        applier,
        weights: "Optional[FitnessWeights]" = None,
        now: Optional[float] = None,
    ):
        """Shadow-evaluate a guardrail-cleared Tier-1 proposal and, only if its
        fitness clears the bar with no safety regression, apply it atomically and
        open probation. Returns ``(ReplayEvaluation, ProbationRecord | None)`` —
        nothing is applied on a failed evaluation."""
        now = time.time() if now is None else now
        if proposal.tier != TIER_PARAMETRIC:
            raise ValueError("promote_parametric handles Tier 1 (parametric) only")
        if proposal.status != PROPOSED:
            return (ReplayEvaluation(proposal.proposal_id, passed=False,
                                     detail=f"proposal has not cleared the guardrails ({proposal.status})"),
                    None)
        ev = evaluate_parametric(proposal, records, weights=weights, now=now)
        if not ev.passed:
            proposal.status = REJECTED
            proposal.reject_reason = ev.detail
            self._persist(proposal)
            return (ev, None)
        pr = apply_tier1(proposal, ev, applier=applier, now=now, store=self._store)
        # consume the change budget + note the application so a later flip back is
        # caught by the anti-oscillation guard.
        self._budget.record_spend(now)
        self._oscillation.record(proposal, now)
        self._persist(proposal)
        return (ev, pr)

    # ── Phase 3: probation watchdog ──────────────────────────────────────────
    def watch_probation(
        self,
        proposal: "MutationProposal",
        probation: "ProbationRecord",
        *,
        applier,
        now: Optional[float] = None,
        **signals,
    ) -> "ProbationRecord":
        """One probation tick: decide hold / rollback / graduate from the live
        signals and act. Auto-rolls-back (restoring the checkpoint and blacklisting
        the mutation) on a boot failure, fault-rate spike, or override spike;
        graduates (CERTIFIED) once the window elapses cleanly; otherwise holds."""
        now = time.time() if now is None else now
        verdict = evaluate_probation(probation, now=now, **signals)
        if verdict.action == PROBATION_ROLLBACK:
            return rollback_tier1(proposal, probation, applier=applier, now=now,
                                  reason=verdict.reason, oscillation=self._oscillation,
                                  store=self._store)
        if verdict.action == PROBATION_GRADUATE:
            return graduate(proposal, probation, now=now, store=self._store)
        return probation

    def _persist(self, p: MutationProposal) -> None:
        if self._store is None:
            return
        try:
            self._store.save_proposal(p)
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            _LOGGER.debug("rsi: proposal persist failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Tier-1 parametric loop (observe → evaluate → act)
# ─────────────────────────────────────────────────────────────────────────────
#
# Still bounded: only Tier-1 PARAMETRIC mutations (numeric thresholds) are
# evaluated and applied here, and only after the Phase-1 guardrails. The shadow
# evaluation is deterministic (replay over recorded, judged decisions) and applies
# NOTHING unless the fitness delta clears the noise floor with zero safety
# regression. The probation watchdog + auto-rollback that closes the loop is
# Phase 3; apply_tier1 opens probation here but does not yet watch it.

_OUTCOME_WRONG = "wrong"   # mirrors replay.WRONG; kept local so rsi stays HA-free

# Subsystems whose regressions are safety-relevant: a candidate may never act on
# MORE wrong cases (or lose MORE good calls) in one of these, whatever its ΔF.
SAFETY_CRITICAL_SUBSYSTEMS = frozenset({
    "intrusion", "hazard", "hazard_monitor", "sentinel", "lockdown", "security",
})


@dataclass(frozen=True)
class FitnessWeights:
    """Weights for ΔF = w1·ΔA − w2·ΔL − w3·ΔC − w4·ΔO, plus the promotion gates:
    a strict improvement floor and a minimum judged-sample count."""
    w_outcome: float = 1.0
    w_latency: float = 0.25
    w_tokens: float = 0.25
    w_overrides: float = 0.5
    delta_f_min: float = 0.01      # noise floor — ΔF must beat this to promote
    min_samples: int = 25


@dataclass(frozen=True)
class SubsystemAnomaly:
    """One subsystem whose recent decisions drifted enough to warrant a cycle."""
    subsystem: str
    score: float
    samples: int
    wrong: int
    overrides: int


def observe(
    signals,
    *,
    now: Optional[float] = None,
    drift_threshold: float = 0.15,
    cooldown_s: float = 3600.0,
    last_cycle_ts: float = 0.0,
) -> list:
    """Aggregate recent decision signals into per-subsystem anomalies, gated by a
    cooldown. Pure: ``signals`` is a list of dicts (the live wiring feeds it from
    decision_record / outcome / feedback) carrying some of {subsystem|kind,
    outcome, success, verified, overridden}. Returns the subsystems whose
    wrong+override rate meets ``drift_threshold``, worst first — or [] while the
    cooldown since ``last_cycle_ts`` has not elapsed."""
    now = time.time() if now is None else now
    if last_cycle_ts and (now - last_cycle_ts) < cooldown_s:
        return []
    by_sub: dict = {}
    for s in signals or []:
        s = s or {}
        sub = str(s.get("subsystem") or s.get("kind") or "").strip().lower()
        if not sub:
            continue
        agg = by_sub.setdefault(sub, {"n": 0, "wrong": 0, "ovr": 0})
        agg["n"] += 1
        if s.get("outcome") == _OUTCOME_WRONG or s.get("success") is False or s.get("verified") is False:
            agg["wrong"] += 1
        if s.get("overridden") or s.get("override"):
            agg["ovr"] += 1
    out = []
    for sub, a in by_sub.items():
        if a["n"] <= 0:
            continue
        score = (a["wrong"] + a["ovr"]) / a["n"]
        if score >= drift_threshold:
            out.append(SubsystemAnomaly(sub, round(score, 4), a["n"], a["wrong"], a["ovr"]))
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def recommend_parametric(records, *, min_samples: int = 25) -> Optional[dict]:
    """Best replay-recommended threshold over judged records (or None if there
    aren't enough samples) — the bridge used to pick a candidate value for a
    threshold-type parametric mutation."""
    try:
        from .. import replay
    except Exception:  # noqa: BLE001
        return None
    return replay.recommend_threshold(records, min_samples=min_samples)


def evaluate_parametric(
    proposal: "MutationProposal",
    records,
    *,
    weights: Optional[FitnessWeights] = None,
    now: Optional[float] = None,
) -> "ReplayEvaluation":
    """Shadow-replay a Tier-1 threshold mutation: baseline(current) vs
    candidate(proposed) over judged decision records, scored into a ΔF. Passes
    only on a strict improvement above the noise floor, with enough samples and no
    regression in a safety-critical subsystem. Deterministic; no side effects."""
    weights = weights or FitnessWeights()
    try:
        cur = float(proposal.current_value)
        cand = float(proposal.proposed_value)
    except (TypeError, ValueError):
        return ReplayEvaluation(proposal.proposal_id, passed=False,
                                detail="non-numeric parametric value; not replay-evaluable")
    try:
        from .. import replay
    except Exception as exc:  # noqa: BLE001
        return ReplayEvaluation(proposal.proposal_id, passed=False,
                                detail=f"replay unavailable: {exc}")
    base = replay.evaluate_threshold(records, cur)
    cand_r = replay.evaluate_threshold(records, cand)
    if base.total < max(1, weights.min_samples):
        return ReplayEvaluation(proposal.proposal_id, passed=False,
                                detail=f"insufficient judged samples ({base.total} < {weights.min_samples})")
    d_outcome = cand_r.accuracy - base.accuracy
    # threshold replay carries no per-threshold latency/token delta; those terms
    # are supplied by the live wiring for non-threshold mutation types later.
    d_latency = 0.0
    d_tokens = 0.0
    d_overrides = ((cand_r.good_calls_lost - base.good_calls_lost) / base.total) if base.total else 0.0
    delta_f = (weights.w_outcome * d_outcome
               - weights.w_latency * d_latency
               - weights.w_tokens * d_tokens
               - weights.w_overrides * d_overrides)
    sub = str(proposal.target_subsystem or "").strip().lower()
    safety_regression = (
        sub in SAFETY_CRITICAL_SUBSYSTEMS
        and (cand_r.kept_wrong > base.kept_wrong or cand_r.good_calls_lost > base.good_calls_lost)
    )
    passed = (delta_f >= weights.delta_f_min) and not safety_regression
    if safety_regression:
        detail = f"safety regression in critical subsystem '{sub}' — candidate acts on more wrong cases"
    elif not passed:
        detail = f"ΔF {delta_f:.4f} below floor {weights.delta_f_min}"
    else:
        detail = f"ΔF {delta_f:.4f} over {base.total} judged samples"
    return ReplayEvaluation(
        proposal_id=proposal.proposal_id,
        delta_fitness=round(delta_f, 6),
        delta_outcome=round(d_outcome, 6),
        delta_latency=d_latency,
        delta_tokens=d_tokens,
        delta_overrides=round(d_overrides, 6),
        safety_violations=1 if safety_regression else 0,
        passed=passed,
        detail=detail,
    )


def apply_tier1(
    proposal: "MutationProposal",
    evaluation: "ReplayEvaluation",
    *,
    applier,
    now: Optional[float] = None,
    provenance_sink=None,
    ledger_sink=None,
    store: Optional["RSIStore"] = None,
) -> "ProbationRecord":
    """Atomically apply a passed Tier-1 parametric mutation and open probation.

    Order: snapshot the prior value (the rollback checkpoint) → stamp provenance →
    apply via the injected ``applier`` (which raises on failure, so nothing is
    half-applied) → mark PROMOTED → record lineage. The probation window is opened
    but not yet watched; Phase 3 adds the watchdog + auto-rollback."""
    now = time.time() if now is None else now
    checkpoint = proposal.current_value
    prov = None
    try:
        from .provenance import record as _prov_record
        prov = _prov_record(
            proposal.proposed_value,
            source=f"rsi:{proposal.proposal_id}",
            confidence=max(0.0, min(1.0, float(evaluation.delta_outcome))),
            model="rsi.tier1",
            now=(lambda: now),
        )
    except Exception:  # noqa: BLE001 — provenance is best-effort metadata
        prov = None

    # The actuating step. A failure here raises out — the override is not applied
    # and no probation record is returned, so the engine never half-mutates.
    applier(proposal.target_key_or_path, proposal.proposed_value)

    proposal.status = PROMOTED
    pr = ProbationRecord(
        proposal_id=proposal.proposal_id,
        mutation_hash=proposal.mutation_hash,
        applied_ts=now,
        window_s=24 * 3600.0,
        checkpoint_ref=json.dumps(checkpoint, default=str),
        status=PROBATION,
    )
    if store is not None:
        try:
            store.save_proposal(proposal)
        except Exception:  # noqa: BLE001
            pass
    if ledger_sink is not None:
        try:
            ledger_sink({
                "event": "rsi_tier1_apply",
                "ts": now,
                "proposal": proposal.as_dict(),
                "delta_fitness": evaluation.delta_fitness,
                "checkpoint": checkpoint,
            })
        except Exception:  # noqa: BLE001
            pass
    if provenance_sink is not None and prov is not None:
        try:
            provenance_sink(prov)
        except Exception:  # noqa: BLE001
            pass
    return pr


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Probation watchdog & auto-rollback (the loop's safety net)
# ─────────────────────────────────────────────────────────────────────────────
#
# An applied Tier-1 mutation is on probation until it proves harmless. This is the
# watchdog that ends probation one of two ways: auto-rollback — restore the prior
# value and blacklist the mutation so it can't return — the moment a regression
# shows (a boot failure, a fault-rate spike, or a user-override spike), or
# graduation to CERTIFIED once the window closes with none of those. Pure decision
# logic; the restore goes back through the same injected applier that applied it.

PROBATION_HOLD = "hold"
PROBATION_ROLLBACK = "rollback"
PROBATION_GRADUATE = "graduate"

_FAULT_SPIKE_FACTOR = 1.5      # fault rate above baseline×this ⇒ regression
_OVERRIDE_SPIKE = 0.4          # override rate during probation ⇒ unwelcome


@dataclass(frozen=True)
class ProbationVerdict:
    action: str
    reason: str = ""


def evaluate_probation(
    probation: "ProbationRecord",
    *,
    now: float,
    fault_rate: float = 0.0,
    baseline_fault_rate: float = 0.0,
    boot_ok: bool = True,
    override_rate: float = 0.0,
    fault_spike_factor: float = _FAULT_SPIKE_FACTOR,
    override_spike: float = _OVERRIDE_SPIKE,
) -> ProbationVerdict:
    """Decide hold / rollback / graduate for one probation record. Rollback wins
    over graduation: any regression ends probation immediately, whatever the
    clock says. Pure — every signal is passed in."""
    if not boot_ok:
        return ProbationVerdict(PROBATION_ROLLBACK, "boot failure during probation")
    threshold = max(baseline_fault_rate * fault_spike_factor, baseline_fault_rate + 1e-9)
    if fault_rate > 0 and fault_rate > threshold:
        return ProbationVerdict(
            PROBATION_ROLLBACK,
            f"fault rate spiked to {fault_rate:.3f} (baseline {baseline_fault_rate:.3f})")
    if override_rate >= override_spike:
        return ProbationVerdict(
            PROBATION_ROLLBACK,
            f"user override spike ({override_rate:.2f} ≥ {override_spike})")
    if (now - probation.applied_ts) >= probation.window_s:
        return ProbationVerdict(PROBATION_GRADUATE, "probation window elapsed with no regression")
    return ProbationVerdict(PROBATION_HOLD, "within probation window, no regression")


def rollback_tier1(
    proposal: "MutationProposal",
    probation: "ProbationRecord",
    *,
    applier,
    now: Optional[float] = None,
    reason: str = "",
    oscillation: "Optional[OscillationGuard]" = None,
    store: "Optional[RSIStore]" = None,
    ledger_sink=None,
) -> "ProbationRecord":
    """Restore a probationary mutation's prior value and lock it out. Restores the
    checkpoint through the injected ``applier`` (which raises on failure — a
    mutation we could not revert is a loud error, never swallowed), blacklists the
    mutation hash so it can't be re-proposed, and marks both records ROLLED_BACK."""
    now = time.time() if now is None else now
    try:
        checkpoint_val = (json.loads(probation.checkpoint_ref)
                          if probation.checkpoint_ref else proposal.current_value)
    except Exception:  # noqa: BLE001
        checkpoint_val = proposal.current_value

    # Restore first. A failure raises out — an un-revertable mutation is loud.
    applier(proposal.target_key_or_path, checkpoint_val)

    proposal.status = ROLLED_BACK
    proposal.reject_reason = reason or "probation rollback"
    probation.status = ROLLED_BACK
    if oscillation is not None:
        oscillation.blacklist(proposal.mutation_hash)
    if store is not None:
        try:
            store.blacklist(proposal.mutation_hash, now, reason or "probation rollback")
            store.save_proposal(proposal)
        except Exception:  # noqa: BLE001
            pass
    if ledger_sink is not None:
        try:
            ledger_sink({"event": "rsi_rollback", "ts": now,
                         "proposal_id": proposal.proposal_id,
                         "restored": checkpoint_val, "reason": reason})
        except Exception:  # noqa: BLE001
            pass
    return probation


def graduate(
    proposal: "MutationProposal",
    probation: "ProbationRecord",
    *,
    now: Optional[float] = None,
    store: "Optional[RSIStore]" = None,
    autonomy_sink=None,
) -> "ProbationRecord":
    """Mark a mutation that survived its probation window CERTIFIED. The optional
    ``autonomy_sink`` is the hook the live wiring uses to nudge earned-trust
    metrics; it never gates the certification."""
    now = time.time() if now is None else now
    proposal.status = CERTIFIED
    probation.status = CERTIFIED
    if store is not None:
        try:
            store.save_proposal(proposal)
        except Exception:  # noqa: BLE001
            pass
    if autonomy_sink is not None:
        try:
            autonomy_sink(proposal)
        except Exception:  # noqa: BLE001
            pass
    return probation
