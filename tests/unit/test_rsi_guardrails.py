"""RSI engine — Phase 1 guardrails (kernel/rsi.py).

The three hard rails every proposed self-improvement must clear before anything
is ever applied: the immutable-governance denylist, the budget ceiling, and the
anti-oscillation lock — plus the target normalizer and the canonical mutation
hash they rely on. Pure, deterministic (``now`` passed in), no DB needed except
the one persistence round-trip test.
"""
import calendar

import pytest


@pytest.fixture
def rsi(load):
    return load("kernel/rsi")


# UTC epochs with a known hour: default off-peak window is [2,6) UTC.
IN_WINDOW = calendar.timegm((2026, 1, 1, 3, 0, 0, 0, 0, 0))    # 03:00 UTC
OUT_WINDOW = calendar.timegm((2026, 1, 1, 12, 0, 0, 0, 0, 0))  # 12:00 UTC


def _propose(rsi, coord, **over):
    kw = dict(
        proposal_id="p1", tier=rsi.TIER_PARAMETRIC,
        target_subsystem="observer", target_key_or_path="cognition_threshold",
        current_value=0.6, proposed_value=0.55, hypothesis="fewer false alerts",
        now=IN_WINDOW,
    )
    kw.update(over)
    return coord.propose(**kw)


# ── normalize_target ─────────────────────────────────────────────────────────

def test_normalize_collapses_spellings(rsi):
    n = rsi.normalize_target
    assert n("custom_components/jarvis/kernel/authority.py") == "custom_components/jarvis/kernel/authority.py"
    assert n("/home/user/jarvis-aio/custom_components/jarvis/kernel/authority.py") == "custom_components/jarvis/kernel/authority.py"
    assert n("./custom_components/jarvis/boot_guard.py") == "custom_components/jarvis/boot_guard.py"
    assert n("custom_components\\jarvis\\enforcement.py") == "custom_components/jarvis/enforcement.py"
    assert n("/abs/path/to/docs/JARVIS_CONSTITUTION.md") == "docs/JARVIS_CONSTITUTION.md"
    # a bare config key passes through
    assert n("cognition_threshold") == "cognition_threshold"
    assert n("") == ""


def test_is_immutable(rsi):
    assert rsi.is_immutable("custom_components/jarvis/kernel/authority.py")
    assert rsi.is_immutable("/x/custom_components/jarvis/kernel/rsi.py")
    assert rsi.is_immutable("docs/JARVIS_CONSTITUTION.md")
    assert not rsi.is_immutable("custom_components/jarvis/observer.py")
    assert not rsi.is_immutable("cognition_threshold")


def test_every_immutable_target_is_detected(rsi):
    for t in rsi.IMMUTABLE_TARGETS:
        assert rsi.is_immutable(t), t


# ── mutation_hash ────────────────────────────────────────────────────────────

def test_mutation_hash_is_deterministic_and_value_sensitive(rsi):
    h1 = rsi.mutation_hash("cognition_threshold", 0.55)
    h2 = rsi.mutation_hash("cognition_threshold", 0.55)
    h3 = rsi.mutation_hash("cognition_threshold", 0.60)
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # sha-256 hex


# ── Guardrail 1: immutable governance core ───────────────────────────────────

def test_immutable_target_is_locked(rsi):
    coord = rsi.RSICoordinator()
    for target in rsi.IMMUTABLE_TARGETS:
        p = _propose(rsi, coord, tier=rsi.TIER_STRUCTURAL,
                     target_key_or_path=target, proposed_value="--- diff ---")
        assert p.status == rsi.LOCKED, target
        assert "immutable" in p.reject_reason


def test_immutable_blocked_even_via_absolute_path(rsi):
    coord = rsi.RSICoordinator()
    p = _propose(rsi, coord, target_key_or_path="/srv/ha/custom_components/jarvis/enforcement.py",
                 proposed_value="x")
    assert p.status == rsi.LOCKED
    assert "immutable" in p.reject_reason


# ── Guardrail 2: anti-oscillation ────────────────────────────────────────────

def test_blacklisted_hash_is_locked(rsi):
    guard = rsi.OscillationGuard()
    coord = rsi.RSICoordinator(oscillation=guard)
    h = rsi.mutation_hash("cognition_threshold", 0.55)
    guard.blacklist(h)
    p = _propose(rsi, coord)   # same target+value => same hash
    assert p.status == rsi.LOCKED
    assert "rolled back" in p.reject_reason or "locked" in p.reject_reason


def test_oscillation_guard_detects_A_B_A(rsi):
    guard = rsi.OscillationGuard()

    class _P:
        target_key_or_path = "cognition_threshold"
        proposed_value = 0.55
        mutation_hash = rsi.mutation_hash("cognition_threshold", 0.55)

    class _PB:
        target_key_or_path = "cognition_threshold"
        proposed_value = 0.70
        mutation_hash = rsi.mutation_hash("cognition_threshold", 0.70)

    # apply A then B
    guard.record(_P, now=IN_WINDOW)
    guard.record(_PB, now=IN_WINDOW + 10)
    # proposing A again closes an A->B->A flip
    oscillating, why = guard.check(_P, now=IN_WINDOW + 20)
    assert oscillating is True
    assert "oscillation" in why


