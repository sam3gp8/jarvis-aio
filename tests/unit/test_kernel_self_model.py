"""Tests for the self-model primitive (roadmap Phase S — Self Model & Awareness).

Pure: a read-only projection of JARVIS's capabilities / commitments / confidence
/ limits. The hard rule is structural — the model only *describes*; naming a
capability never makes it usable, and an unavailable one is never reported usable.
"""
import pytest


@pytest.fixture
def S(load):
    return load("kernel.self_model")


def test_project_coerces_mixed_capability_inputs(S):
    sm = S.project(capabilities=[
        S.Capability("control_device"),                         # object
        {"name": "vision", "status": S.DEGRADED, "note": "slow"},  # mapping
        "recall",                                               # bare string
        {"status": S.AVAILABLE},                                # no name → dropped
        123,                                                     # junk → dropped
    ])
    names = [c.name for c in sm.capabilities]
    assert names == ["control_device", "vision", "recall"]
    assert sm.capability("vision").status == S.DEGRADED


def test_can_is_case_insensitive_and_usability_aware(S):
    sm = S.project(capabilities=[
        {"name": "control_device", "status": S.AVAILABLE},
        {"name": "arm_alarm", "status": S.UNAVAILABLE},
        {"name": "vision", "status": S.DEGRADED},
    ])
    assert sm.can("Control_Device") is True        # case-insensitive, available
    assert sm.can("vision") is True                # degraded is still usable
    assert sm.can("arm_alarm") is False            # present but NOT usable
    assert sm.can("teleport") is False             # absent
    assert sm.can("") is False


def test_available_excludes_unavailable(S):
    sm = S.project(capabilities=[
        {"name": "a", "status": S.AVAILABLE},
        {"name": "b", "status": S.UNAVAILABLE},
        {"name": "c", "status": S.DEGRADED},
    ])
    assert {c.name for c in sm.available} == {"a", "c"}
    assert sm.to_dict()["usable_count"] == 2


def test_report_never_overclaims(S):
    sm = S.project(
        capabilities=[{"name": "a", "status": S.AVAILABLE},
                      {"name": "b", "status": S.UNAVAILABLE}],
        commitments=["dim the lights at sunset"],
        limits=["cannot unlock the front door"],
        confidence=0.8,
        identity="Sir",
    )
    rep = sm.report()
    # 1 of 2 usable — the unavailable one is not counted usable.
    assert "1/2 capabilities usable" in rep
    assert "1 commitment" in rep and "1 known limit" in rep
    assert "confidence 0.80" in rep and "serving Sir" in rep


def test_confidence_is_clamped(S):
    assert S.project(confidence=5.0).confidence == 1.0
    assert S.project(confidence=-1.0).confidence == 0.0
    assert S.project(confidence="nope").confidence == 1.0   # bad → default 1.0


def test_capability_usability_by_status(S):
    assert S.Capability("x", S.AVAILABLE).usable is True
    assert S.Capability("x", S.DEGRADED).usable is True
    assert S.Capability("x", S.UNAVAILABLE).usable is False


def test_empty_projection_is_empty_and_safe(S):
    sm = S.project()
    assert sm.is_empty is True
    assert sm.available == ()
    # report() must not raise on an empty model.
    assert sm.report().startswith("self: 0/0 capabilities usable")


def test_unknown_status_falls_back_to_available(S):
    sm = S.project(capabilities=[{"name": "x", "status": "bogus"}])
    assert sm.capability("x").status == S.AVAILABLE
    assert sm.can("x") is True


def test_to_dict_round_trips_fields(S):
    sm = S.project(capabilities=["a"], commitments=["g"], limits=["l"],
                   confidence=0.5, identity="Sir")
    d = sm.to_dict()
    assert d["commitments"] == ["g"] and d["limits"] == ["l"]
    assert d["confidence"] == 0.5 and d["identity"] == "Sir"
    assert d["capabilities"][0]["name"] == "a" and d["usable_count"] == 1
