"""Tests for the long-horizon agency primitive (roadmap Phase V).

Pure: a durable goal is an ordered set of milestones; progress / next / stalled /
complete are deterministic functions of the record plus the ``now`` passed in, and
``advance`` returns a NEW goal (the receiver is never mutated).
"""
import pytest


@pytest.fixture
def LH(load):
    return load("kernel.long_horizon")


def test_plan_goal_coerces_mixed_milestones_with_unique_ids(LH):
    g = LH.plan_goal("spring clean", [
        LH.milestone("declutter garage"),             # object
        {"label": "deep clean kitchen", "status": LH.ACTIVE},  # mapping
        "wash windows",                                # bare string
        {"status": LH.PENDING},                        # no label → dropped
        42,                                            # junk → dropped
        "declutter garage",                            # dup slug → id made unique
    ], id="goal-1", now=1000.0)
    labels = [m.label for m in g.milestones]
    assert labels == ["declutter garage", "deep clean kitchen", "wash windows",
                      "declutter garage"]
    ids = [m.id for m in g.milestones]
    assert len(ids) == len(set(ids))                   # ids unique within the goal
    assert g.id == "goal-1" and g.total == 4


def test_progress_counts_resolved_done_and_skipped(LH):
    g = LH.plan_goal("g", ["a", "b", "c", "d"], id="g", now=0.0)
    g = g.advance(g.milestones[0].id, LH.DONE, now=10.0)
    g = g.advance(g.milestones[1].id, LH.SKIPPED, now=20.0)   # resolved, not achieved
    assert g.resolved == 2 and g.achieved == 1
    assert abs(g.progress - 0.5) < 1e-9
    assert g.is_complete is False


def test_advance_is_pure_and_total(LH):
    g0 = LH.plan_goal("g", ["a", "b"], id="g", now=0.0)
    g1 = g0.advance(g0.milestones[0].id, LH.DONE, now=5.0)
    # original unchanged (pure)
    assert g0.milestones[0].status == LH.PENDING
    assert g1.milestones[0].status == LH.DONE and g1.milestones[0].updated_at == 5.0
    assert g1.updated_at == 5.0
    # unknown id and invalid status are no-ops
    assert g1.advance("nope", LH.DONE, now=9.0).to_dict() == g1.to_dict()
    assert g1.advance(g1.milestones[1].id, "bogus", now=9.0).to_dict() == g1.to_dict()


def test_next_milestone_prefers_active_then_first_unresolved(LH):
    g = LH.plan_goal("g", ["a", "b", "c"], id="g", now=0.0)
    assert g.next_milestone().label == "a"              # first unresolved
    g = g.advance(g.milestones[1].id, LH.ACTIVE, now=1.0)
    assert g.next_milestone().label == "b"              # active preferred
    g = (g.advance(g.milestones[0].id, LH.DONE, now=2.0)
          .advance(g.milestones[1].id, LH.DONE, now=3.0)
          .advance(g.milestones[2].id, LH.DONE, now=4.0))
    assert g.is_complete is True and g.next_milestone() is None


def test_is_stalled_only_when_incomplete_and_idle(LH):
    g = LH.plan_goal("g", ["a", "b"], id="g", now=1000.0)
    assert g.is_stalled(now=1000.0 + 500, max_idle=86400.0) is False   # recent
    assert g.is_stalled(now=1000.0 + 90000, max_idle=86400.0) is True  # idle
    done = (g.advance(g.milestones[0].id, LH.DONE, now=1001.0)
             .advance(g.milestones[1].id, LH.DONE, now=1002.0))
    assert done.is_stalled(now=1000.0 + 90000, max_idle=86400.0) is False  # complete


def test_empty_goal_is_safe(LH):
    g = LH.plan_goal("empty", [], id="g", now=0.0)
    assert g.total == 0 and g.progress == 0.0
    assert g.is_complete is False and g.next_milestone() is None
    assert g.is_stalled(now=1e9, max_idle=1.0) is True   # incomplete + idle


def test_summarize_rolls_up_and_skips_non_goals(LH):
    done = LH.plan_goal("d", ["a"], id="d", now=0.0).advance("a", LH.DONE, now=1.0)
    # half is incomplete but freshly touched (now=1000) → NOT stalled
    half = LH.plan_goal("h", ["a", "b"], id="h", now=0.0).advance("a", LH.DONE, now=1000.0)
    stalled = LH.plan_goal("s", ["a"], id="s", now=0.0)   # last touched t=0 → stalled
    st = LH.summarize([done, half, stalled, "not-a-goal"], now=1000.0, max_idle=10.0)
    assert st.count == 3 and st.complete == 1 and st.stalled == 1
    assert abs(st.avg_progress - ((1.0 + 0.5 + 0.0) / 3)) < 1e-9


def test_to_dict_surfaces_progress_and_next(LH):
    g = LH.plan_goal("g", ["a", "b"], id="g", now=0.0).advance("a", LH.DONE, now=1.0)
    d = g.to_dict()
    assert d["id"] == "g" and d["total"] == 2 and d["resolved"] == 1
    assert d["progress"] == 0.5 and d["next"]["label"] == "b"
