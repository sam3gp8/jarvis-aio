"""Tests for briefing helpers and the deterministic fallback path."""
import sqlite3
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def b(load):
    return load("briefing")


def test_reads_the_gathered_facts(b):
    out = b._plain_briefing("Good morning", "sir",
                            ["It is Monday.", "Weather: 45F.", "At home: Sam."])
    assert out.startswith("Good morning, sir.")
    assert "Weather: 45F." in out and "At home: Sam." in out


def test_empty_context_says_nothing_notable(b):
    assert "Nothing notable" in b._plain_briefing("Good evening", "sir", [])


def test_skips_blank_lines(b):
    out = b._plain_briefing("Good afternoon", "sir", ["", "  ", "Calendar: Dentist at 09:00."])
    assert out == "Good afternoon, sir. Calendar: Dentist at 09:00."


# ── greeting instruction: localize instead of forcing an English string (#79) ──
import types as _types


def _hass(lang):
    return _types.SimpleNamespace(config=_types.SimpleNamespace(language=lang))


def test_greeting_instruction_english_pins_exact_greeting(b, monkeypatch):
    monkeypatch.setattr(b, "_time_greeting", lambda: "Good evening")
    assert b._greeting_instruction(_hass("en"), "sir") == "Begin with 'Good evening, sir.' "


def test_greeting_instruction_absent_language_is_english(b, monkeypatch):
    monkeypatch.setattr(b, "_time_greeting", lambda: "Good morning")
    assert b._greeting_instruction(_hass(None), "sir") == "Begin with 'Good morning, sir.' "


def test_greeting_instruction_non_english_asks_for_equivalent(b, monkeypatch):
    monkeypatch.setattr(b, "_time_greeting", lambda: "Good evening")
    out = b._greeting_instruction(_hass("de"), "Sir")
    # No forced English greeting string that would fight the language directive.
    assert "Begin with 'Good evening, Sir.'" not in out
    assert "German equivalent of 'Good evening'" in out
    assert "continue in German" in out


def test_gather_weather_open_things_calendar_and_energy(b, fake_hass):
    hass = fake_hass
    hass.states.set("weather.home", "cloudy", temperature=19, temperature_unit="°C",
                   forecast=[{"temperature": 23, "templow": 15}])
    hass.states.set("binary_sensor.front_door", "on", device_class="door",
                   friendly_name="Front Door")
    hass.states.set("binary_sensor.kitchen_window", "on", device_class="window",
                   friendly_name="Kitchen Window")
    hass.states.set("lock.front_gate", "unlocked", friendly_name="Front Gate")
    hass.states.set("calendar.work", "on", message="Team standup",
                   start_time="2026-09-26T09:00:00+00:00")
    hass.states.set("sensor.main_panel", "750", device_class="power",
                   friendly_name="Main Panel", unit_of_measurement="W")
    hass.states.set("sensor.dryer", "120", device_class="power",
                   friendly_name="Dryer", unit_of_measurement="W")

    assert "cloudy" in b._gather_weather(hass)
    assert "Front Door is open" in b._gather_open_things(hass)
    assert "Team standup at 09:00" in b._gather_calendar(hass)[0]
    assert "Main Panel is drawing 750W" in b._gather_energy_anomalies(hass)[0]


