"""Tests for the governance enforcement registry (panel Governance tab backend).

The registry names every live ``*_ENFORCE`` kill-switch, reports its value, and
lets the owner persist an override that is applied to the live flag. Here the
target modules and the config store are faked, so the test is pure and never
imports the heavy consumer modules.
"""
import types

import pytest


@pytest.fixture
def enf(load, monkeypatch):
    mod = load("enforcement")
    # One fake module per target, pre-seeded with its flag at the code default.
    fakes: dict[str, types.SimpleNamespace] = {}
    for sw in mod.all_switches():
        ns = fakes.setdefault(sw.module, types.SimpleNamespace())
        setattr(ns, sw.attr, sw.default)
    monkeypatch.setattr(mod, "_resolve", lambda name: fakes.get(name))
    # In-memory config store standing in for jarvis_config.
    store: dict = {}
    monkeypatch.setattr(mod, "_cfg_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(mod, "_cfg_set", lambda k, v: store.__setitem__(k, v))
    return types.SimpleNamespace(mod=mod, fakes=fakes, store=store)


def test_registry_is_wellformed(enf):
    switches = enf.mod.all_switches()
    assert switches, "registry must not be empty"
    keys = [s.key for s in switches]
    assert len(keys) == len(set(keys)), "switch keys must be unique"
    for s in switches:
        assert s.category in (enf.mod.CATEGORY_SAFETY, enf.mod.CATEGORY_CAPABILITY)
        assert s.name and s.explanation and s.attr and s.module
        assert isinstance(s.default, bool)


def test_known_defaults(enf):
    by_key = {s.key: s for s in enf.mod.all_switches()}
    # Owner-opt-in capabilities ship OFF.
    for k in ("continuity_resume", "agency_orchestration", "knowledge_graph"):
        assert by_key[k].default is False
        assert by_key[k].category == enf.mod.CATEGORY_CAPABILITY
    # Live safety rungs ship ON.
    for k in ("authority", "agency_budget", "loop_detect", "safety_seam",
              "hazard_situation", "intrusion_gate", "delivery_situation"):
        assert by_key[k].default is True
        assert by_key[k].category == enf.mod.CATEGORY_SAFETY


def test_current_reports_defaults_without_override(enf):
    for entry in enf.mod.current():
        assert entry["enabled"] == entry["default"]
        assert entry["overridden"] is False


def test_set_switch_persists_and_flips_live(enf):
    entry = enf.mod.set_switch("continuity_resume", True)
    assert entry is not None and entry["enabled"] is True and entry["overridden"] is True
    # Persisted to the (fake) config store…
    sw = {s.key: s for s in enf.mod.all_switches()}["continuity_resume"]
    assert enf.store[sw.config_key] is True
    # …and applied to the live (fake) module attribute immediately.
    assert getattr(enf.fakes["continuity"], "CONTINUITY_RESUME_ENFORCE") is True


def test_set_switch_can_disable_a_safety_kill_switch(enf):
    # The safety rungs are kill-switches: turning one OFF must take effect.
    entry = enf.mod.set_switch("authority", False)
    assert entry is not None and entry["enabled"] is False
    assert getattr(enf.fakes["authority_bridge"], "AUTHORITY_ENFORCE") is False


def test_set_switch_unknown_key_returns_none(enf):
    assert enf.mod.set_switch("does_not_exist", True) is None


def test_apply_overrides_applies_only_stored(enf):
    sw = {s.key: s for s in enf.mod.all_switches()}
    enf.store[sw["continuity_resume"].config_key] = True
    enf.store[sw["authority"].config_key] = False
    applied = enf.mod.apply_overrides()
    assert applied == 2
    assert getattr(enf.fakes["continuity"], "CONTINUITY_RESUME_ENFORCE") is True
    assert getattr(enf.fakes["authority_bridge"], "AUTHORITY_ENFORCE") is False
    # A switch with no stored override keeps its code default.
    assert getattr(enf.fakes["agent"], "AGENCY_ORCHESTRATION_ENFORCE") is False


def test_live_value_falls_back_when_module_missing(enf, monkeypatch):
    monkeypatch.setattr(enf.mod, "_resolve", lambda name: None)
    for entry in enf.mod.current():
        assert entry["enabled"] == entry["default"]   # default fallback, no raise


def test_apply_overrides_is_defensive(enf, monkeypatch):
    monkeypatch.setattr(enf.mod, "_resolve", lambda name: None)
    sw = {s.key: s for s in enf.mod.all_switches()}
    enf.store[sw["continuity_resume"].config_key] = True
    # Module unresolvable → applied count 0, no raise.
    assert enf.mod.apply_overrides() == 0
