"""MCU Phase C (C4): the WorldModel facade is *authoritative* on the migrated
context-read paths — not merely consulted in parallel with raw state.

C1–C3 routed presence / home-summary / briefing context through
`WorldModel.devices(...)`. Their own tests seed HA state and check the rendered
output — but because the facade preserves state verbatim, those tests pass
whether the code reads the facade OR the raw sweep, so they cannot catch a
silent regression back to `hass.states.async_all`.

These tests close that gap. Each makes the facade return something that
*differs* from the raw HA state and asserts the facade's value wins. If a
migrated path ever reverts to a raw read, the facade value would be ignored and
the assertion fails. This is what makes the `world_model` adoption stage
**enforce** (authoritative on a live path), not just parity.
"""
import json

import pytest


def _canon(entity_id, state, **attrs):
    eid = entity_id
    return {
        "entity_id": eid,
        "domain": eid.split(".", 1)[0],
        "name": attrs.get("friendly_name") or eid,
        "state": state,
        "area": None,
        "attributes": dict(attrs),
    }


def _fake_devices(mapping):
    """Build a WorldModel.devices replacement returning `mapping[domain]`."""
    def devices(self, *, domain=None, area=None):
        return list(mapping.get(domain, []))
    return devices


# ── C2: SafetyManager._residents_away reads the facade ──────────────────────────

def test_residents_away_is_authoritative_from_facade(cognitive_core, fake_hass,
                                                     monkeypatch):
    # Raw HA state is EMPTY → the legacy sweep would see no tracked presence and
    # return False (untracked ≠ away). The facade reports a tracked-but-away
    # person, which must flip the decision to True — proving the facade is read.
    monkeypatch.setattr(
        cognitive_core.WorldModel, "devices",
        _fake_devices({"person": [_canon("person.ghost", "not_home")]}))
    safety = cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})
    assert safety._residents_away() is True  # raw-read path would give False


# ── C3: home_state._build_summary reads the facade ──────────────────────────────

def test_build_summary_is_authoritative_from_facade(load, fake_hass, monkeypatch):
    home_state = load("home_state")
    # Raw HA has a light that is OFF; the facade reports a DIFFERENT light ON.
    fake_hass.states.set("light.raw", "off", friendly_name="Raw Light")
    monkeypatch.setattr(
        home_state.WorldModel, "devices",
        _fake_devices({"light": [_canon("light.facade", "on",
                                        friendly_name="Facade Light")]}))
    summary = home_state._build_summary(fake_hass)
    assert "Facade Light" in summary          # facade value wins
    assert "Raw Light" not in summary          # raw sweep is not consulted


# ── C3: agent._exec_home_summary reads the facade ───────────────────────────────

async def test_exec_home_summary_is_authoritative_from_facade(load, fake_hass,
                                                              monkeypatch):
    agent = load("agent")
    # `agent._exec_home_summary` imports WorldModel locally; it resolves to the
    # same class object that `home_state` binds at module level, so patching the
    # class via home_state's handle also governs the agent tool.
    home_state = load("home_state")
    # Raw HA is empty; the facade reports a person. The tool must report the
    # facade's person, proving it does not fall back to a raw sweep.
    monkeypatch.setattr(
        home_state.WorldModel, "devices",
        _fake_devices({"person": [_canon("person.facade", "home",
                                         friendly_name="Facade Person")]}))
    out = json.loads(await agent._exec_home_summary(fake_hass, {}))
    assert out["people"] == [{"name": "Facade Person", "state": "home"}]


# ── C3: proactive_briefing._anyone_home reads the facade ────────────────────────

def test_anyone_home_is_authoritative_from_facade(load, fake_hass, monkeypatch):
    pb = load("proactive_briefing")
    # Raw HA empty → raw read would say nobody home; facade says someone is.
    monkeypatch.setattr(
        pb.WorldModel, "devices",
        _fake_devices({"person": [_canon("person.facade", "home")]}))
    assert pb._anyone_home(fake_hass) is True  # raw-read path would give False
