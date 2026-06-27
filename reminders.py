"""
JARVIS — Context-aware reminders.

Unlike phone alarms, JARVIS reminders:
  - Check presence before speaking (silent if nobody's home)
  - Respect quiet hours (don't speak 22:00 - 07:00 by default)
  - Can be acknowledged ("got it, Jarvis") to self-silence
  - Persist across HA restarts (stored in SQLite)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, time
from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

from .presence import get_presence_summary
from .tts_helper import async_announce

_LOGGER = logging.getLogger(__name__)

DB_PATH = Path("/config/jarvis/reminders.db")
QUIET_START = time(22, 0)
QUIET_END   = time(7, 0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created     TEXT NOT NULL,
    label       TEXT NOT NULL,
    trigger_at  TEXT NOT NULL,  -- ISO timestamp
    repeat      TEXT,            -- 'daily', 'weekly:MON', etc. (optional)
    require_home   INTEGER NOT NULL DEFAULT 1,  -- 1 = only fire when someone home
    respect_quiet  INTEGER NOT NULL DEFAULT 1,  -- 1 = skip during quiet hours
    acknowledged   INTEGER NOT NULL DEFAULT 0,
    last_fired     TEXT
);
CREATE INDEX IF NOT EXISTS idx_trigger_at ON reminders(trigger_at);
"""


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _in_quiet_hours(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    t = now.time()
    if QUIET_START < QUIET_END:
        return QUIET_START <= t <= QUIET_END
    return t >= QUIET_START or t <= QUIET_END


def add_reminder(
    label: str,
    trigger_at: datetime,
    repeat: Optional[str] = None,
    require_home: bool = True,
    respect_quiet: bool = True,
) -> int:
    """Insert a reminder. Returns the new row's id."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                """INSERT INTO reminders (created, label, trigger_at, repeat,
                                          require_home, respect_quiet)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), label, trigger_at.isoformat(),
                 repeat, 1 if require_home else 0, 1 if respect_quiet else 0),
            )
            return cur.lastrowid
    except Exception as exc:
        _LOGGER.warning("JARVIS: reminder insert failed: %s", exc)
        return -1


def acknowledge_reminder(reminder_id: int) -> bool:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE reminders SET acknowledged = 1 WHERE id = ?", (reminder_id,)
            )
        return True
    except Exception:
        return False


def get_due_reminders(now: Optional[datetime] = None) -> list[dict]:
    """Return all reminders whose trigger time has passed and not yet fired."""
    now = now or datetime.now()
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT * FROM reminders
                   WHERE trigger_at <= ?
                     AND acknowledged = 0
                     AND (last_fired IS NULL OR last_fired < trigger_at)""",
                (now.isoformat(),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("JARVIS: reminder read error: %s", exc)
        return []


def mark_fired(reminder_id: int):
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE reminders SET last_fired = ? WHERE id = ?",
                (datetime.now().isoformat(), reminder_id),
            )
    except Exception as exc:
        _LOGGER.debug("JARVIS: mark_fired error: %s", exc)


def _advance_repeating(reminder: dict) -> None:
    """If a reminder has 'repeat', advance its trigger_at to the next occurrence."""
    repeat = reminder.get("repeat")
    if not repeat:
        return
    try:
        current = datetime.fromisoformat(reminder["trigger_at"])
        if repeat == "daily":
            next_time = current + timedelta(days=1)
        elif repeat == "weekly":
            next_time = current + timedelta(days=7)
        elif repeat == "hourly":
            next_time = current + timedelta(hours=1)
        else:
            return
        with _connect() as conn:
            conn.execute(
                "UPDATE reminders SET trigger_at = ?, last_fired = NULL WHERE id = ?",
                (next_time.isoformat(), reminder["id"]),
            )
    except Exception as exc:
        _LOGGER.debug("JARVIS: advance_repeating error: %s", exc)


class ReminderWatcher:
    """Periodic task that fires due reminders via TTS."""

    def __init__(
        self,
        hass: HomeAssistant,
        honorific_getter,
        tts_getter,
        speakers_getter,
    ):
        self.hass = hass
        self._honorific_getter = honorific_getter
        self._tts_getter = tts_getter
        self._speakers_getter = speakers_getter
        self._unsub = None

    async def async_start(self) -> None:
        self._unsub = async_track_time_interval(
            self.hass, self._check, timedelta(seconds=30)
        )
        _LOGGER.info("JARVIS reminders active.")

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _check(self, now) -> None:
        self.hass.async_create_task(self._check_async())

    async def _check_async(self) -> None:
        due = await self.hass.async_add_executor_job(get_due_reminders)
        if not due:
            return

        honorific = self._honorific_getter()
        tts_entity = self._tts_getter()
        speakers = self._speakers_getter()

        presence = get_presence_summary(self.hass)

        for reminder in due:
            # Skip if quiet hours and respect_quiet
            if reminder.get("respect_quiet") and _in_quiet_hours():
                _LOGGER.debug("JARVIS reminder '%s' deferred (quiet hours)", reminder["label"])
                continue
            # Skip if require_home and nobody's home
            if reminder.get("require_home") and not presence.get("anyone_home"):
                _LOGGER.debug("JARVIS reminder '%s' deferred (nobody home)", reminder["label"])
                continue

            text = f"{honorific}, reminder: {reminder['label']}."
            await async_announce(self.hass, text, tts_entity, speakers)
            await self.hass.async_add_executor_job(mark_fired, reminder["id"])
            await self.hass.async_add_executor_job(_advance_repeating, reminder)


# Service handlers

async def async_add_reminder_service(
    hass: HomeAssistant,
    call: ServiceCall,
    honorific: str,
    tts_entity: str | None,
    speakers: list[str],
) -> dict:
    """Service: jarvis.add_reminder"""
    label = call.data["label"]
    trigger_str = call.data["trigger_at"]
    repeat = call.data.get("repeat")
    require_home = call.data.get("require_home", True)
    respect_quiet = call.data.get("respect_quiet", True)

    try:
        trigger_at = datetime.fromisoformat(trigger_str)
    except ValueError:
        return {"success": False, "error": "invalid_datetime"}

    rid = await hass.async_add_executor_job(
        add_reminder, label, trigger_at, repeat, require_home, respect_quiet
    )
    if rid < 0:
        return {"success": False, "error": "db_error"}

    await async_announce(
        hass,
        f"Reminder saved, {honorific}. I'll let you know.",
        tts_entity, speakers,
    )
    return {"success": True, "reminder_id": rid}
