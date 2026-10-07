"""#254 — camera-derived presence/occupancy must require a *person*, not a
vehicle or an incidental object.

Frigate/ONVIF expose a per-object-class binary_sensor for every camera
(``binary_sensor.garage_car_occupancy``, ``…_dog_motion``, …) alongside the
per-person one. Those non-person sensors turn ``on`` for a parked car, a
passing animal or a delivered package — which previously made JARVIS treat the
area as *occupied by a human* (discussion #221, reported by @televisorsaal-ai).

The fix is a conservative classifier, ``entity_filter.is_nonperson_object_sensor``,
applied at the human-presence paths (``presence.get_presence_summary`` rooms and
``audio_routing`` occupancy). The person case — and ordinary room occupancy /
mmWave presence sensors — is unchanged. The pattern-learning / automation engine
deliberately still sees car sensors, so it is NOT touched here.
"""
from fakes import FakeHass


# ── the pure classifier ──────────────────────────────────────────────────────

def test_nonperson_sensor_classifier(load):
    ef = load("entity_filter")
    f = ef.is_nonperson_object_sensor

    # Frigate per-object NON-person sensors → not a human present.
    assert f("binary_sensor.garage_car_occupancy", "Garage Car Occupancy")
    assert f("binary_sensor.bay_2_car_occupancy")
    assert f("binary_sensor.driveway_dog_motion", "Driveway Dog Motion")
    assert f("binary_sensor.front_doorbell_package_detected")
    assert f("binary_sensor.yard_animal_presence")
    assert f("binary_sensor.street_truck_occupancy")
    assert f("binary_sensor.bay_car")                 # label as the final token
    assert f(None, "Garage Car Occupancy")            # name-only classification

    # Person sensors and ordinary room occupancy/presence → a human may be present.
    assert not f("binary_sensor.garage_person_occupancy", "Garage Person Occupancy")
    assert not f("binary_sensor.kitchen_occupancy", "Kitchen Occupancy")
    assert not f("binary_sensor.kitchen_mmwave_presence")
    assert not f("binary_sensor.living_room_presence", "Living Room Presence")
    assert not f("binary_sensor.office_motion", "Office Motion")

    # Conservative: a non-person word that is NOT in the object-sensor shape
    # (not before a presence suffix, not the final token) must NOT match, so a
    # room that merely contains such a word keeps counting.
    assert not f("binary_sensor.cat_room_occupancy", "Cat Room Occupancy")
    assert not f("", "")
    assert not f(None, None)


# ── presence.get_presence_summary rooms ──────────────────────────────────────

def test_presence_summary_skips_car_occupancy(load):
    presence, const = load("presence"), load("const")
    h = FakeHass()
    h.data = {const.DOMAIN: {"e1": {"runtime_config": {}}}}
    h.states.set("binary_sensor.kitchen_presence", "on",
                 device_class="occupancy", friendly_name="Kitchen Presence")
    h.states.set("binary_sensor.garage_car_occupancy", "on",
                 device_class="occupancy", friendly_name="Garage Car Occupancy")
    rooms = presence.get_presence_summary(h).get("rooms", {})
    assert "kitchen" in rooms
    # The car sensor must not register the garage as a human-occupied room.
    assert not any("car" in r for r in rooms)


def test_presence_summary_keeps_person_occupancy(load):
    presence, const = load("presence"), load("const")
    h = FakeHass()
    h.data = {const.DOMAIN: {"e1": {"runtime_config": {}}}}
    h.states.set("binary_sensor.garage_person_occupancy", "on",
                 device_class="occupancy", friendly_name="Garage Person Occupancy")
    rooms = presence.get_presence_summary(h).get("rooms", {})
    assert rooms, "a person-occupancy sensor must still mark the area occupied"


# ── audio_routing.anyone_home ────────────────────────────────────────────────

def test_anyone_home_ignores_parked_car(load):
    routing = load("audio_routing")
    h = FakeHass()
    # Only signal is a car in the garage → nobody is actually home.
    h.states.set("binary_sensor.garage_car_occupancy", "on",
                 device_class="occupancy", friendly_name="Garage Car Occupancy")
    assert routing.anyone_home(h) is False


def test_anyone_home_true_for_real_presence(load):
    routing = load("audio_routing")
    h = FakeHass()
    h.states.set("binary_sensor.kitchen_presence", "on",
                 device_class="occupancy", friendly_name="Kitchen Presence")
    assert routing.anyone_home(h) is True


def test_anyone_home_true_for_person_entity(load):
    routing = load("audio_routing")
    h = FakeHass()
    # A car sensor on, but a person is genuinely home → still home.
    h.states.set("binary_sensor.garage_car_occupancy", "on",
                 device_class="occupancy", friendly_name="Garage Car Occupancy")
    h.states.set("person.sam", "home")
    assert routing.anyone_home(h) is True