def test_gather_overnight_events_reads_db_and_handles_missing_file(b, fake_hass, tmp_path, monkeypatch):
    import jc.paths as paths
    from datetime import timedelta

    missing = tmp_path / "missing" / "conversations.db"
    monkeypatch.setattr(paths, "config_path", lambda *parts, **kwargs: missing)
    assert b._gather_overnight_events(fake_hass, hours=6) == []

    fixed_now = datetime(2026, 9, 26, 12, 0)
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.replace(tzinfo=tz)

    monkeypatch.setattr(b, "datetime", FixedDateTime)
    db_path = tmp_path / "jarvis" / "conversations.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sentinel_events (timestamp TEXT, detail TEXT)")
        conn.execute(
            "INSERT INTO sentinel_events (timestamp, detail) VALUES (?, ?)",
            ((fixed_now - timedelta(hours=8)).isoformat(), "Motion at front door"),
        )
        conn.execute(
            "INSERT INTO sentinel_events (timestamp, detail) VALUES (?, ?)",
            ((fixed_now - timedelta(hours=3, minutes=30)).isoformat(), "Doorbell rang"),
        )
        conn.commit()

    monkeypatch.setattr(paths, "config_path", lambda *parts, **kwargs: db_path)
    rows = b._gather_overnight_events(fake_hass, hours=12)
    assert rows[0].startswith("08:30: Doorbell rang")
    assert "Motion at front door" in rows[1]


@pytest.mark.asyncio
async def test_async_briefing_falls_back_to_plain_facts_when_model_is_empty(b, fake_hass, monkeypatch):
    hass = fake_hass
    hass.states.set("person.sam", "home", friendly_name="Sam")
    hass.states.set("weather.home", "clear", temperature=20, temperature_unit="°C")
    call = SimpleNamespace(data={"announce": True, "include_weather": True, "include_calendar": True,
                                "include_presence": True, "include_events": True,
                                "include_energy": True, "include_hazards": False, "hours": 12})

    recorded = {}

    class DummyClient:
        def chat(self, messages, max_tokens=1500, temperature=0.6):
            recorded["messages"] = messages
            return {"text": ""}

    async def fake_announce(*args, **kwargs):
        recorded["announce"] = args

    def fake_save_message(*args, **kwargs):
        recorded["save_message"] = args

    monkeypatch.setattr(b, "get_presence_summary", lambda hass: {"people": [{"name": "Sam", "state": "home"}]})
    monkeypatch.setattr(b, "save_message", fake_save_message)
    monkeypatch.setattr(b, "async_announce", fake_announce)
    monkeypatch.setattr(b, "build_system_prompt", lambda hass, honorific, task: "system prompt")

    result = await b.async_briefing(
        hass,
        call,
        DummyClient(),
        "sir",
        "tts.piper",
        ["media_player.kitchen"],
    )

    assert result["success"] is True
    assert "Good " in result["briefing"]
    assert "At home: Sam" in result["briefing"]
    assert recorded["save_message"][1].startswith("[Briefing]")
    assert recorded["announce"][1].startswith("Good ")


def test_time_greeting_hits_all_time_branches(b, monkeypatch):
    def fake_now(hour):
        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 26, hour, tzinfo=tz)
        return FakeDateTime

    monkeypatch.setattr(b, "datetime", fake_now(9))
    assert b._time_greeting() == "Good morning"

    monkeypatch.setattr(b, "datetime", fake_now(14))
    assert b._time_greeting() == "Good afternoon"

    monkeypatch.setattr(b, "datetime", fake_now(20))
    assert b._time_greeting() == "Good evening"

    monkeypatch.setattr(b, "datetime", fake_now(23))
    assert b._time_greeting() == "Still awake"


