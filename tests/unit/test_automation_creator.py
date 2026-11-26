"""Tests for automation YAML creation and Home Assistant reload behavior."""
from __future__ import annotations

import sys
import types

import pytest
import yaml


@pytest.fixture
def creator(load):
    module = load("automation_creator")
    sys.modules["jc"].__dict__["automation_creator"] = module
    return module


class FakeServices:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def async_call(self, domain, service, **kwargs):
        self.calls.append((domain, service, kwargs))
        if self.error:
            raise self.error


class FakeHass:
    def __init__(self, path, reload_error=None, path_error=None):
        self.config = types.SimpleNamespace(path=self._path)
        self._automation_path = path
        self._path_error = path_error
        self.executor_calls = 0
        self.services = FakeServices(reload_error)

    def _path(self, filename):
        if self._path_error:
            raise self._path_error
        assert filename == "automations.yaml"
        return str(self._automation_path)

    async def async_add_executor_job(self, func):
        self.executor_calls += 1
        return func()


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger,action", [
    (None, [{"service": "light.turn_off"}]),
    ({"platform": "time", "at": "00:00:00"}, None),
    ([], [{"service": "light.turn_off"}]),
    ([{"platform": "time"}], []),
])
async def test_missing_trigger_or_action_is_rejected_without_io(
        creator, tmp_path, trigger, action):
    hass = FakeHass(tmp_path / "automations.yaml")

    result = await creator.create_automation(
        hass, alias="Night lights", trigger=trigger, action=action)

    assert result == {
        "success": False, "error": "Both trigger and action are required"}
    assert hass.executor_calls == 0
    assert hass.services.calls == []


