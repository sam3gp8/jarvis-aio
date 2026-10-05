"""MCU Phase H (H10): LocalIntentRouter executes through the universal seam.

The local intent router used to actuate with bare ``hass.services.async_call``
(a multi-entity batch call for area intents, a single call for the pronoun
"turn it off" context action). H10 routes both through
``actuation.execute_actuator`` — one canonical per-entity request — so an
intent-router actuation is planned, event-published and journaled like every
other actuation, while its per-entity mutex locks and write-ahead recovery
ledger are preserved.

These load the router through the ``jc`` package harness (not the standalone
loader the pure-matching tests use) so the lazy ``from .. import actuation``
resolves to the same ``jc.actuation`` the seam lives in.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def ir(load):
    # Import through the REAL jc.intent package (its __init__ runs and re-exports
    # LocalIntentRouter) rather than load("intent.intent_router"), which would
    # register a bare jc.intent intermediate and shadow `from .intent import
    # LocalIntentRouter` for other modules (e.g. proactive_audio) later in the
    # suite. jc.__path__ is set by conftest, so normal import machinery works.
    load("actuation")                       # ensure the jc stub tree is set up
    import importlib
    return importlib.import_module("jc.intent.intent_router")


def _hass_with_area(entities: dict[str, str]):
    """FakeHass whose entities all resolve to area 'office'."""
    h = FakeHass()
    for eid, state in entities.items():
        h.states.set(eid, state)
    return h


def _router(ir, hass):
    r = ir.LocalIntentRouter(hass)
    # Everything is in the office for these tests.
    r._area_of = lambda eid: "office"
    return r


async def test_lights_off_routes_each_target_through_seam(ir, load, monkeypatch):
    ev = load("events")
    published = []
    monkeypatch.setattr(ev, "publish", lambda hass, e: published.append(e))
    hass = _hass_with_area({"light.a": "on", "light.b": "on"})
    r = _router(ir, hass)

    res = await r.execute({"intent": "lights_off"}, "office")

    assert res["executed"] is True
    assert set(res["entities"]) == {"light.a", "light.b"}
    # per-entity canonical service calls (not one multi-entity batch)
    assert ("light", "turn_off", {"entity_id": "light.a"}) in hass.service_calls
    assert ("light", "turn_off", {"entity_id": "light.b"}) in hass.service_calls
    # and each published a canonical actuation event
    caps = [(e.data.get("capability"), e.subject) for e in published]
    assert ("light.turn_off", "light.a") in caps
    assert ("light.turn_off", "light.b") in caps


async def test_secure_area_marks_ledger_complete_per_entity(ir):
    completed = []

    class FakeLedger:
        def record_intent(self, eid, desired, action=""):
            return f"txn:{eid}"

        def mark_complete(self, txn):
            completed.append(txn)

    hass = _hass_with_area({"lock.front": "unlocked", "cover.garage": "open"})
    r = ir.LocalIntentRouter(hass, ledger=FakeLedger())
    r._area_of = lambda eid: "office"

    res = await r.execute({"intent": "secure_area"}, "office")

    assert res["executed"] is True
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls
    assert ("cover", "close_cover", {"entity_id": "cover.garage"}) in hass.service_calls
    # write-ahead ledger entries marked complete for the entities that actuated
    assert "txn:lock.front" in completed
    assert "txn:cover.garage" in completed


async def test_context_off_routes_single_entity_through_seam(ir, load, monkeypatch):
    ev = load("events")
    published = []
    monkeypatch.setattr(ev, "publish", lambda hass, e: published.append(e))
    hass = _hass_with_area({"light.desk": "on"})
    r = _router(ir, hass)
    # pronoun "turn it off" resolves to the active light in the area
    r.resolve_active_entity = lambda area_id: ("light.desk", "light")

    res = await r.execute({"intent": "context_off"}, "office")

    assert res["executed"] is True and res["entity_id"] == "light.desk"
    assert ("light", "turn_off", {"entity_id": "light.desk"}) in hass.service_calls
    assert any(e.data.get("capability") == "light.turn_off"
               and e.subject == "light.desk" for e in published)
