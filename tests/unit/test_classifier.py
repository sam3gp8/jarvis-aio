"""Rule and LLM-fallback tests for the Observer event classifier."""
import pytest


@pytest.fixture
def classifier(load):
    return load("classifier")


@pytest.mark.parametrize("entity_id", [
    "sensor.sun_next_rising", "sensor.time", "button.restart",
    "input_boolean.guest_mode", "automation.morning", "device_tracker.phone",
])
def test_noise_entity_prefixes_are_ignored(classifier, entity_id):
    result = classifier._rule_classify(entity_id, "off", "on", "", None, "12:00")
    assert result == {"worth_considering": False, "rule": "noise_prefix"}


@pytest.mark.parametrize(("old_state", "new_state"), [
    ("unknown", "unknown"), ("unavailable", "unknown"), ("on", "on"),
])
def test_noise_transitions_are_ignored(classifier, old_state, new_state):
    result = classifier._rule_classify(
        "sensor.example", old_state, new_state, "", None, "12:00")
    assert result == {"worth_considering": False, "rule": "noise_transition"}


@pytest.mark.parametrize("device_class", [
    "connectivity", "plug", "power", "update", "running", "battery_charging",
])
def test_noise_device_classes_are_ignored(classifier, device_class):
    result = classifier._rule_classify(
        "binary_sensor.example", "off", "on", "", device_class, "12:00")
    assert result == {"worth_considering": False, "rule": "noise_dc"}


@pytest.mark.parametrize("device_class", ["smoke", "gas", "moisture", "carbon_monoxide"])
def test_critical_safety_classes_always_pass(classifier, device_class):
    result = classifier._rule_classify(
        "sensor.example", "clear", "detected", "", device_class, "12:00")
    assert result == {
        "worth_considering": True, "urgency": "critical",
        "category": "security", "rule": "safety",
    }


@pytest.mark.parametrize(("device_class", "time", "urgency"), [
    ("door", "23:15", "high"), ("window", "05:45", "high"),
    ("garage_door", "12:30", "medium"), ("door", "bad-time", "medium"),
])
def test_open_doors_and_windows_are_time_sensitive(
        classifier, device_class, time, urgency):
    result = classifier._rule_classify(
        "binary_sensor.entry", "off", "on", "", device_class, time)
    assert result == {
        "worth_considering": True, "urgency": urgency,
        "category": "doors_windows", "rule": "door_opened",
    }


def test_closed_door_is_ignored(classifier):
    assert classifier._rule_classify(
        "binary_sensor.entry", "on", "off", "", "door", "23:00") == {
            "worth_considering": False, "rule": "door_closed"}


@pytest.mark.parametrize(("old_state", "new_state", "time", "expected"), [
    ("on", "off", "02:00", {"worth_considering": False, "rule": "motion_clear"}),
    ("off", "on", "04:59", {
        "worth_considering": True, "urgency": "low",
        "category": "presence", "rule": "motion_night"}),
    ("off", "on", "23:00", {
        "worth_considering": True, "urgency": "low",
        "category": "presence", "rule": "motion_night"}),
    ("off", "on", "12:00", {"worth_considering": False, "rule": "motion_routine"}),
    ("off", "on", "bad-time", {"worth_considering": False, "rule": "motion_routine"}),
])
def test_motion_and_occupancy_rules(classifier, old_state, new_state, time, expected):
    assert classifier._rule_classify(
        "binary_sensor.hall", old_state, new_state, "", "motion", time) == expected


@pytest.mark.parametrize(("old_state", "new_state", "rule"), [
    ("not_home", "home", "arrived"), ("home", "not_home", "left"),
])
def test_person_arrival_and_departure(classifier, old_state, new_state, rule):
    assert classifier._rule_classify(
        "person.sam", old_state, new_state, "Sam", None, "12:00") == {
            "worth_considering": True, "urgency": "medium",
            "category": "presence", "rule": rule,
        }


def test_person_other_transition_is_ignored(classifier):
    assert classifier._rule_classify(
        "person.sam", "not_home", "unknown", "Sam", None, "12:00") == {
            "worth_considering": False, "rule": "person_same"}


def test_lock_and_alarm_rules(classifier):
    assert classifier._rule_classify(
        "lock.front_door", "locked", "unlocked", "", None, "12:00") == {
            "worth_considering": True, "urgency": "medium",
            "category": "security", "rule": "unlocked"}
    assert classifier._rule_classify(
        "lock.front_door", "unlocked", "locked", "", None, "12:00") == {
            "worth_considering": False, "rule": "locked"}
    for state in ("triggered", "pending"):
        assert classifier._rule_classify(
            "alarm_control_panel.home", "disarmed", state, "", None, "12:00")["urgency"] == "critical"
    assert classifier._rule_classify(
        "alarm_control_panel.home", "disarmed", "armed_home", "", None, "12:00") == {
            "worth_considering": True, "urgency": "low",
            "category": "security", "rule": "alarm_change"}


