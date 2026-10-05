"""Tests for the unified cognitive cycle (roadmap Phase J, J1 — pure).

No Home Assistant, no DB: steps are injected callables over a shared context.
"""
import pytest


@pytest.fixture
def cyc(load):
    return load("kernel.cycle")


def test_tick_runs_steps_in_order_and_traces(cyc):
    seen = []
    steps = [
        cyc.CycleStep("perceive", lambda c: seen.append("p")),
        cyc.CycleStep("decide", lambda c: seen.append("d")),
        cyc.CycleStep("act", lambda c: seen.append("a")),
    ]
    trace = cyc.CognitiveCycle(steps, now=lambda: 1.0).tick()
    assert seen == ["p", "d", "a"]
    assert trace.names == ["perceive", "decide", "act"]
    assert trace.ok is True and trace.failed == []
    assert trace.cycle_id and trace.started_ts == 1.0


def test_context_is_shared_and_cycle_id_stamped(cyc):
    def p(c): c["x"] = 1
    def d(c): c["y"] = c["x"] + 1
    captured = {}
    def a(c): captured.update(c)
    cyc.CognitiveCycle([cyc.CycleStep("perceive", p), cyc.CycleStep("decide", d),
                        cyc.CycleStep("act", a)]).tick()
    assert captured["x"] == 1 and captured["y"] == 2
    assert "cycle_id" in captured


def test_given_cycle_id_is_preserved(cyc):
    trace = cyc.CognitiveCycle([cyc.CycleStep("perceive", lambda c: None)]).tick(
        {"cycle_id": "fixed123"})
    assert trace.cycle_id == "fixed123"


def test_failing_step_recorded_continue_by_default(cyc):
    def boom(c): raise ValueError("nope")
    ran_after = []
    steps = [
        cyc.CycleStep("perceive", lambda c: None),
        cyc.CycleStep("decide", boom),
        cyc.CycleStep("act", lambda c: ran_after.append(1)),
    ]
    trace = cyc.CognitiveCycle(steps).tick()
    assert ran_after == [1]                      # continued past the failure
    assert trace.ok is False
    assert [s.name for s in trace.failed] == ["decide"]
    assert "ValueError: nope" in trace.failed[0].detail


def test_on_error_stop_halts_the_pass(cyc):
    def boom(c): raise RuntimeError("halt")
    ran_after = []
    steps = [
        cyc.CycleStep("perceive", boom),
        cyc.CycleStep("decide", lambda c: ran_after.append(1)),
    ]
    trace = cyc.CognitiveCycle(steps, on_error=cyc.ON_ERROR_STOP).tick()
    assert ran_after == []                        # halted after the failure
    assert trace.names == ["perceive"]


def test_tick_never_raises(cyc):
    def boom(c): raise Exception("x")
    # Even an all-failing cycle returns a trace rather than raising.
    trace = cyc.CognitiveCycle([cyc.CycleStep("perceive", boom)]).tick()
    assert trace.ok is False


def test_standard_cycle_builds_canonical_order_skipping_absent(cyc):
    handlers = {
        "perceive": lambda c: None,
        "decide": lambda c: None,
        "act": lambda c: None,
        # interpret + reflect omitted
    }
    cycle = cyc.standard_cycle(handlers)
    assert cycle.step_names == ["perceive", "decide", "act"]


def test_standard_cycle_full_order(cyc):
    handlers = {p: (lambda c: None) for p in cyc.PHASES}
    assert cyc.standard_cycle(handlers).step_names == list(cyc.PHASES)


def test_trace_to_dict_round_trips_shape(cyc):
    trace = cyc.CognitiveCycle([cyc.CycleStep("perceive", lambda c: None)],
                               now=lambda: 5.0).tick()
    d = trace.to_dict()
    assert d["ok"] is True and d["steps"][0]["name"] == "perceive"
    assert d["cycle_id"] == trace.cycle_id
