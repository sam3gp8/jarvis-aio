"""Regression tests for SafetyManager pipe-freeze detection.

Pins the temperature thresholds and de-duplication: critical at <=20°F (auto-act),
a one-shot warning at <=35°F, silence above, and a 1-hour cooldown. Source is
either a weather entity's temperature attribute or an outdoor temperature sensor.
"""
import pytest


@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})


async def _freeze(safety):
    return await safety._check_freeze()


async def test_critical_below_20f_from_weather(safety, fake_hass):
    fake_hass.states.set("weather.home", "cloudy", temperature=15)
    action = await _freeze(safety)
    assert action is not None
    assert action["type"] == "freeze_critical"
    assert action["urgency"] == "critical"
    assert action["auto_act"] is True


async def test_warning_below_35f_from_weather(safety, fake_hass):
    fake_hass.states.set("weather.home", "cloudy", temperature=30)
    action = await _freeze(safety)
    assert action is not None
    assert action["type"] == "freeze_warning"
    assert action["urgency"] == "high"
    assert action["auto_act"] is False


async def test_no_alert_when_warm(safety, fake_hass):
    fake_hass.states.set("weather.home", "sunny", temperature=55)
    assert await _freeze(safety) is None


async def test_outdoor_sensor_is_used_when_no_weather(safety, fake_hass):
    fake_hass.states.set(
        "sensor.outdoor_temp", "18",
        device_class="temperature", friendly_name="Outdoor Temperature")
    action = await _freeze(safety)
    assert action is not None and action["type"] == "freeze_critical"


async def test_indoor_sensor_is_ignored(safety, fake_hass):
    # A temperature sensor without an outdoor hint must not drive freeze logic.
    fake_hass.states.set(
        "sensor.living_room", "16",
        device_class="temperature", friendly_name="Living Room Temperature")
    assert await _freeze(safety) is None


async def test_warning_is_one_shot_then_cooldown(safety, fake_hass):
    fake_hass.states.set("weather.home", "cloudy", temperature=30)
    first = await _freeze(safety)
    second = await _freeze(safety)  # still cold, but already warned + in cooldown
    assert first is not None and first["type"] == "freeze_warning"
    assert second is None