@pytest.mark.asyncio
async def test_create_normalizes_dict_inputs_and_writes_default_description(creator, tmp_path):
    path = tmp_path / "automations.yaml"
    hass = FakeHass(path)

    result = await creator.create_automation(
        hass,
        alias="Night Lights",
        trigger={"platform": "time", "at": "00:00:00"},
        condition={"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
        action={"service": "light.turn_off", "target": {"area_id": "living_room"}},
        mode="restart",
    )

    assert result == {
        "success": True,
        "automation_id": "jarvis_auto_night_lights",
        "alias": "JARVIS · Night Lights",
    }
    saved = yaml.safe_load(path.read_text())
    assert len(saved) == 1
    assert saved[0] == {
        "id": "jarvis_auto_night_lights",
        "alias": "JARVIS · Night Lights",
        "description": "Created by JARVIS: Night Lights",
        "mode": "restart",
        "triggers": [{"platform": "time", "at": "00:00:00"}],
        "conditions": [{
            "condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}],
        "actions": [{
            "service": "light.turn_off", "target": {"area_id": "living_room"}}],
    }
    assert hass.services.calls == [
        ("automation", "reload", {"blocking": True})]
    assert hass.executor_calls == 1


@pytest.mark.asyncio
async def test_create_preserves_lists_and_omits_empty_condition(creator, tmp_path):
    path = tmp_path / "automations.yaml"
    hass = FakeHass(path)
    trigger = [{"platform": "state", "entity_id": "binary_sensor.door"}]
    action = [{"service": "notify.mobile_app_phone"}]

    result = await creator.create_automation(
        hass, alias="Door alert", description="Notify when open",
        trigger=trigger, condition=[], action=action, mode="queued")

    assert result["success"] is True
    saved = yaml.safe_load(path.read_text())[0]
    assert saved["triggers"] == trigger
    assert saved["actions"] == action
    assert saved["description"] == "Notify when open"
    assert saved["mode"] == "queued"
    assert "conditions" not in saved


@pytest.mark.asyncio
async def test_create_replaces_duplicate_id_and_preserves_other_automations(
        creator, tmp_path):
    path = tmp_path / "automations.yaml"
    path.write_text(yaml.safe_dump([
        {"id": "other_automation", "alias": "Keep me"},
        {"id": "jarvis_auto_lights_off", "alias": "Old version"},
        {"id": "jarvis_auto_lights_off", "alias": "Another old version"},
    ]))
    hass = FakeHass(path)

    result = await creator.create_automation(
        hass, alias="Lights Off", trigger={"platform": "time"},
        action={"service": "light.turn_off"})

    saved = yaml.safe_load(path.read_text())
    assert result["success"] is True
    assert [item["id"] for item in saved] == [
        "other_automation", "jarvis_auto_lights_off"]
    assert saved[-1]["alias"] == "JARVIS · Lights Off"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_yaml", ["not: [valid", "{not: a list}"])
async def test_create_refuses_to_overwrite_unparseable_or_nonlist_yaml(
        creator, tmp_path, existing_yaml):
    """Data-loss guard (spec #40, 2B): an unparseable automations.yaml (a syntax
    error, or HA !secret/!include tags) or a non-list document must ABORT the
    write, not silently reset to [] and wipe every automation the user has. The
    original file is left byte-for-byte untouched."""
    path = tmp_path / "automations.yaml"
    path.write_text(existing_yaml)
    hass = FakeHass(path)

    result = await creator.create_automation(
        hass, alias="Recovered", trigger={"platform": "state"},
        action={"service": "light.turn_on"})

    assert result["success"] is False
    # the user's file is preserved exactly — nothing overwritten, nothing reloaded
    assert path.read_text() == existing_yaml
    assert hass.services.calls == []


@pytest.mark.asyncio
async def test_yaml_generation_failure_returns_without_writing(creator, tmp_path, monkeypatch):
    hass = FakeHass(tmp_path / "automations.yaml")

    def fail_dump(*args, **kwargs):
        raise RuntimeError("cannot serialize")

    monkeypatch.setattr(creator.yaml, "dump", fail_dump)
    result = await creator.create_automation(
        hass, alias="Broken", trigger={"platform": "state"},
        action={"service": "light.turn_on"})

    assert result == {"success": False, "error": "YAML generation failed: cannot serialize"}
    assert hass.executor_calls == 0
    assert not (tmp_path / "automations.yaml").exists()


@pytest.mark.asyncio
async def test_path_and_file_write_failures_are_returned(creator, tmp_path):
    path_failure = FakeHass(
        tmp_path / "automations.yaml", path_error=RuntimeError("config unavailable"))
    result = await creator.create_automation(
        path_failure, alias="Path error", trigger={"platform": "state"},
        action={"service": "light.turn_on"})
    assert result == {"success": False, "error": "config unavailable"}

    blocked_path = tmp_path / "automations.yaml"
    blocked_path.mkdir()
    write_failure = FakeHass(blocked_path)
    result = await creator.create_automation(
        write_failure, alias="Write error", trigger={"platform": "state"},
        action={"service": "light.turn_on"})
    assert result["success"] is False
    assert "Is a directory" in result["error"]
    assert write_failure.services.calls == []


@pytest.mark.asyncio
async def test_reload_failure_returns_error_after_file_is_written(creator, tmp_path):
    path = tmp_path / "automations.yaml"
    hass = FakeHass(path, reload_error=RuntimeError("reload failed"))

    result = await creator.create_automation(
        hass, alias="Reload failure", trigger={"platform": "state"},
        action={"service": "light.turn_on"})

    assert result == {"success": False, "error": "reload failed"}
    assert yaml.safe_load(path.read_text())[0]["id"] == "jarvis_auto_reload_failure"
    assert hass.services.calls == [
        ("automation", "reload", {"blocking": True})]


# ── structural schema enforcement (spec #40, 2A) ──────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("trigger,action,bad_field", [
    ("state", [{"action": "light.turn_on"}], "trigger"),          # string trigger
    ([{"trigger": "state"}], "light.turn_on", "action"),          # string action
    ([{"trigger": "state"}], ["light.turn_on"], "action"),        # non-dict step
])
async def test_non_listdict_trigger_or_action_is_rejected_without_io(
        creator, tmp_path, trigger, action, bad_field):
    hass = FakeHass(tmp_path / "automations.yaml")
    result = await creator.create_automation(
        hass, alias="x", trigger=trigger, action=action)
    assert result["success"] is False
    assert bad_field in result["error"]
    assert hass.executor_calls == 0           # a corrupted shape never reaches disk
    assert hass.services.calls == []


@pytest.mark.asyncio
async def test_invalid_mode_is_rejected_when_validating(creator, tmp_path):
    hass = FakeHass(tmp_path / "automations.yaml")
    result = await creator.create_automation(
        hass, alias="x", trigger=[{"trigger": "state"}],
        action=[{"action": "light.turn_on"}], mode="turbo", validate=True)
    assert result["success"] is False and "mode" in result["error"]
    assert hass.executor_calls == 0


@pytest.mark.asyncio
async def test_invalid_mode_falls_back_to_single_without_validate(creator, tmp_path):
    path = tmp_path / "automations.yaml"
    hass = FakeHass(path)
    result = await creator.create_automation(
        hass, alias="x", trigger={"trigger": "state"},
        action={"action": "light.turn_on"}, mode="turbo")
    assert result["success"] is True
    assert yaml.safe_load(path.read_text())[0]["mode"] == "single"


# ── alias de-branding + strict id slug (spec #40, 2A) ─────────────────────────

@pytest.mark.asyncio
async def test_learned_prefix_is_stripped_and_id_is_strictly_slugified(creator, tmp_path):
    path = tmp_path / "automations.yaml"
    hass = FakeHass(path)
    result = await creator.create_automation(
        hass, alias="JARVIS Learned: Porch @ Dusk!!",
        trigger={"trigger": "state", "entity_id": "binary_sensor.d"},
        action={"action": "light.turn_on"})
    assert result["success"] is True
    # branded exactly once, from the user's real name; id strictly slugified
    assert result["alias"] == "JARVIS · Porch @ Dusk!!"
    assert result["automation_id"] == "jarvis_auto_porch_dusk"
    saved = yaml.safe_load(path.read_text())[0]
    assert saved["alias"] == "JARVIS · Porch @ Dusk!!"
    assert saved["id"] == "jarvis_auto_porch_dusk"


@pytest.mark.asyncio
async def test_existing_valid_automations_are_preserved_on_write(creator, tmp_path):
    # The atomic write must keep other automations intact (no silent wipe).
    path = tmp_path / "automations.yaml"
    path.write_text(yaml.safe_dump([{"id": "keep_me", "alias": "Hand-written"}]))
    hass = FakeHass(path)
    result = await creator.create_automation(
        hass, alias="New One", trigger={"trigger": "state"},
        action={"action": "light.turn_on"})
    assert result["success"] is True
    ids = [a["id"] for a in yaml.safe_load(path.read_text())]
    assert ids == ["keep_me", "jarvis_auto_new_one"]