def test_coordinator_locks_A_B_A(rsi):
    guard = rsi.OscillationGuard()
    coord = rsi.RSICoordinator(oscillation=guard)

    class _PB:
        target_key_or_path = "cognition_threshold"
        proposed_value = 0.70
        mutation_hash = rsi.mutation_hash("cognition_threshold", 0.70)

    # history: A (0.55) then B (0.70) applied
    a = rsi.MutationProposal("a", rsi.TIER_PARAMETRIC, "observer",
                             "cognition_threshold", 0.6, 0.55)
    guard.record(a, now=IN_WINDOW)
    guard.record(_PB, now=IN_WINDOW + 10)
    # now propose A again
    p = _propose(rsi, coord, proposed_value=0.55, now=IN_WINDOW + 20)
    assert p.status == rsi.LOCKED
    assert "oscillation" in p.reject_reason


def test_fresh_target_not_oscillating(rsi):
    guard = rsi.OscillationGuard()
    p = rsi.MutationProposal("x", rsi.TIER_PARAMETRIC, "observer", "k", 1, 2)
    oscillating, _ = guard.check(p, now=IN_WINDOW)
    assert oscillating is False


# ── Guardrail 3: budget ceiling ──────────────────────────────────────────────

def test_budget_rejects_outside_offpeak_window(rsi):
    coord = rsi.RSICoordinator()
    p = _propose(rsi, coord, now=OUT_WINDOW)
    assert p.status == rsi.REJECTED
    assert "off-peak" in p.reject_reason or "window" in p.reject_reason


def test_budget_window_wraps_midnight(rsi):
    b = rsi.RSIBudget(offpeak_start_hour=22, offpeak_end_hour=4)
    assert b.in_window(calendar.timegm((2026, 1, 1, 23, 0, 0, 0, 0, 0))) is True
    assert b.in_window(calendar.timegm((2026, 1, 1, 2, 0, 0, 0, 0, 0))) is True
    assert b.in_window(calendar.timegm((2026, 1, 1, 12, 0, 0, 0, 0, 0))) is False


def test_budget_daily_mutation_cap(rsi):
    b = rsi.RSIBudget(max_mutations_per_day=2)
    assert b.can_spend(IN_WINDOW)[0] is True
    b.record_spend(IN_WINDOW)
    b.record_spend(IN_WINDOW)
    ok, why = b.can_spend(IN_WINDOW)
    assert ok is False and "mutation cap" in why


def test_budget_cap_rejects_in_coordinator(rsi):
    b = rsi.RSIBudget(max_mutations_per_day=1)
    b.record_spend(IN_WINDOW)          # budget already spent for the day
    coord = rsi.RSICoordinator(budget=b)
    p = _propose(rsi, coord)
    assert p.status == rsi.REJECTED
    assert "cap" in p.reject_reason


def test_budget_token_cap(rsi):
    b = rsi.RSIBudget(max_tokens_per_day=1000)
    assert b.can_spend(IN_WINDOW, tokens=500)[0] is True
    b.record_spend(IN_WINDOW, tokens=900)
    ok, why = b.can_spend(IN_WINDOW, tokens=500)
    assert ok is False and "token cap" in why


def test_budget_rolls_over_at_utc_midnight(rsi):
    b = rsi.RSIBudget(max_mutations_per_day=1)
    b.record_spend(IN_WINDOW)
    assert b.can_spend(IN_WINDOW)[0] is False
    next_day = IN_WINDOW + 86_400
    assert b.can_spend(next_day)[0] is True   # fresh day, fresh budget


# ── Happy path ───────────────────────────────────────────────────────────────

def test_clean_proposal_passes_all_guardrails(rsi):
    coord = rsi.RSICoordinator()
    p = _propose(rsi, coord)
    assert p.status == rsi.PROPOSED
    assert p.reject_reason == ""
    assert p.mutation_hash and len(p.mutation_hash) == 64


def test_invalid_tier_raises(rsi):
    coord = rsi.RSICoordinator()
    with pytest.raises(ValueError):
        _propose(rsi, coord, tier=9)


# ── Persistence round-trip ───────────────────────────────────────────────────

def test_store_persists_proposal_and_blacklist(rsi, tmp_path):
    db = str(tmp_path / "rsi.db")
    store = rsi.RSIStore(db)
    coord = rsi.RSICoordinator(store=store)
    p = _propose(rsi, coord)
    assert p.status == rsi.PROPOSED
    # blacklist survives a reload into a fresh coordinator
    store.blacklist(p.mutation_hash, IN_WINDOW, "test")
    coord2 = rsi.RSICoordinator(store=rsi.RSIStore(db))
    assert coord2.oscillation.is_blacklisted(p.mutation_hash)
    p2 = _propose(rsi, coord2)
    assert p2.status == rsi.LOCKED      # the reloaded blacklist locks it
