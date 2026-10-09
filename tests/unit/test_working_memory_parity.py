"""Phase K parity: attention consults the shared kernel working set (output_gate).

The owner chose a shared kernel-level WorkingMemory as the canonical cognitive
context. ``output_gate._attention_working_memory_parity`` reads it and logs
whether consulting it (e.g. learning the household is asleep) would change the
arbitration vs the working-memory-blind baseline. Observe-only.

The helper is exercised directly (with a fresh GateState) rather than through
``can_announce``, so the test is isolated from environment-dependent gate state.
"""
import logging

import pytest

DEFAULTS = dict(category="x", urgency="normal", budget_multiplier=1.0,
                max_per_hour=6)


@pytest.fixture
def wm(load):
    return load("kernel.working_memory")


@pytest.fixture
def og(load, wm):
    mod = load("output_gate")
    mod._STATE = mod.GateState()        # fresh: no shush, no recent interruptions
    wm.reset_shared()
    yield mod
    wm.reset_shared()


def _asleep(wm):
    wm.shared().remember(wm.KIND_SITUATION, "home_occupancy",
                         content="empty, household asleep", salience=0.8, now=0.0)


def _parity_line(caplog):
    return [r.message for r in caplog.records
            if "attention+working_memory(parity)" in r.message]


def test_logs_would_change_when_asleep_and_normal(og, wm, caplog):
    # Asleep in the working set; a NORMAL request the blind baseline would ALLOW
    # becomes DEFER once quiet-hours is derived from the canonical context.
    _asleep(wm)
    with caplog.at_level(logging.DEBUG):
        og._attention_working_memory_parity(**DEFAULTS)
    line = _parity_line(caplog)
    assert line and "asleep=True" in line[-1] and "would_change=True" in line[-1]


def test_high_priority_overrides_quiet_no_change(og, wm, caplog):
    # HIGH overrides quiet hours, so consulting the asleep situation changes
    # nothing — would_change=False.
    _asleep(wm)
    with caplog.at_level(logging.DEBUG):
        og._attention_working_memory_parity(**{**DEFAULTS, "urgency": "high"})
    line = _parity_line(caplog)
    assert line and "asleep=True" in line[-1] and "would_change=False" in line[-1]


def test_awake_situation_no_change(og, wm, caplog):
    wm.shared().remember(wm.KIND_SITUATION, "home_occupancy",
                         content="occupied", salience=0.8, now=0.0)
    with caplog.at_level(logging.DEBUG):
        og._attention_working_memory_parity(**DEFAULTS)
    line = _parity_line(caplog)
    assert line and "asleep=False" in line[-1] and "would_change=False" in line[-1]


def test_no_situation_emits_nothing(og, wm, caplog):
    # Empty working set → nothing to consult, no parity line.
    with caplog.at_level(logging.DEBUG):
        og._attention_working_memory_parity(**DEFAULTS)
    assert not _parity_line(caplog)


def test_kill_switch(og, wm, caplog):
    _asleep(wm)
    og.WORKING_MEMORY_PARITY = False
    try:
        with caplog.at_level(logging.DEBUG):
            og._attention_working_memory_parity(**DEFAULTS)
        assert not _parity_line(caplog)
    finally:
        og.WORKING_MEMORY_PARITY = True


def test_never_raises_on_bad_working_set(og, wm, monkeypatch):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(wm, "shared", _boom)
    # Must swallow the error — no exception escapes the helper.
    og._attention_working_memory_parity(**DEFAULTS)


def test_helper_returns_none_and_has_no_side_effects(og, wm):
    # The parity read is observe-only: it returns None and leaves gate state
    # untouched (the can_announce path is covered by test_attention_shadow).
    _asleep(wm)
    before = (len(og._STATE.history), len(og._STATE.reservations), og._STATE.mute_all)
    result = og._attention_working_memory_parity(**DEFAULTS)
    after = (len(og._STATE.history), len(og._STATE.reservations), og._STATE.mute_all)
    assert result is None and before == after
