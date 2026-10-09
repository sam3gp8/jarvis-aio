"""Phase J (J4) enforce — the proactive dispatch loop IS the kernel CognitiveCycle.

When the household opts in (`cognitive_cycle_enforce`), `_tick`'s proactive
dispatch plan is produced by the canonical PERCEIVE→INTERPRET→DECIDE→ACT→REFLECT
cycle's DECIDE phase instead of iterating `actions` directly — so one (non-safety)
subsystem's loop genuinely becomes the cycle. Default OFF is behaviour-preserving
(the plan is exactly `actions`, same order); fail-safe — any cycle failure raises
so the caller falls back to the legacy order, and dispatch runs exactly once over
the chosen plan (no double-fire).
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture(autouse=True)
def _restore_core_config(cc):
    """Keep the session-cached _CORE.config isolated from these tests."""
    saved = cc._CORE.config
    cc._CORE.config = {}
    yield
    cc._CORE.config = saved


# ── the kill-switch ───────────────────────────────────────────────────────────
def test_enforce_off_by_default(cc):
    assert cc._cognitive_cycle_enforce_on() is False


def test_enforce_on_via_flag(cc, monkeypatch):
    monkeypatch.setattr(cc, "COGNITIVE_CYCLE_ENFORCE", True)
    assert cc._cognitive_cycle_enforce_on() is True


def test_enforce_on_via_config(cc):
    cc._CORE.config = {"cognitive_cycle_enforce": True}
    assert cc._cognitive_cycle_enforce_on() is True


def test_enforce_never_raises_on_bad_core(cc):
    cc._CORE.config = None  # defensive: a half-initialised core must not throw
    assert cc._cognitive_cycle_enforce_on() is False


# ── the cycle-produced plan ─────────────────────────────────────────────────────
def test_plan_preserves_order_and_contents(cc):
    actions = [{"id": 1}, {"id": 2}, {"id": 3}]
    plan = cc._cognitive_cycle_plan(
        actions, people=2, anyone_home=True, sleeping=False)
    # behaviour-preserving: the cycle's DECIDE owns the plan, == actions in order.
    assert plan == actions
    assert [a["id"] for a in plan] == [1, 2, 3]


def test_plan_empty_actions(cc):
    assert cc._cognitive_cycle_plan(
        [], people=0, anyone_home=False, sleeping=True) == []


def test_plan_does_not_mutate_input(cc):
    actions = [{"id": "a"}]
    plan = cc._cognitive_cycle_plan(
        actions, people=1, anyone_home=True, sleeping=False)
    assert plan is not actions  # a fresh list, so the caller's list is untouched


def test_plan_raises_on_cycle_failure(cc, load, monkeypatch):
    """A failed cycle pass must raise so `_tick` falls back to the legacy order."""
    kcycle = load("kernel.cycle")

    def _boom(handlers, **kw):
        # a DECIDE that raises → trace.ok is False → _cognitive_cycle_plan raises
        def _raise(ctx):
            raise RuntimeError("decide blew up")
        return kcycle.standard_cycle({kcycle.DECIDE: _raise}, **kw)

    monkeypatch.setattr(kcycle, "standard_cycle", _boom)
    with pytest.raises(RuntimeError):
        cc._cognitive_cycle_plan(
            [{"id": 1}], people=1, anyone_home=True, sleeping=False)
