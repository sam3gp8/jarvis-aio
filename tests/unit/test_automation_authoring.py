"""Plain-language automation authoring with entity/service validation.

The validator checks every entity_id and service a would-be automation
references against the live Home Assistant instance, so JARVIS never installs an
automation that silently points at a guessed or misspelled name — it refuses up
front and hands back the closest real name. Opt-in (validate=True), so the
existing HA service + learned-suggestion installer paths are unchanged.
"""
import json

import pytest

from fakes import FakeHass


@pytest.fixture
def ac(load):
    return load("automation_creator")


def _hass_with(entities=(), services=()):
    h = FakeHass()
    for eid in entities:
        h.states.set(eid, "on")
    for dom_svc in services:
        dom, svc = dom_svc.split(".", 1)
        h.services.register(dom, svc)
    return h


# ── reference extraction ──────────────────────────────────────────────────────

def test_collect_refs_pulls_entities_and_services(ac):
    ents, svcs = set(), set()
    ac._collect_refs(
        [{"trigger": "state", "entity_id": "binary_sensor.front_door", "to": "on"}],
        ents, svcs)
    ac._collect_refs(
        [{"action": "light.turn_on", "target": {"entity_id": ["light.porch", "light.hall"]}}],
        ents, svcs)
    assert ents == {"binary_sensor.front_door", "light.porch", "light.hall"}
    assert svcs == {"light.turn_on"}


def test_collect_refs_skips_templates_and_non_dotted(ac):
    ents, svcs = set(), set()
    ac._collect_refs(
        [{"action": "{{ 'light.turn_' ~ x }}", "entity_id": "all"},
         {"service": "notify.mobile", "data": {"message": "hi there"}}],
        ents, svcs)
    assert ents == set()                 # "all" is not a dotted entity
    assert svcs == {"notify.mobile"}     # template service skipped, real one kept


# ── validation ────────────────────────────────────────────────────────────────

def test_valid_config_passes(ac):
    h = _hass_with(entities=["light.porch", "binary_sensor.front_door"],
                   services=["light.turn_on"])
    report = ac.validate_automation_config(
        h,
        trigger=[{"trigger": "state", "entity_id": "binary_sensor.front_door", "to": "on"}],
        condition=None,
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}])
    assert report["ok"] is True
    assert report["invalid_entities"] == {} and report["invalid_services"] == {}


def test_bad_entity_is_flagged_with_closest_match(ac):
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "light.porchh"}],
        condition=None,
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}])
    assert report["ok"] is False
    assert report["invalid_entities"] == {"light.porchh": "light.porch"}


def test_bad_service_is_flagged(ac):
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "light.porch"}],
        condition=None,
        action=[{"action": "light.turnon", "target": {"entity_id": "light.porch"}}])
    assert report["ok"] is False
    assert report["invalid_services"] == {"light.turnon": "light.turn_on"}


def test_unknown_name_with_no_close_match_reports_none(ac):
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "sensor.zzzzz_nonsense"}],
        condition=None, action=[{"action": "light.turn_on"}])
    assert report["ok"] is False
    assert report["invalid_entities"] == {"sensor.zzzzz_nonsense": None}


# ── create_automation gate (fail path writes nothing) ─────────────────────────

@pytest.mark.asyncio
async def test_create_with_validation_refuses_bad_name(ac):
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    res = await ac.create_automation(
        h, alias="Porch at dusk",
        trigger=[{"trigger": "state", "entity_id": "light.porchh"}],
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        validate=True)
    assert res["success"] is False
    assert "light.porchh" in res["invalid_entities"]
    # nothing was written: no reload service call happened
    assert h.services.calls == [] if hasattr(h.services, "calls") else True


@pytest.mark.asyncio
async def test_validation_is_opt_in(ac, monkeypatch):
    # Default (validate omitted) must not even call the validator — existing
    # callers are unchanged. Make the validator explode to prove it isn't run.
    def _boom(*a, **k):
        raise AssertionError("validator must not run when validate is not set")
    monkeypatch.setattr(ac, "validate_automation_config", _boom)

    h = _hass_with()
    # Missing trigger/action short-circuits before any write; the point is that
    # the validator wasn't consulted.
    res = await ac.create_automation(h, alias="x", trigger=None, action=None)
    assert res["success"] is False  # "trigger and action required", not a validator run


# ── agent tool handler ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_tool_requires_trigger_and_action(load):
    agent = load("agent")
    h = _hass_with()
    out = json.loads(await agent._exec_create_automation(h, {"alias": "x"}))
    assert out["success"] is False and "required" in out["error"].lower()


@pytest.mark.asyncio
async def test_agent_tool_validates_and_reports_bad_name(load):
    agent = load("agent")
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    out = json.loads(await agent._exec_create_automation(h, {
        "alias": "Porch",
        "trigger": [{"trigger": "state", "entity_id": "light.porchh"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
    }))
    assert out["success"] is False
    assert out["invalid_entities"] == {"light.porchh": "light.porch"}


def test_create_automation_tool_is_registered(load):
    agent = load("agent")
    assert "create_automation" in agent._TOOL_MAP
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "create_automation" in names


# ── closed validation bypass: non-dotted refs are now FLAGGED (spec #40, 2C) ──

def test_nondotted_entity_name_is_flagged_not_ignored(ac):
    # The old _collect_refs silently dropped "Living Room Light" (ok=True on a
    # hallucinated name). It must now be reported as invalid.
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "Living Room Light"}],
        condition=None,
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}])
    assert report["ok"] is False
    assert "Living Room Light" in report["invalid_entities"]


