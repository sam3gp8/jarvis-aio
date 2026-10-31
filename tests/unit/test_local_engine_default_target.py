"""local_engine: value/area commands resolve a default target (bug-audit follow-up).

Media commands ("pause the music"), volume-set and generic set-temperature
capture a number and/or an optional area — never a device name. They previously
either skipped on a null name (media) or searched for the literal number
(set_temp / volume_set) and failed. They now resolve the target from the captured
area or a sole/active default, and only actuate when that target is unambiguous.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def le(load):
    return load("local_engine")


@pytest.fixture(autouse=True)
def _no_confirm(le, monkeypatch):
    monkeypatch.setattr(le, "_needs_confirmation", lambda *a, **k: False)


# ── media with no area → active / sole default ───────────────────────────────

async def test_pause_music_uses_active_player(le):
    h = FakeHass()
    h.states.set("media_player.lounge", "playing", friendly_name="Lounge")
    h.states.set("media_player.kitchen", "idle", friendly_name="Kitchen")
    result = await le.try_local(h, "pause the music", "sir")
    assert result is not None and result.success
    assert ("media_player", "media_pause", {"entity_id": "media_player.lounge"}) in h.service_calls


async def test_pause_music_uses_sole_player_when_none_active(le):
    h = FakeHass()
    h.states.set("media_player.lounge", "idle", friendly_name="Lounge")
    result = await le.try_local(h, "pause the music", "sir")
    assert result is not None and result.success
    assert ("media_player", "media_pause", {"entity_id": "media_player.lounge"}) in h.service_calls


async def test_pause_music_ambiguous_falls_through(le):
    h = FakeHass()
    h.states.set("media_player.a", "playing")
    h.states.set("media_player.b", "playing")   # two active → ambiguous
    result = await le.try_local(h, "pause the music", "sir")
    assert result is None                        # escalate rather than guess
    assert h.service_calls == []


async def test_pause_music_no_player_falls_through(le):
    h = FakeHass()
    result = await le.try_local(h, "pause the music", "sir")
    assert result is None
    assert h.service_calls == []


# ── volume set → number is the level, not the entity name ────────────────────

async def test_set_volume_uses_number_as_level(le):
    h = FakeHass()
    h.states.set("media_player.lounge", "playing", friendly_name="Lounge")
    result = await le.try_local(h, "set volume to 50", "sir")
    assert result is not None and result.success
    assert ("media_player", "volume_set",
            {"entity_id": "media_player.lounge", "volume_level": 0.5}) in h.service_calls


# ── generic set temperature → number is the setpoint, sole climate resolved ──

async def test_set_temperature_generic_uses_sole_climate(le):
    h = FakeHass()
    h.states.set("climate.main", "heat", friendly_name="Main")
    result = await le.try_local(h, "set the temperature to 72", "sir")
    assert result is not None and result.success
    assert ("climate", "set_temperature",
            {"entity_id": "climate.main", "temperature": 72.0}) in h.service_calls


async def test_set_temperature_generic_ambiguous_falls_through(le):
    h = FakeHass()
    h.states.set("climate.up", "heat")
    h.states.set("climate.down", "heat")         # two climates → ambiguous
    result = await le.try_local(h, "set the temperature to 72", "sir")
    assert result is None
    assert h.service_calls == []


# ── regression guard: the NAMED set-temp path still resolves by name ─────────

async def test_set_temperature_named_still_resolves_by_name(le):
    h = FakeHass()
    h.states.set("climate.bedroom", "heat", friendly_name="Bedroom")
    h.states.set("climate.office", "heat", friendly_name="Office")   # not sole
    result = await le.try_local(h, "set the bedroom temperature to 70", "sir")
    assert result is not None and result.success
    assert ("climate", "set_temperature",
            {"entity_id": "climate.bedroom", "temperature": 70.0}) in h.service_calls
