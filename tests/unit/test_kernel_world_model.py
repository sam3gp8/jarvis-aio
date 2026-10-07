"""Tests for the world-model facade (kernel Phase 2).

HA-state reads are checked for *parity* against a FakeHass's raw states; the
knowledge / presence / scene sources are reached through module-level seams that
these tests monkeypatch, so no real store or Home Assistant is needed.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def wm_mod(load):
    return load("kernel.world_model")


@pytest.fixture
def hass():
    h = FakeHass()
    h.states.set("light.kitchen", "on", friendly_name="Kitchen Light", area_id="kitchen")
    h.states.set("light.hall", "off", friendly_name="Hall Light", area_id="hall")
    h.states.set("binary_sensor.front_door", "off", area_id="porch", device_class="door")
    h.states.set("sensor.temp", "21", area="kitchen")  # area (not area_id) fallback
    return h


# ── HA-state parity ───────────────────────────────────────────────────────────
def test_device_matches_raw_state(wm_mod, hass):
    wm = wm_mod.WorldModel(hass)
    dev = wm.device("light.kitchen")
    raw = hass.states.get("light.kitchen")
    assert dev["entity_id"] == raw.entity_id
    assert dev["state"] == raw.state
    assert dev["attributes"] == dict(raw.attributes)
    assert dev["domain"] == "light"
    assert dev["name"] == "Kitchen Light"
    assert dev["area"] == "kitchen"


def test_device_missing_is_none(wm_mod, hass):
    assert wm_mod.WorldModel(hass).device("light.nope") is None


def test_devices_parity_with_async_all(wm_mod, hass):
    wm = wm_mod.WorldModel(hass)
    facade_ids = {d["entity_id"] for d in wm.devices()}
    raw_ids = {s.entity_id for s in hass.states.async_all()}
    assert facade_ids == raw_ids


def test_devices_domain_filter_parity(wm_mod, hass):
    wm = wm_mod.WorldModel(hass)
    facade = {d["entity_id"] for d in wm.devices(domain="light")}
    raw = {s.entity_id for s in hass.states.async_all("light")}
    assert facade == raw == {"light.kitchen", "light.hall"}


def test_devices_area_filter(wm_mod, hass):
    wm = wm_mod.WorldModel(hass)
    kitchen = {d["entity_id"] for d in wm.devices(area="kitchen")}
    assert kitchen == {"light.kitchen", "sensor.temp"}  # area_id and area fallback


def test_rooms_are_distinct_sorted_areas(wm_mod, hass):
    assert wm_mod.WorldModel(hass).rooms() == ["hall", "kitchen", "porch"]


def test_area_attribute_fallback(wm_mod, hass):
    # entity_area (registry) fails under FakeHass → falls back to state attribute.
    dev = wm_mod.WorldModel(hass).device("sensor.temp")
    assert dev["area"] == "kitchen"


# ── delegated sources (seams monkeypatched) ───────────────────────────────────
def test_people_canonicalizes_presence(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_presence_summary", lambda h: {
        "people": [
            {"name": "Sam", "state": "home", "area": "kitchen"},
            {"name": "Alex", "state": "not_home"},
        ]})
    people = wm_mod.WorldModel(hass).people()
    assert people == [
        {"name": "Sam", "state": "home", "home": True, "area": "kitchen"},
        {"name": "Alex", "state": "not_home", "home": False, "area": None},
    ]


def test_people_best_effort_on_error(wm_mod, hass, monkeypatch):
    def _boom(h):
        raise RuntimeError("presence down")
    monkeypatch.setattr(wm_mod, "_presence_summary", _boom)
    assert wm_mod.WorldModel(hass).people() == []


def test_person_in_delegates(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_quick_person", lambda h, area: "Sam" if area == "kitchen" else "")
    wm = wm_mod.WorldModel(hass)
    assert wm.person_in("kitchen") == "Sam"
    assert wm.person_in("garage") is None   # empty string → None


def test_facts_and_relationships_delegate(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_all_facts", lambda subject: [{"subject": subject, "text": "runs cold"}])
    monkeypatch.setattr(wm_mod, "_related",
                        lambda s, o, p: [{"subject": s, "predicate": "owns", "object": "jeep"}])
    wm = wm_mod.WorldModel(hass)
    assert wm.facts("Sam") == [{"subject": "Sam", "text": "runs cold"}]
    rel = wm.relationships("Sam", predicate="owns")
    assert rel == [{"subject": "Sam", "predicate": "owns", "object": "jeep"}]


def test_relationships_best_effort_on_error(wm_mod, hass, monkeypatch):
    def _boom(s, o, p):
        raise RuntimeError("db down")
    monkeypatch.setattr(wm_mod, "_related", _boom)
    assert wm_mod.WorldModel(hass).relationships("Sam") == []


# ── provenance shadow (#237) ───────────────────────────────────────────────────

def test_provenances_wrap_facts(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_all_facts", lambda subject: [
        {"subject": "garage", "key": "occupied", "value": False,
         "confidence": 0.8, "source": "camera"},
        {"subject": "Sam", "key": "coffee", "value": "oat milk",
         "confidence": 1.0, "source": "stated"},
    ])
    recs = wm_mod.WorldModel(hass).provenances()
    assert len(recs) == 2
    assert recs[0].value is False and recs[0].source == "camera"
    assert abs(recs[0].confidence - 0.8) < 1e-9
    assert recs[1].value == "oat milk" and recs[1].source == "stated"


def test_provenances_skip_bad_rows_and_default_source(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_all_facts", lambda subject: [
        "not-a-dict", {"key": "k", "value": 1},   # no source → "knowledge"
    ])
    recs = wm_mod.WorldModel(hass).provenances()
    assert len(recs) == 1 and recs[0].source == "knowledge"


def test_provenances_best_effort_on_error(wm_mod, hass, monkeypatch):
    def _boom(subject):
        raise RuntimeError("db down")
    monkeypatch.setattr(wm_mod, "_all_facts", _boom)
    assert wm_mod.WorldModel(hass).provenances() == []


def test_facts_shadow_does_not_change_rows(wm_mod, hass, monkeypatch):
    rows = [{"subject": "s", "key": "k", "value": 1, "confidence": 0.5, "source": "x"}]
    monkeypatch.setattr(wm_mod, "_all_facts", lambda subject: list(rows))
    # Shadow emission on by default; facts() must still return the rows unchanged.
    assert wm_mod.WorldModel(hass).facts("s") == rows
    # And with the kill-switch off, still unchanged.
    monkeypatch.setattr(wm_mod, "PROVENANCE_SHADOW", False)
    assert wm_mod.WorldModel(hass).facts("s") == rows


def test_last_seen_delegates(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_where_last_seen",
                        lambda term: {"term": term, "camera": "porch", "ts": 123.0})
    out = wm_mod.WorldModel(hass).last_seen("keys")
    assert out["camera"] == "porch" and out["term"] == "keys"


def test_person_home_detection_parity(wm_mod, hass):
    """MCU Phase C (C1): cognitive_core reads 'anyone home' via
    WorldModel.devices('person'); it must match the raw person states."""
    hass.states.set("person.sam", "home")
    hass.states.set("person.alex", "not_home")
    wm = wm_mod.WorldModel(hass)
    people = wm.devices(domain="person")
    assert {d["entity_id"] for d in people} == {"person.sam", "person.alex"}
    assert any(d["state"] == "home" for d in people) is True
    # raw-source parity
    raw_home = any(s.state == "home" for s in hass.states.async_all("person"))
    assert any(d["state"] == "home" for d in people) == raw_home
