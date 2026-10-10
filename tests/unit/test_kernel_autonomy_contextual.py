"""Phase AC — contextual autonomy (pure kernel primitive).

`AutonomyContext` + `contextual_cap` layer a dynamic, context-sensitive cap on
top of the earned per-capability level. The cap is DOWNWARD-ONLY: a restrictive
context (guests / asleep / low-confidence) can only ever lower autonomy toward
CONFIRM, never raise it, and never past the earned N-ceiling (which runs first).
"""
import pytest


@pytest.fixture
def A(load):
    return load("kernel.autonomy")


# ── AutonomyContext ──────────────────────────────────────────────────────────
def test_empty_context_is_not_restrictive(A):
    assert A.AutonomyContext().restrictive is False


@pytest.mark.parametrize("kwargs", [
    {"guests_present": True},
    {"asleep": True},
    {"low_confidence": True},
    {"guests_present": True, "asleep": True},
])
def test_any_flag_makes_it_restrictive(A, kwargs):
    assert A.AutonomyContext(**kwargs).restrictive is True


# ── contextual_cap: downward-only ────────────────────────────────────────────
def test_unrestricted_context_leaves_level_untouched(A):
    ctx = A.AutonomyContext()
    for level in (A.SUGGEST, A.CONFIRM, A.ACT):
        assert A.contextual_cap(level, ctx) == level


def test_restrictive_context_caps_act_at_confirm(A):
    ctx = A.AutonomyContext(guests_present=True)
    assert A.contextual_cap(A.ACT, ctx) == A.CONFIRM


def test_restrictive_context_never_raises_a_lower_level(A):
    ctx = A.AutonomyContext(guests_present=True)
    # already at/below the cap → unchanged (never bumped UP to CONFIRM)
    assert A.contextual_cap(A.SUGGEST, ctx) == A.SUGGEST
    assert A.contextual_cap(A.CONFIRM, ctx) == A.CONFIRM


def test_cap_is_min_on_the_ladder(A):
    # contextual_cap(level, ctx) == min(level, cap) on SUGGEST<CONFIRM<ACT
    restr = A.AutonomyContext(asleep=True)
    assert A.contextual_cap(A.ACT, restr) == A.CONFIRM
    assert A.contextual_cap(A.CONFIRM, restr) == A.CONFIRM
    assert A.contextual_cap(A.SUGGEST, restr) == A.SUGGEST


# ── context_blocks_autonomy: the boolean adapter ─────────────────────────────
def test_blocks_only_under_restrictive_context(A):
    assert A.context_blocks_autonomy(A.AutonomyContext(guests_present=True)) is True
    assert A.context_blocks_autonomy(A.AutonomyContext()) is False


def test_blocks_is_defensive_on_junk(A):
    # None / malformed context never blocks (fail-safe = act as today).
    assert A.context_blocks_autonomy(None) is False
    assert A.context_blocks_autonomy(object()) is False