@pytest.mark.asyncio
async def test_async_briefing_collects_hazards_and_snapshot_context(b, fake_hass, monkeypatch):
    hass = fake_hass
    hass.states.set("weather.home", "cloudy", temperature=21, temperature_unit="°C",
                   forecast=[{"temperature": 24, "templow": 18}])
    hass.states.set("person.sam", "home", friendly_name="Sam")
    hass.states.set("person.lee", "not_home", friendly_name="Lee")
    hass.states.set("binary_sensor.front_door", "on", device_class="door", friendly_name="Front Door")
    hass.states.set("binary_sensor.kitchen_window", "on", device_class="window", friendly_name="Kitchen Window")
    hass.states.set("lock.front_gate", "unlocked", friendly_name="Front Gate")
    hass.states.set("calendar.work", "on", message="Team standup", start_time="2026-09-26T09:00:00+00:00")
    hass.states.set("sensor.main_panel", "750", device_class="power", friendly_name="Main Panel",
                   unit_of_measurement="W")
    hass.states.set("sensor.idle", "200", device_class="power", friendly_name="Idle", unit_of_measurement="W")

    pkg = sys.modules["jc"]
    async def fake_scan_now(hass):
        return {
            "ok": True,
            "earthquakes": [{"mag": 4.2, "dist_km": 35, "place": "Near town"}],
            "weather": [{"severity": "Severe", "event": "Storm", "area": "North"}],
            "disasters": [{"category": "wildfire", "title": "Ridge Fire", "dist_km": 12}],
        }

    haz = SimpleNamespace(scan_now=fake_scan_now)
    monkeypatch.setattr(pkg, "hazard_monitor", haz, raising=False)
    proactive = SimpleNamespace(get_snapshot_summary=lambda hours=None: "camera: 2 motion events detected")
    monkeypatch.setattr(pkg, "proactive_briefing", proactive, raising=False)
    monkeypatch.setitem(sys.modules, "jc.proactive_briefing", proactive)

    class DummyClient:
        def chat(self, messages, max_tokens=1500, temperature=0.6):
            return {"text": "Good morning update."}

    call = SimpleNamespace(data={
        "announce": True,
        "include_weather": True,
        "include_calendar": True,
        "include_presence": True,
        "include_events": True,
        "include_energy": True,
        "include_hazards": True,
        "hours": 12,
    })

    recorded = {}

    async def fake_announce(*args, **kwargs):
        recorded["announce"] = args

    def fake_save_message(*args, **kwargs):
        recorded["save_message"] = args

    monkeypatch.setattr(b, "save_message", fake_save_message)
    monkeypatch.setattr(b, "async_announce", fake_announce)
    monkeypatch.setattr(b, "build_system_prompt", lambda hass, honorific, task: "system prompt")

    result = await b.async_briefing(hass, call, DummyClient(), "sir", "tts.piper", ["media_player.kitchen"])

    assert result["announced"] is True
    assert "At home: Sam" in result["context"]
    assert "Away: Lee" in result["context"]
    assert "Currently open/unlocked" in result["context"]
    assert "Calendar: Team standup" in result["context"]
    assert "High power draw: Main Panel is drawing 750W" in result["context"]
    assert "Active hazards nearby" in result["context"]
    assert "camera: 2 motion events detected" in result["context"]
    assert recorded["save_message"][1].startswith("[Briefing] Good morning update.")
    assert recorded["announce"][1].startswith("Good morning update.")


@pytest.mark.asyncio
async def test_async_briefing_handles_provider_and_announce_failures(b, fake_hass, monkeypatch, caplog):
    hass = fake_hass
    call = SimpleNamespace(data={"announce": True, "include_weather": False, "include_calendar": False,
                                "include_presence": False, "include_events": False,
                                "include_energy": False, "include_hazards": False, "hours": 12})

    class BoomClient:
        def chat(self, messages, max_tokens=1500, temperature=0.6):
            raise RuntimeError("provider down")

    monkeypatch.setattr(b, "build_system_prompt", lambda hass, honorific, task: "system prompt")
    monkeypatch.setattr(b, "save_message", lambda *args, **kwargs: None)

    result = await b.async_briefing(hass, call, BoomClient(), "sir", None, [])
    assert "Good " in result["briefing"]
    assert "JARVIS briefing error" in caplog.text

    class OkClient:
        def chat(self, messages, max_tokens=1500, temperature=0.6):
            return {"text": "Ready."}

    async def fake_announce(*args, **kwargs):
        return None

    monkeypatch.setattr(b, "async_announce", fake_announce)
    result2 = await b.async_briefing(hass, call, OkClient(), "sir", "tts.piper", [])
    assert result2["announced"] is False
    assert "no announcement speakers configured" in caplog.text
