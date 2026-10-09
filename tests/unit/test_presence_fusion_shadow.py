"""Multi-source presence fusion shadow (Epistemic Fabric — Conflict).

``presence.get_presence_summary()`` logs, per person, the world-model fusion of
their whereabouts across independent source types — HA's own ``person.state`` vs
a recent camera recognition — adjudicated through ``WorldModel.fuse_presence``
(``kernel.conflict``). Observe-only: the summary dict is unchanged.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def presence(load):
    p = load("presence")
    p.PRESENCE_FUSION_SHADOW = True
    return p


@pytest.fixture
def wm(load):
    return load("kernel.world_model")


def test_fusion_shadow_logs_contested(presence, wm, caplog, monkeypatch):
    h = FakeHass()
    h.states.set("person.sam", "not_home", friendly_name="Sam")   # HA: away
    monkeypatch.setattr(wm, "_who_is_where", lambda hass: {"camera.kitchen": "Sam"})
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    line = [r.message for r in caplog.records if "presence_fusion(shadow)" in r.message]
    assert line and "person.sam" in line[-1] and "CONTESTED" in line[-1]


def test_fusion_shadow_logs_resolved(presence, wm, caplog, monkeypatch):
    h = FakeHass()
    h.states.set("person.sam", "home", friendly_name="Sam")       # HA: home
    monkeypatch.setattr(wm, "_who_is_where", lambda hass: {"camera.kitchen": "Sam"})
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    line = [r.message for r in caplog.records if "presence_fusion(shadow)" in r.message]
    assert line and "resolved" in line[-1] and "'home'" in line[-1]


def test_fusion_shadow_single_source_logs_nothing(presence, wm, caplog, monkeypatch):
    h = FakeHass()
    h.states.set("person.sam", "home", friendly_name="Sam")
    monkeypatch.setattr(wm, "_who_is_where", lambda hass: {})      # no camera vote
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    # Only one source → no fusion → nothing logged.
    assert not [r for r in caplog.records if "presence_fusion(shadow)" in r.message]


def test_fusion_shadow_kill_switch(presence, wm, caplog, monkeypatch):
    presence.PRESENCE_FUSION_SHADOW = False
    try:
        h = FakeHass()
        h.states.set("person.sam", "not_home", friendly_name="Sam")
        monkeypatch.setattr(wm, "_who_is_where", lambda hass: {"camera.k": "Sam"})
        with caplog.at_level(logging.DEBUG):
            presence.get_presence_summary(h)
        assert not [r for r in caplog.records if "presence_fusion(shadow)" in r.message]
    finally:
        presence.PRESENCE_FUSION_SHADOW = True


def test_summary_unchanged_with_fusion_on_or_off(presence, wm, monkeypatch):
    h = FakeHass()
    h.states.set("person.sam", "not_home", friendly_name="Sam")
    monkeypatch.setattr(wm, "_who_is_where", lambda hass: {"camera.k": "Sam"})
    presence.PRESENCE_FUSION_SHADOW = True
    on = presence.get_presence_summary(h)
    presence.PRESENCE_FUSION_SHADOW = False
    off = presence.get_presence_summary(h)
    presence.PRESENCE_FUSION_SHADOW = True
    assert on == off
