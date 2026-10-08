"""cognitive_core space/time shadow emission (roadmap Phase Q — shadow).

`_emit_space_time_shadow` builds the WorldModel space/time views and logs a
one-line summary, observe-only and kill-switched. It must never raise and must
drive nothing — these tests pin the log line, the kill-switch, and defensiveness.
"""
import logging

import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


def test_emit_logs_summary(cc, fake_hass, monkeypatch, caplog):
    # Fake the WorldModel the emitter constructs so no real store/HA is needed.
    class _G:
        areas = ("kitchen", "hall", "living")
        edges = (("hall", "kitchen"), ("hall", "living"))

        def is_empty(self):
            return False

    class _Frame:
        daypart = "evening"
        hour = 19
        is_known = True

    class _WM:
        def __init__(self, hass, config):
            pass

        def spatial_graph(self):
            return _G()

        def temporal_frame(self):
            return _Frame()

    monkeypatch.setattr(cc, "SPACE_TIME_SHADOW", True)
    # WorldModel is imported inside the function from .kernel.world_model; patch there.
    from jc.kernel import world_model as wm_mod
    monkeypatch.setattr(wm_mod, "WorldModel", _WM)

    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_shadow(fake_hass, {})
    assert any("space_time(shadow):" in r.message and "daypart=evening" in r.message
               for r in caplog.records)


def test_kill_switch_silences(cc, fake_hass, monkeypatch, caplog):
    monkeypatch.setattr(cc, "SPACE_TIME_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_shadow(fake_hass, {})
    assert not any("space_time(shadow)" in r.message for r in caplog.records)


def test_emit_is_defensive(cc, fake_hass, monkeypatch, caplog):
    from jc.kernel import world_model as wm_mod

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("world model down")

    monkeypatch.setattr(cc, "SPACE_TIME_SHADOW", True)
    monkeypatch.setattr(wm_mod, "WorldModel", _Boom)
    # must not raise
    with caplog.at_level(logging.DEBUG):
        cc._emit_space_time_shadow(fake_hass, {})
    assert not any("space_time(shadow)" in r.message for r in caplog.records)
