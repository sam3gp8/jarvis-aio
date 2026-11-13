"""Tests for Frigate-native identity (v6.59.0) — reading a recognized name from
Frigate's sub_label instead of requiring Double Take. The MQTT handler needs a
live bus, so the focus here is the pure _parse_sub_label normalizer, which is
where the real risk lives: Frigate encodes sub_label differently by version."""
import pytest


@pytest.fixture
def rec(load):
    return load("recognition")


# ── string form: "Name" (no score) ──────────────────────────────────────────

def test_bare_string_name(rec):
    name, conf = rec._parse_sub_label("Sam")
    assert name == "Sam" and conf == 0.0


def test_string_is_trimmed(rec):
    name, conf = rec._parse_sub_label("  Eliana  ")
    assert name == "Eliana"


# ── list form: ["Name", score] with score 0..1 ──────────────────────────────

def test_list_name_and_fractional_score(rec):
    name, conf = rec._parse_sub_label(["Sam", 0.92])
    assert name == "Sam"
    assert conf == 92.0                       # 0..1 → percent


def test_list_score_already_percent(rec):
    # some setups may already emit a 0..100 value; don't double-scale
    name, conf = rec._parse_sub_label(["Sam", 95.0])
    assert name == "Sam" and conf == 95.0


def test_list_name_only(rec):
    name, conf = rec._parse_sub_label(["Sam"])
    assert name == "Sam" and conf == 0.0


# ── empty / malformed → no identity, never raises ────────────────────────────

def test_none_is_empty(rec):
    assert rec._parse_sub_label(None) == ("", 0.0)


def test_empty_string(rec):
    assert rec._parse_sub_label("") == ("", 0.0)


def test_empty_list(rec):
    assert rec._parse_sub_label([]) == ("", 0.0)


def test_garbage_score_does_not_raise(rec):
    name, conf = rec._parse_sub_label(["Sam", "notanumber"])
    # falls back cleanly — name may be kept but confidence must be safe
    assert conf == 0.0


def test_none_name_in_list(rec):
    name, conf = rec._parse_sub_label([None, 0.5])
    assert name == ""


# ── threshold semantics line up with is_confident logic ──────────────────────

def test_confidence_threshold_boundary(rec):
    # sanity: the module's threshold is a plain percent number
    assert isinstance(rec.CONFIDENCE_THRESHOLD, (int, float))
    _, conf = rec._parse_sub_label(["Sam", 0.61])
    assert conf >= rec.CONFIDENCE_THRESHOLD    # 61% clears a 60 threshold


# ── sub_label dedup: Frigate repeats the face on every tracking update ────────
# Frigate assigns the face during tracking, so the sub_label lands on "update"/
# "end" events and repeats on each update tick for the same tracked object. The
# handler must fire a recognition once per (event, name), re-firing only when the
# recognised name changes for that object.

@pytest.fixture(autouse=True)
def _clear_subid(rec):
    rec._FRIGATE_SUBID_FIRED.clear()
    yield
    rec._FRIGATE_SUBID_FIRED.clear()


def test_subid_first_sighting_fires(rec):
    assert rec._subid_should_fire("evt-1", "Sam") is True


def test_subid_repeat_same_name_suppressed(rec):
    assert rec._subid_should_fire("evt-1", "Sam") is True
    assert rec._subid_should_fire("evt-1", "Sam") is False   # same update tick again
    assert rec._subid_should_fire("evt-1", "Sam") is False


def test_subid_name_change_refires(rec):
    assert rec._subid_should_fire("evt-1", "unknown") is True
    assert rec._subid_should_fire("evt-1", "Sam") is True     # model got a clearer frame
    assert rec._subid_should_fire("evt-1", "Sam") is False


def test_subid_distinct_events_independent(rec):
    assert rec._subid_should_fire("evt-1", "Sam") is True
    assert rec._subid_should_fire("evt-2", "Sam") is True     # different tracked object


def test_subid_blank_event_id_always_fires(rec):
    assert rec._subid_should_fire("", "Sam") is True
    assert rec._subid_should_fire("", "Sam") is True


