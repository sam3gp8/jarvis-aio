"""MCU Phase D (D4): situation lifecycle transitions publish canonical JarvisEvents.

The situation mirrors (intrusion / hazard / delivery) now publish a
``situation.transition`` JarvisEvent on the kernel event bus whenever they open
or change state — parity: the event enters the stream (and the ledger records
it), but no consumer reacts yet. These tests cover the `from_situation` builder,
`events.publish_situation`, and the mirrors actually publishing on transition.
"""
import pytest

from fakes import FakeHass


# ── the pure builder ────────────────────────────────────────────────────────────

def test_from_situation_builder(load):
    K = load("kernel.event")
    ev = K.from_situation("intrusion", state="confirmed", action="confirmed",
                          subject="garage", location="garage",
                          situation_id="sit_1", correlation_id="corr_9")
    assert ev.type == K.EVENT_SITUATION
    assert ev.source == "situation"
    assert ev.subject == "garage" and ev.location == "garage"
    assert ev.correlation_id == "corr_9"
    assert ev.data == {"kind": "intrusion", "state": "confirmed",
                       "action": "confirmed", "situation_id": "sit_1"}


# ── wiring a FakeHass to a real bus + collector ─────────────────────────────────

@pytest.fixture
def bus_hass(load):
    bus = load("kernel.event_bus").JarvisEventBus()
    captured = []
    bus.subscribe_all(lambda ev: captured.append(ev))
    hass = FakeHass()
    hass.data["jarvis"] = {"entry1": {"event_bus": bus}}
    return hass, bus, captured


def test_publish_situation_reaches_the_bus(load, bus_hass):
    events = load("events")
    hass, bus, captured = bus_hass

    class _Sit:
        kind = "hazard"
        state = "confirmed"
        subject = "freeze"
        location = None
        id = "sit_x"
        correlation_id = None

    events.publish_situation(hass, _Sit(), action="critical")
    assert len(captured) == 1
    ev = captured[0]
    assert ev.type == "situation.transition"
    assert ev.data["kind"] == "hazard" and ev.data["state"] == "confirmed"
    assert ev.data["action"] == "critical"


def test_publish_situation_no_bus_is_silent(load):
    events = load("events")
    # No bus wired → best-effort no-op, never raises.

    class _Sit:
        kind = "delivery"; state = "resolved"; subject = "camera.p"
        location = None; id = "sit_y"; correlation_id = None

    events.publish_situation(FakeHass(), _Sit(), action="removed")  # must not raise


# ── the mirrors publish on transition ───────────────────────────────────────────

def test_intrusion_mirror_publishes(load, tmp_path, monkeypatch, bus_hass):
    intr = load("intrusion")
    S = load("kernel.situation")
    hass, bus, captured = bus_hass
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(intr, "_situation_mgr", mgr)
    monkeypatch.setattr(intr, "_situation_id", None)

    intr._mirror_situation_sync(hass, "confirmed", breach_area="garage")
    sit_events = [e for e in captured if e.type == "situation.transition"]
    assert sit_events and sit_events[-1].data["kind"] == "intrusion"
    assert sit_events[-1].data["state"] == S.CONFIRMED


def test_hazard_mirror_publishes(load, tmp_path, monkeypatch, bus_hass):
    hz = load("hazard_situation")
    S = load("kernel.situation")
    hass, bus, captured = bus_hass
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(hz, "_mgr", mgr)
    monkeypatch.setattr(hz, "_freeze_situation_id", None)

    hz.mirror_freeze_sync(hass, "critical", "15°F")
    sit_events = [e for e in captured if e.type == "situation.transition"]
    assert sit_events and sit_events[-1].data["kind"] == "hazard"
    assert sit_events[-1].data["state"] == S.CONFIRMED


def test_delivery_mirror_publishes(load, tmp_path, monkeypatch, bus_hass):
    ds = load("delivery_situation")
    S = load("kernel.situation")
    hass, bus, captured = bus_hass
    mgr = S.SituationManager(str(tmp_path / "situations.db"))
    monkeypatch.setattr(ds, "_mgr", mgr)
    monkeypatch.setattr(ds, "_delivery_situation_ids", {})

    ds.mirror_delivery_sync(hass, "camera.porch", "delivered", 1)
    sit_events = [e for e in captured if e.type == "situation.transition"]
    assert sit_events and sit_events[-1].data["kind"] == "delivery"
    assert sit_events[-1].data["state"] == S.INVESTIGATING
    assert sit_events[-1].subject == "camera.porch"
