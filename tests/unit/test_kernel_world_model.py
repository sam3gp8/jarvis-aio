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


# ── knowledge-graph view (roadmap Phase T — shadow) ───────────────────────────
# The facade folds facts()+relationships() into a typed kernel.graph view.
# Shadow: available + unit-tested, consumed by nothing.

def test_knowledge_graph_view(wm_mod, hass, monkeypatch):
    monkeypatch.setattr(wm_mod, "_all_facts", lambda subj: [
        {"subject": "Front Door", "key": "material", "value": "oak",
         "confidence": 0.9, "source": "user"}])
    monkeypatch.setattr(wm_mod, "_related", lambda s, o, p: [
        {"subject": "Front Door", "predicate": "leads_to", "object": "Hallway"}])
    wm = wm_mod.WorldModel(hass)
    g = wm.knowledge_graph()
    assert not g.is_empty()
    assert {e.name for e in wm.entities()} == {"Front Door", "Hallway"}
    rels = wm.relations("Front Door")
    assert len(rels) == 1 and rels[0].predicate == "leads_to"
    assert wm.query(predicate="leads_to")[0].object == "Hallway"
    # the Front Door node carries its attribute
    fd = g.entity("front door")
    assert fd is not None and fd.get("material") == "oak"


def test_knowledge_graph_best_effort_on_error(wm_mod, hass, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")
    monkeypatch.setattr(wm_mod, "_all_facts", boom)
    monkeypatch.setattr(wm_mod, "_related", boom)
    wm = wm_mod.WorldModel(hass)
    assert wm.knowledge_graph().is_empty()
    assert wm.entities() == [] and wm.relations() == [] and wm.query() == []


# ── space & time view (roadmap Phase Q) ───────────────────────────────────────
def test_spatial_graph_view(wm_mod, hass, monkeypatch):
    # residence_graph.room_adjacency shape: {area: set(neighbors)}
    monkeypatch.setattr(wm_mod, "_room_adjacency", lambda cfg: {
        "kitchen": {"hall"}, "hall": {"kitchen", "living"}, "living": {"hall"}})
    wm = wm_mod.WorldModel(hass)
    g = wm.spatial_graph()
    assert not g.is_empty()
    assert [a.name for a in g.areas] == ["hall", "kitchen", "living"]
    assert g.adjacent("kitchen", "hall") and not g.adjacent("kitchen", "living")
    assert g.distance("kitchen", "living") == 2


def test_spatial_graph_best_effort_on_error(wm_mod, hass, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no floor plan")
    monkeypatch.setattr(wm_mod, "_room_adjacency", boom)
    assert wm_mod.WorldModel(hass).spatial_graph().is_empty()


def test_temporal_frame_view(wm_mod, hass, monkeypatch):
    import datetime
    # a fixed Saturday-evening clock
    monkeypatch.setattr(wm_mod, "_now",
                        lambda: datetime.datetime(2026, 10, 10, 19, 30))
    frame = wm_mod.WorldModel(hass).temporal_frame()
    assert frame.is_known and frame.hour == 19
    assert frame.daypart == "evening" and frame.is_weekend
    # an explicit time overrides the clock
    weekday_noon = datetime.datetime(2026, 10, 7, 12, 0)
    f2 = wm_mod.WorldModel(hass).temporal_frame(weekday_noon)
    assert f2.daypart == "midday" and f2.is_daytime and not f2.is_weekend


def test_temporal_frame_best_effort_on_error(wm_mod, hass, monkeypatch):
    def boom():
        raise RuntimeError("no clock")
    monkeypatch.setattr(wm_mod, "_now", boom)
    frame = wm_mod.WorldModel(hass).temporal_frame()
    assert not frame.is_known and frame.daypart == ""
