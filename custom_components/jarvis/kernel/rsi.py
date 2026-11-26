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

    def _persist(self, p: MutationProposal) -> None:
        if self._store is None:
            return
        try:
            self._store.save_proposal(p)
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            _LOGGER.debug("rsi: proposal persist failed: %s", exc)
