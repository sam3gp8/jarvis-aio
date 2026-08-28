"""Adaptive interruption budget in the output gate (opt-in).

When `adaptive_interruption_budget` is off (default), the hourly announcement cap
is unchanged. When on, the multiplier from decision_record.interruption_budget()
scales the cap down, so JARVIS interrupts less after a run of dismissed alerts.
"""
import pytest


@pytest.fixture
def og(load):
    return load("output_gate")


def _fill(og, n):
    now = og._now()
    og._STATE.history.clear()
    for i in range(n):
        og._STATE.history.append(og.Announcement(
            timestamp=now, entity_id=f"e{i}", category="x",
            urgency="low", message=f"m{i}", was_spoken=True))


def test_multiplier_off_by_default(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)   # flag unset -> False
    og._BUDGET_CACHE["ts"] = 0.0
    assert og._budget_multiplier() == 1.0


def test_multiplier_applied_when_enabled(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: True if k == "adaptive_interruption_budget" else d)
    dr = load("decision_record")
    monkeypatch.setattr(dr, "interruption_budget", lambda *a, **k: {"multiplier": 0.5})
    og._BUDGET_CACHE["ts"] = 0.0
    assert og._budget_multiplier() == 0.5


def test_cap_unchanged_when_off(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)   # adaptive off
    og._BUDGET_CACHE["ts"] = 0.0
    _fill(og, og.DEFAULT_MAX_PER_HOUR)                     # exactly at base cap (6)
    allowed, reason = og.can_announce(
        entity_id="e", category="x", urgency="low", message="new")
    assert allowed is False
    assert f"/{og.DEFAULT_MAX_PER_HOUR}/hour" in reason    # base cap enforced


def test_cap_tightened_when_adaptive_on(og, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: True if k == "adaptive_interruption_budget" else d)
    dr = load("decision_record")
    monkeypatch.setattr(dr, "interruption_budget", lambda *a, **k: {"multiplier": 0.5})
    og._BUDGET_CACHE["ts"] = 0.0
    half = max(1, round(og.DEFAULT_MAX_PER_HOUR * 0.5))    # 3
    _fill(og, half)                                        # only 3 in the last hour
    allowed, reason = og.can_announce(
        entity_id="e", category="x", urgency="low", message="new")
    assert allowed is False                                # blocked at the tightened cap
    assert f"/{half}/hour" in reason
