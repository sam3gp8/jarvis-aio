"""Tests for reminder database connection setup."""
import sys
import types
from unittest.mock import MagicMock

import pytest

if "homeassistant.helpers.event" not in sys.modules:
    event_module = types.ModuleType("homeassistant.helpers.event")
    event_module.async_track_time_interval = MagicMock()
    sys.modules["homeassistant.helpers.event"] = event_module


@pytest.mark.parametrize("failing_operation", ["executescript", "commit"])
def test_connect_closes_connection_when_setup_fails(
    load, tmp_path, monkeypatch, failing_operation
):
    reminders = load("reminders")
    connection = MagicMock()
    setup_error = reminders.sqlite3.OperationalError("setup failed")
    getattr(connection, failing_operation).side_effect = setup_error
    monkeypatch.setattr(reminders, "DB_PATH", tmp_path / "reminders.db")
    monkeypatch.setattr(
        reminders.sqlite3, "connect", MagicMock(return_value=connection)
    )

    with pytest.raises(reminders.sqlite3.OperationalError, match="setup failed"):
        reminders._connect()

    connection.close.assert_called_once_with()


def test_quiet_hours_cover_day_and_overnight(load):
    reminders = load("reminders")
    import datetime
    assert reminders._in_quiet_hours(datetime.datetime(2026, 1, 1, 23, 0)) is True
    assert reminders._in_quiet_hours(datetime.datetime(2026, 1, 1, 6, 0)) is True
    assert reminders._in_quiet_hours(datetime.datetime(2026, 1, 1, 12, 0)) is False


def test_reminder_lifecycle_and_repeats(load, tmp_path, monkeypatch):
    reminders = load("reminders")
    monkeypatch.setattr(reminders, "DB_PATH", tmp_path / "reminders.db")
    import datetime
    trigger = datetime.datetime.now() - datetime.timedelta(minutes=1)
    rid = reminders.add_reminder("take medicine", trigger, repeat="daily")
    assert rid > 0
    due = reminders.get_due_reminders()
    assert due and due[0]["label"] == "take medicine"
    reminders._advance_repeating(due[0])
    assert reminders.get_due_reminders() == []
    assert reminders.acknowledge_reminder(rid) is True
    reminders.mark_fired(rid)


def test_advance_repeating_supports_weekly_hourly_and_invalid(load, tmp_path, monkeypatch):
    reminders = load("reminders")
    monkeypatch.setattr(reminders, "DB_PATH", tmp_path / "reminders.db")
    import datetime
    trigger = datetime.datetime(2026, 1, 1)
    for repeat, delta in (
        ("weekly", datetime.timedelta(days=7)),
        ("hourly", datetime.timedelta(hours=1)),
    ):
        rid = reminders.add_reminder("x", trigger, repeat=repeat)
        reminders._advance_repeating(
            {"id": rid, "trigger_at": trigger.isoformat(), "repeat": repeat}
        )
        with reminders._connect() as connection:
            row = connection.execute(
                "SELECT trigger_at FROM reminders WHERE id = ?", (rid,)
            ).fetchone()
        assert row["trigger_at"] == (trigger + delta).isoformat()
    reminders._advance_repeating({"id": 1, "trigger_at": "bad", "repeat": "daily"})
    reminders._advance_repeating({"id": 1, "trigger_at": "2026-01-01T00:00:00", "repeat": "monthly"})


async def test_add_reminder_service_validates_and_announces(load, fake_hass, monkeypatch):
    reminders = load("reminders")
    from types import SimpleNamespace
    call = SimpleNamespace(data={"label": "x", "trigger_at": "not-a-date"})
    out = await reminders.async_add_reminder_service(fake_hass, call, "Sir", None, [])
    assert out == {"success": False, "error": "invalid_datetime"}
    monkeypatch.setattr(reminders, "add_reminder", lambda *args: -1)
    call.data["trigger_at"] = "2026-01-01T00:00:00"
    out = await reminders.async_add_reminder_service(fake_hass, call, "Sir", None, [])
    assert out == {"success": False, "error": "db_error"}