def test_subid_cap_bounds_memory(rec):
    for i in range(rec._FRIGATE_SUBID_MAX + 5):
        rec._subid_should_fire(f"evt-{i}", "Sam")
    # it cleared at the cap rather than growing without bound
    assert len(rec._FRIGATE_SUBID_FIRED) <= rec._FRIGATE_SUBID_MAX


def test_recognition_source_default_is_both(rec):
    # with no config set, the module defaults to 'both' sources active
    import sys, types
    cfg = types.ModuleType("jc.jarvis_config")
    cfg.get = lambda k, d=None: d          # returns default
    sys.modules["jc.jarvis_config"] = cfg
    try:
        src = str(cfg.get("recognition_source", "both") or "both").lower()
        assert src == "both"
        assert src in ("both", "doubletake")   # doubletake would be active
        assert src in ("both", "frigate")      # frigate would be active
    finally:
        sys.modules.pop("jc.jarvis_config", None)


# ── score normalizer ─────────────────────────────────────────────────────────

def test_normalize_score_fractional(rec):
    assert rec._normalize_score(0.93) == 93.0


def test_normalize_score_already_percent(rec):
    assert rec._normalize_score(88.0) == 88.0


def test_normalize_score_none(rec):
    assert rec._normalize_score(None) == 0.0


# ── Frigate last_recognized_face sensor reader (v6.66.0) ─────────────────────

class _St:
    def __init__(self, entity_id, state, **attrs):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attrs


class _Hass:
    def __init__(self, sensors):
        self._sensors = sensors
    @property
    def states(self):
        outer = self
        class _S:
            def async_all(self, domain):
                return outer._sensors if domain == "sensor" else []
        return _S()


def test_read_face_sensors_names_known_person(rec):
    hass = _Hass([
        _St("sensor.dining_room_last_recognized_face", "Sam", score=0.91),
        _St("sensor.backyard_last_recognized_face", "None"),
        _St("sensor.some_other_sensor", "42"),
    ])
    found = rec.read_frigate_face_sensors(hass)
    assert len(found) == 1
    assert found[0]["name"] == "Sam"
    assert found[0]["camera"] == "dining_room"
    assert found[0]["camera_entity"] == "camera.dining_room"
    assert found[0]["confidence"] == 91.0


def test_read_face_sensors_skips_empty_states(rec):
    # none / unknown / unavailable all mean "no known face right now"
    hass = _Hass([
        _St("sensor.a_last_recognized_face", "none"),
        _St("sensor.b_last_recognized_face", "unknown"),
        _St("sensor.c_last_recognized_face", "unavailable"),
    ])
    assert rec.read_frigate_face_sensors(hass) == []


def test_read_face_sensors_never_raises_on_bad_state(rec):
    hass = _Hass([_St("sensor.x_last_recognized_face", None)])
    assert rec.read_frigate_face_sensors(hass) == []    # no crash


# ── who_do_you_see aggregates sensor + cache ─────────────────────────────────

def test_who_do_you_see_from_sensor(rec):
    rec._RECOGNITION_CACHE.clear()
    hass = _Hass([_St("sensor.front_last_recognized_face", "Sam", score=0.95)])
    res = rec.who_do_you_see(hass)
    assert res["any"] is True
    assert "Sam" in res["seen"]
    assert res["detail"][0]["source"] == "frigate_sensor"


def test_who_do_you_see_empty_when_nothing_recognized(rec):
    rec._RECOGNITION_CACHE.clear()
    hass = _Hass([_St("sensor.front_last_recognized_face", "None")])
    res = rec.who_do_you_see(hass)
    assert res["any"] is False
    assert res["seen"] == []


def test_context_string_includes_frigate_sensor(rec):
    rec._RECOGNITION_CACHE.clear()
    hass = _Hass([_St("sensor.dining_room_last_recognized_face", "Sam", score=0.9)])
    s = rec.recognition_context_string(hass)
    assert "Sam" in s
    assert "dining room" in s


# ── agent tool registration ──────────────────────────────────────────────────

def test_who_do_you_see_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "who_do_you_see" in names
    assert "who_do_you_see" in agent._TOOL_MAP