@pytest.mark.parametrize(("entity_id", "rule"), [
    ("light.kitchen", "light_routine"),
    ("switch.plug", "switch_routine"),
    ("climate.lounge", "climate_routine"),
    ("media_player.tv", "media_player_routine"),
])
def test_routine_device_changes_are_ignored(classifier, entity_id, rule):
    assert classifier._rule_classify(
        entity_id, "off", "on", "", None, "12:00") == {
            "worth_considering": False, "rule": rule}


def test_sensor_spikes_drift_binary_sensors_and_ambiguous_entities(classifier):
    assert classifier._rule_classify(
        "sensor.house_power", "100", "1201", "", None, "12:00") == {
            "worth_considering": True, "urgency": "low",
            "category": "energy", "rule": "power_spike"}
    assert classifier._rule_classify(
        "sensor.wattage", "10", "1011", "", None, "12:00")["rule"] == "power_spike"
    for entity, old, new in (("sensor.temperature", "18", "19"),
                             ("sensor.power", "bad", "value")):
        assert classifier._rule_classify(
            entity, old, new, "", None, "12:00") == {
                "worth_considering": False, "rule": "sensor_drift"}
    assert classifier._rule_classify(
        "binary_sensor.unknown", "off", "on", "", None, "12:00") == {
            "worth_considering": False, "rule": "binary_no_dc"}
    assert classifier._rule_classify(
        "cover.garage", "closed", "open", "", None, "12:00") is None


@pytest.mark.parametrize(("raw", "expected"), [
    ("", {"worth_considering": False}),
    ('{"worth_considering": true}', {"worth_considering": True}),
    ('```json\n{"urgency":"high"}\n```', {"urgency": "high"}),
    ('prefix {"category":"energy"} suffix', {"category": "energy"}),
    ("not json", {"worth_considering": False}),
    ("prefix {broken} suffix", {"worth_considering": False}),
])
def test_parse_json_formats_and_invalid_input(classifier, raw, expected):
    assert classifier._parse_json(raw) == expected


class FakeHass:
    async def async_add_executor_job(self, func):
        return func()


class FakeProvider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_classify_returns_rule_result_without_calling_provider(classifier):
    provider = FakeProvider()
    result = await classifier.classify(
        FakeHass(), provider, entity_id="lock.front_door", old_state="locked",
        new_state="unlocked", now_hhmm="12:00")
    assert result["rule"] == "unlocked"
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("response", "expected"), [
    ({"text": '{"worth_considering":true,"urgency":"high","category":"security"}'},
     {"worth_considering": True, "urgency": "high", "category": "security"}),
    ({"text": '{"worth_considering":true,"urgency":"invalid"}'},
     {"worth_considering": True, "urgency": "low", "category": "other"}),
    ({"text": '{"worth_considering":false,"urgency":"critical"}'},
     {"worth_considering": False}),
    ('{"worth_considering":true,"urgency":"medium","category":"energy"}',
     {"worth_considering": True, "urgency": "medium", "category": "energy"}),
    ({"text": "not json"}, {"worth_considering": False}),
])
async def test_classify_normalizes_llm_fallback_response(classifier, response, expected):
    provider = FakeProvider(response=response)
    result = await classifier.classify(
        FakeHass(), provider, entity_id="cover.garage", old_state="closed",
        new_state="open", friendly_name="Garage door", now_hhmm="12:00")
    assert result == expected
    assert provider.calls[0][1] == {"temperature": 0.0, "max_tokens": 80}
    assert provider.calls[0][0][1]["role"] == "user"


@pytest.mark.asyncio
async def test_classify_swallows_provider_failure(classifier):
    provider = FakeProvider(error=RuntimeError("provider unavailable"))
    result = await classifier.classify(
        FakeHass(), provider, entity_id="cover.garage", old_state="closed",
        new_state="open")
    assert result == {"worth_considering": False}


@pytest.mark.asyncio
async def test_classify_continues_when_optional_websocket_logger_is_missing(
        classifier, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "jc.websocket", None)
    provider = FakeProvider(response={
        "text": '{"worth_considering":true,"urgency":"medium","category":"other"}'})

    result = await classifier.classify(
        FakeHass(), provider, entity_id="cover.garage", old_state="closed",
        new_state="open")

    assert result == {
        "worth_considering": True, "urgency": "medium", "category": "other"}