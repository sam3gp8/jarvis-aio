"""Tests for the weather forecast tool (v6.75.0). JARVIS could previously only
see current conditions, so "what time is it supposed to rain?" had no real
answer and the model fell back to HA's clock intent. These cover the forecast
exec against a mocked weather.get_forecasts service."""
import json

import pytest


@pytest.fixture
def agent(load):
    return load("agent")


class _State:
    def __init__(self, eid, state, **attrs):
        self.entity_id = eid
        self.state = state
        self.attributes = attrs


class _Hass:
    def __init__(self, weather_entities=None, forecast=None, raises=False):
        self._weather = weather_entities or []
        self._forecast = forecast
        self._raises = raises
        self.calls = []
        self.services = self._Services(self)

    class _Services:
        def __init__(self, hass):
            self._h = hass
        async def async_call(self, domain, service, data, blocking=False,
                             return_response=False):
            self._h.calls.append((domain, service, dict(data)))
            if self._h._raises:
                raise RuntimeError("entity does not support this forecast type")
            eid = data.get("entity_id")
            return {eid: {"forecast": self._h._forecast or []}}

    class _States:
        def __init__(self, hass): self._h = hass
        def async_all(self, domain=None): return self._h._weather
        def get(self, eid):
            return next((s for s in self._h._weather if s.entity_id == eid), None)

    @property
    def states(self):
        return _Hass._States(self)


async def test_forecast_returns_hourly_entries(agent):
    fc = [
        {"datetime": "2026-08-08T03:00:00+00:00", "condition": "cloudy",
         "temperature": 71, "precipitation_probability": 20},
        {"datetime": "2026-08-08T05:00:00+00:00", "condition": "rainy",
         "temperature": 68, "precipitation": 0.3, "precipitation_probability": 80},
    ]
    hass = _Hass([_State("weather.home", "cloudy", temperature=72)], forecast=fc)
    out = json.loads(await agent._exec_weather_forecast(hass, {"kind": "hourly"}))
    assert out["entity_id"] == "weather.home"
    assert out["type"] == "hourly"
    assert len(out["forecast"]) == 2
    # the rain hour is present with its time — this is what answers "when"
    rainy = [f for f in out["forecast"] if f["condition"] == "rainy"]
    assert rainy and rainy[0]["datetime"].startswith("2026-08-08T05:00")
    assert rainy[0]["precipitation_probability"] == 80


async def test_forecast_includes_current_conditions(agent):
    hass = _Hass([_State("weather.home", "sunny", temperature=80)], forecast=[])
    out = json.loads(await agent._exec_weather_forecast(hass, {}))
    assert out["current"]["condition"] == "sunny"
    assert out["current"]["temperature"] == 80


async def test_forecast_defaults_to_hourly(agent):
    hass = _Hass([_State("weather.home", "sunny")], forecast=[])
    await agent._exec_weather_forecast(hass, {})
    domain, service, data = hass.calls[0]
    assert (domain, service) == ("weather", "get_forecasts")
    assert data["type"] == "hourly"


async def test_forecast_honors_daily_kind(agent):
    hass = _Hass([_State("weather.home", "sunny")], forecast=[])
    await agent._exec_weather_forecast(hass, {"kind": "daily"})
    assert hass.calls[0][2]["type"] == "daily"


async def test_forecast_no_weather_entity_is_clean_error(agent):
    hass = _Hass([], forecast=[])
    out = json.loads(await agent._exec_weather_forecast(hass, {}))
    assert "error" in out
    assert "no weather entity" in out["error"].lower()


async def test_forecast_falls_back_when_type_unsupported(agent):
    # hourly unsupported → retries daily rather than failing outright
    hass = _Hass([_State("weather.home", "sunny")], forecast=[], raises=True)
    out = json.loads(await agent._exec_weather_forecast(hass, {"kind": "hourly"}))
    # both attempts raise in this stub, so we get a clean error (never an
    # exception bubbling out)
    assert "error" in out
    # it did try the daily fallback
    assert any(c[2].get("type") == "daily" for c in hass.calls)


async def test_forecast_caps_entries(agent):
    fc = [{"datetime": f"2026-08-08T{h:02d}:00:00+00:00", "condition": "sunny",
           "temperature": 70} for h in range(0, 24)] * 3      # 72 entries
    hass = _Hass([_State("weather.home", "sunny")], forecast=fc)
    out = json.loads(await agent._exec_weather_forecast(hass, {}))
    assert len(out["forecast"]) <= 24
