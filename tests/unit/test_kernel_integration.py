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