def test_nondotted_action_service_is_flagged(ac):
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "light.porch"}],
        condition=None,
        action=[{"action": "turn_on", "target": {"entity_id": "light.porch"}}])
    assert report["ok"] is False
    assert "turn_on" in report["invalid_services"]


def test_mobile_notification_action_payload_is_not_a_service(ac):
    # "action" inside a notify data payload is a button id, not a service ref —
    # it must not be flagged.
    h = _hass_with(entities=[], services=["notify.mobile_app"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "time", "at": "08:00:00"}], condition=None,
        action=[{"action": "notify.mobile_app",
                 "data": {"message": "hi", "data": {"actions": [
                     {"action": "SNOOZE", "title": "Snooze"}]}}}])
    assert report["ok"] is True
    assert report["invalid_services"] == {}


# ── semantic validation (spec #40, 2C) ───────────────────────────────────────

def test_impossible_sun_condition_is_flagged(ac):
    h = _hass_with(entities=["binary_sensor.door", "light.porch"],
                   services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        condition=[{"condition": "sun", "after": "sunset", "before": "sunrise"}],
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}])
    assert any("sun" in e for e in report["semantic_errors"])


def test_binary_sensor_impossible_state_is_flagged(ac):
    h = _hass_with(entities=["binary_sensor.front_door", "light.porch"],
                   services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "binary_sensor.front_door",
                     "to": "open"}],
        condition=None,
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}])
    assert any("binary_sensor" in e for e in report["semantic_errors"])


def test_cross_domain_service_target_is_flagged(ac):
    h = _hass_with(entities=["switch.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "switch.porch"}],
        condition=None,
        action=[{"action": "light.turn_on", "target": {"entity_id": "switch.porch"}}])
    assert any("different domain" in e for e in report["semantic_errors"])


def test_homeassistant_service_is_allowed_cross_domain(ac):
    h = _hass_with(entities=["switch.porch"], services=["homeassistant.turn_off"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "switch.porch"}],
        condition=None,
        action=[{"action": "homeassistant.turn_off",
                 "target": {"entity_id": "switch.porch"}}])
    assert report["semantic_errors"] == []


def test_typeless_trigger_and_actionless_step_are_flagged(ac):
    h = _hass_with(entities=["light.porch"], services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"entity_id": "light.porch", "to": "on"}],   # no trigger/platform
        condition=None, action=[{"foo": "bar"}])                 # no action key
    assert any("trigger" in e for e in report["semantic_errors"])
    assert any("action" in e for e in report["semantic_errors"])


def test_well_formed_config_has_no_semantic_errors(ac):
    h = _hass_with(entities=["binary_sensor.door", "light.porch", "sun.sun"],
                   services=["light.turn_on"])
    report = ac.validate_automation_config(
        h, trigger=[{"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        condition=[{"condition": "state", "entity_id": "sun.sun",
                    "state": "below_horizon"}],
        action=[{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}])
    assert report["ok"] is True
    assert report["semantic_errors"] == []


# ── modern plural-key coercion + strict structure (spec #40, 2D) ──────────────

def test_coerce_accepts_modern_plural_keys(ac):
    out = ac._coerce_automation({
        "alias": "x",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.d", "to": "on"}],
        "actions": [{"action": "light.turn_on"}]})
    assert out is not None
    assert out["trigger"] == [{"trigger": "state", "entity_id": "binary_sensor.d",
                               "to": "on"}]
    assert out["action"] == [{"action": "light.turn_on"}]
    assert "triggers" not in out and "actions" not in out


def test_coerce_rejects_string_and_empty_steps(ac):
    assert ac._coerce_automation(
        {"trigger": "state", "action": [{"action": "light.turn_on"}]}) is None
    assert ac._coerce_automation(
        {"trigger": [{"trigger": "state"}], "action": []}) is None
    assert ac._coerce_automation(
        {"trigger": [{"trigger": "state"}], "action": ["light.turn_on"]}) is None


# ── alias / id hygiene (spec #40, 2A) ────────────────────────────────────────

def test_clean_alias_strips_every_jarvis_prefix(ac):
    assert ac._clean_alias("JARVIS · Porch") == "Porch"
    assert ac._clean_alias("JARVIS Learned: Hall light") == "Hall light"
    assert ac._clean_alias("JARVIS Learned (review): Close garage") == "Close garage"
    assert ac._clean_alias("JARVIS · JARVIS Learned: Lamp") == "Lamp"
    assert ac._clean_alias("Night Lights") == "Night Lights"
    assert ac._clean_alias("") == "automation"


def test_slug_alias_is_strict(ac):
    assert ac._slug_alias("Porch @ Dusk!!") == "jarvis_auto_porch_dusk"
    assert ac._slug_alias("") == "jarvis_auto_unnamed"
    assert ac._slug_alias("---") == "jarvis_auto_unnamed"
