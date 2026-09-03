"""Manual 'Analyze Now' trigger — run_analysis_now() contract.

Bypasses only the 6h throttle (resets _last_analysis), keeps the data gate, and
returns a result the panel can show: {ran: False, reason} when ineligible, or
{ran: True, patterns_found, new_suggestions} when it ran.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


class _FakeAnalyzer:
    def __init__(self, eligible):
        self._eligible = eligible
        self._last_analysis = 999.0
        self._last_result = {}

    def should_analyze(self):
        return self._eligible

    async def analyze(self, hass):
        self._last_result = {"patterns_found": 3, "new_suggestions": 2,
                             "person_routines": 1, "facts": 0}
        return [1, 2, 3]


class _SL:
    def __init__(self, days, changes):
        self._d = days
        self._c = changes

    def get_pattern_stats(self):
        return {"days_of_data": self._d, "state_changes": self._c}


async def test_not_eligible_returns_reason(cc, load, monkeypatch, fake_hass):
    pa = load("pattern_analyzer")
    fake = _FakeAnalyzer(eligible=False)
    monkeypatch.setattr(pa, "get_analyzer", lambda: fake)
    monkeypatch.setattr(cc._CORE, "state_logger", _SL(days=3, changes=12))
    res = await cc.run_analysis_now(fake_hass)
    assert res["ran"] is False
    assert "week" in res["reason"].lower() or "7 days" in res["reason"]
    assert res["days_of_data"] == 3 and res["state_changes"] == 12
    assert fake._last_analysis == 0.0          # throttle was reset for the manual run


async def test_eligible_runs_and_reports_counts(cc, load, monkeypatch, fake_hass):
    pa = load("pattern_analyzer")
    fake = _FakeAnalyzer(eligible=True)
    monkeypatch.setattr(pa, "get_analyzer", lambda: fake)
    monkeypatch.setattr(pa, "set_thresholds", lambda *a, **k: None)
    monkeypatch.setattr(cc._CORE, "state_logger", _SL(days=20, changes=5000))
    cc._CORE.config = {}
    res = await cc.run_analysis_now(fake_hass)
    assert res["ran"] is True
    assert res["patterns_found"] == 3 and res["new_suggestions"] == 2
    assert res["state_changes"] == 5000 and res["days_of_data"] == 20
