"""Per-entity pattern-log rate limit — the flood cap."""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


def test_interval_default_and_config(cc, monkeypatch):
    cc._CORE.config = {}
    assert cc._pattern_log_interval() == 60.0        # default
    cc._CORE.config = {"pattern_log_min_interval": 30}
    assert cc._pattern_log_interval() == 30.0
    cc._CORE.config = {"pattern_log_min_interval": 0}
    assert cc._pattern_log_interval() == 0.0          # disabled
    cc._CORE.config = {"pattern_log_min_interval": "bad"}
    assert cc._pattern_log_interval() == 60.0          # bad value -> default
