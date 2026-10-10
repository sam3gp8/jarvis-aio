"""Phase P (pure) — proactive household intelligence.

`kernel/household.py` derives an occupancy rhythm + a routine graph from
observations and emits **advisory** suggestions only — the invariant is that a
household suggestion carries no actuator (suggest, never act).
"""
import pytest


@pytest.fixture
def H(load):
    return load("kernel.household")


def _o(H, daypart="", occupied=False, activity="", weekday=None):
    return H.Observation(daypart=daypart, occupied=occupied, activity=activity, weekday=weekday)


def test_occupancy_rhythm(H):
    obs = [_o(H, "morning", True), _o(H, "morning", True), _o(H, "morning", False),
           _o(H, "night", False)]
    r = H.occupancy_rhythm(obs)
    assert r["morning"] == round(2 / 3, 4)
    assert r["night"] == 0.0
    assert "afternoon" not in r          # no data → omitted


def test_routines_min_support(H):
    obs = [_o(H, activity="wake"), _o(H, activity="coffee"),
           _o(H, activity="wake"), _o(H, activity="coffee"),
           _o(H, activity="coffee"), _o(H, activity="leave")]
    rt = H.routines(obs, min_support=2)
    assert ("wake", "coffee", 2) in rt
    assert all(n >= 2 for _a, _b, n in rt)   # below-support transitions dropped


def test_anticipate_presence_suggestion(H):
    obs = [_o(H, "evening", True), _o(H, "evening", True), _o(H, "evening", False)]
    sugg = H.anticipate(obs, daypart="evening")
    presence = [s for s in sugg if s.kind == "presence"]
    assert presence and presence[0].advisory is True
    assert presence[0].confidence == round(2 / 3, 4)


def test_anticipate_routine_suggestion(H):
    obs = [_o(H, activity="wake"), _o(H, activity="coffee"),
           _o(H, activity="wake"), _o(H, activity="coffee")]
    sugg = H.anticipate(obs, last_activity="wake", min_support=2)
    routine = [s for s in sugg if s.kind == "routine"]
    assert routine and "coffee" in routine[0].message


def test_suggestions_are_always_advisory_never_actuators(H):
    obs = [_o(H, "morning", True), _o(H, "morning", True),
           _o(H, activity="wake"), _o(H, activity="coffee"),
           _o(H, activity="wake"), _o(H, activity="coffee")]
    for s in H.anticipate(obs, daypart="morning", last_activity="wake"):
        d = s.to_dict()
        assert d["advisory"] is True
        assert not hasattr(s, "actuator") and "actuator" not in d


def test_anticipate_empty_is_empty(H):
    assert H.anticipate([], daypart="night") == []
    assert H.anticipate(None) == []


def test_summarize(H):
    obs = [_o(H, "morning", True), _o(H, activity="wake"), _o(H, activity="coffee"),
           _o(H, activity="wake"), _o(H, activity="coffee")]
    s = H.summarize(obs)
    assert s["observations"] == 5 and s["dayparts_modeled"] >= 1
    assert s["top_routine"] == "wake→coffee"
