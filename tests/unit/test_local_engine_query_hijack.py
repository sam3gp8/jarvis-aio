"""local_engine: query patterns must not hijack actuation commands (bug-audit).

try_local scans _QUERY_PATTERNS BEFORE the command patterns, so a query whose
interrogative prefix was optional would match a bare command word — `open` in
"open the garage door" matching a `what_open` query, `home` in "the home
theater" matching `who_home`, `outside` in "the outside lights" matching
`weather`. The prefixes are now REQUIRED, so commands route to the command path
and genuine questions still answer.

Also pins the set_temp 0° guard (a valid 0 setpoint must not be dropped by a
falsy check).
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def le(load):
    return load("local_engine")


@pytest.fixture(autouse=True)
def _no_confirm(le, monkeypatch):
    # Isolate pattern routing from the confirmation gate.
    monkeypatch.setattr(le, "_needs_confirmation", lambda *a, **k: False)


# ── commands are no longer hijacked by query patterns ───────────────────────

async def test_open_command_not_hijacked_by_what_open(le):
    h = FakeHass()
    h.states.set("cover.garage", "closed", friendly_name="Garage")
    result = await le.try_local(h, "open the garage door", "sir")
    assert result is not None and result.success
    assert ("cover", "open_cover", {"entity_id": "cover.garage"}) in h.service_calls
    assert "closed and secured" not in result.text.lower()   # not the what_open answer


async def test_turn_on_home_theater_not_hijacked_by_who_home(le):
    h = FakeHass()
    h.states.set("light.home_theater", "off", friendly_name="Home Theater")
    result = await le.try_local(h, "turn on the home theater", "sir")
    assert result is not None and result.success
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)


async def test_turn_on_outside_lights_not_hijacked_by_weather(le):
    h = FakeHass()
    h.states.set("light.outside_lights", "off", friendly_name="Outside Lights")
    result = await le.try_local(h, "turn on the outside lights", "sir")
    assert result is not None and result.success
    assert any(c[:2] == ("light", "turn_on") for c in h.service_calls)


# ── genuine queries still answer ─────────────────────────────────────────────

async def test_what_is_open_still_answers(le):
    h = FakeHass()                      # nothing open
    result = await le.try_local(h, "what's open", "sir")
    assert result is not None and result.success
    assert "closed and secured" in result.text.lower()
    assert h.service_calls == []        # a query never actuates


async def test_whos_home_still_answers(le):
    h = FakeHass()
    result = await le.try_local(h, "who's home", "sir")
    assert result is not None and result.success
    assert "home" in result.text.lower()


async def test_whats_the_weather_still_answers(le):
    h = FakeHass()                      # no weather entity → honest no-data answer
    result = await le.try_local(h, "what's the weather", "sir")
    assert result is not None and result.success
    assert ("weather" in result.text.lower()) or ("data" in result.text.lower())


# ── set_temp 0° guard ────────────────────────────────────────────────────────

async def test_set_temp_named_accepts_zero(le):
    h = FakeHass()
    h.states.set("climate.bedroom", "heat", friendly_name="Bedroom")
    result = await le.try_local(h, "set the bedroom temperature to 0", "sir")
    assert result is not None and result.success
    assert any(c[0] == "climate" and c[1] == "set_temperature"
               and c[2].get("temperature") == 0.0 for c in h.service_calls)
