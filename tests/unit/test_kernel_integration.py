"""Phase R — the closed cognitive/agency loop trace (pure kernel primitive).

kernel.integration.LoopTrace records one perceive→predict→decide→act→learn pass
under a single correlation id. `is_closed` is True only when all five stages ran;
`reached`/`depth` describe a partial pass. Pure and deterministic.
"""
import pytest


@pytest.fixture
def II(load):
    return load("kernel.integration")


def test_stages_are_canonical_order(II):
    assert II.STAGES == (II.PERCEIVE, II.PREDICT, II.DECIDE, II.ACT, II.LEARN)


def test_closed_loop_all_present(II):
    tr = II.from_flags("c1", perceive=True, predict=True, decide=True, act=True, learn=True)
    assert tr.is_closed is True
    assert tr.present_stages == II.STAGES
    assert tr.missing_stages == ()
    assert tr.reached == II.LEARN
    assert tr.depth == 5


def test_open_loop_partial(II):
    # perceive + decide, nothing else — the common "noticed, decided nothing to do"
    tr = II.from_flags("c2", perceive=True, decide=True)
    assert tr.is_closed is False
    assert set(tr.present_stages) == {II.PERCEIVE, II.DECIDE}
    # reached stops at the first gap (PREDICT missing after PERCEIVE)
    assert tr.reached == II.PERCEIVE
    assert tr.depth == 1


def test_reached_follows_the_leading_run(II):
    tr = II.from_flags("c3", perceive=True, predict=True, decide=True)
    assert tr.reached == II.DECIDE
    assert tr.depth == 3
    assert tr.is_closed is False


def test_empty_pass_reached_is_blank(II):
    tr = II.from_flags("c4")
    assert tr.reached == ""
    assert tr.depth == 0
    assert tr.is_closed is False


def test_closed_loop_scenario_is_journal_reconstructable(II):
    # Phase R enforce proof: a full proactive pass (perceive the world, predict an
    # anticipation, decide an offer, act autonomously, learn from the outcome)
    # closes the loop AND is reconstructable from its correlation id.
    tr = II.from_flags(
        "tick-1801", started_ts=1000.0,
        perceive=True, predict=True, decide=True, act=True, learn=True,
        summaries={II.PERCEIVE: "2 home", II.ACT: "porch light on"})
    assert tr.is_closed is True
    d = tr.to_dict()
    assert d["correlation_id"] == "tick-1801"
    assert d["is_closed"] is True
    assert d["present"] == list(II.STAGES)         # every stage recorded, in order
    assert d["missing"] == []
    # each stage carries its owning primitive, so the pass is reconstructable
    owners = {s["stage"]: s["owner"] for s in d["stages"]}
    assert owners[II.PERCEIVE] == "world_model" and owners[II.LEARN] == "outcome"


def test_correlation_id_threads_through(II):
    tr = II.from_flags("corr-xyz", perceive=True)
    assert tr.correlation_id == "corr-xyz"
    assert tr.to_dict()["correlation_id"] == "corr-xyz"
    assert "corr-xyz" in tr.summary()


def test_default_owners_are_the_canonical_primitives(II):
    tr = II.from_flags("c5", perceive=True, act=True)
    by = {s["stage"]: s for s in tr.to_dict()["stages"]}
    assert by[II.PERCEIVE]["owner"] == "world_model"
    assert by[II.ACT]["owner"] == "actuation"


def test_summaries_and_owners_override(II):
    tr = II.from_flags(
        "c6", perceive=True,
        summaries={II.PERCEIVE: "2 home"}, owners={II.PERCEIVE: "world_model"})
    by = {s.stage: s for s in tr.stages}
    assert by[II.PERCEIVE].summary == "2 home"


def test_summary_marks_present_and_missing(II):
    tr = II.from_flags("c7", perceive=True, predict=True)
    s = tr.summary()
    # mark string is one char per stage: + present, - absent
    assert s.startswith("[++---]")
    assert "open@" in s


def test_trace_is_order_independent_and_canonical(II):
    # feeding stages out of order still renders canonical order in to_dict
    tr = II.trace("c8", [
        II.stage(II.LEARN, True), II.stage(II.PERCEIVE, True),
        II.stage(II.ACT, True),
    ])
    stages = [s["stage"] for s in tr.to_dict()["stages"]]
    assert stages == [II.PERCEIVE, II.ACT, II.LEARN]


def test_trace_is_defensive_on_junk(II):
    tr = II.trace("c9", [None, 123, II.stage(II.PERCEIVE, True), ("decide", True)])
    assert set(tr.present_stages) == {II.PERCEIVE, II.DECIDE}


# ── LoopAccumulator (Phase R parity) ─────────────────────────────────────────
def test_accumulator_empty(II):
    acc = II.LoopAccumulator()
    assert acc.total == 0 and acc.closed == 0 and acc.rate == 0.0
    assert acc.summary() == "no passes recorded"


def test_accumulator_rate_and_histogram(II):
    acc = II.LoopAccumulator()
    acc.record(II.from_flags("a", perceive=True, predict=True, decide=True,
                             act=True, learn=True))        # closed
    acc.record(II.from_flags("b", perceive=True, decide=True))  # open@perceive
    acc.record(II.from_flags("c", perceive=True, predict=True))  # open@predict
    assert acc.total == 3 and acc.closed == 1
    assert abs(acc.rate - (1 / 3)) < 1e-6
    hist = acc.reached_histogram()
    assert hist["learn"] == 1 and hist["perceive"] == 1 and hist["predict"] == 1
    assert "closed 1/3 (33%)" in acc.summary()


def test_accumulator_counts_none_for_empty_pass(II):
    acc = II.LoopAccumulator()
    acc.record(II.from_flags("x"))   # nothing ran
    assert acc.reached_histogram() == {"none": 1}
    assert acc.rate == 0.0


def test_accumulator_histogram_is_canonical_order(II):
    acc = II.LoopAccumulator()
    acc.record(II.from_flags("a", perceive=True, predict=True, decide=True,
                             act=True, learn=True))
    acc.record(II.from_flags("b", perceive=True))
    acc.record(II.from_flags("c"))
    # none first, then canonical stage order
    assert list(acc.reached_histogram().keys()) == ["none", "perceive", "learn"]


def test_accumulator_ignores_junk_and_resets(II):
    acc = II.LoopAccumulator()
    acc.record(None)
    acc.record("not a trace")
    assert acc.total == 0
    acc.record(II.from_flags("a", perceive=True))
    assert acc.total == 1
    acc.reset()
    assert acc.total == 0 and acc.reached_histogram() == {}
