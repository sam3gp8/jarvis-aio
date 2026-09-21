"""Integration tests for the async analyze() assembly.

Unit tests cover each detector in isolation; these drive the full
PatternAnalyzer.analyze(hass) orchestration with a fake hass, so a wiring
regression — a detector dropped from analyze(), or the sensor-history fetch
breaking the sequence/numeric detectors it now feeds — is caught. The store
methods are monkeypatched so we assert on what analyze() *produces*, not on
downstream persistence (covered elsewhere).
"""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from fakes import FakeHass

_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
    entity_id TEXT NOT NULL, domain TEXT NOT NULL, old_state TEXT,
    new_state TEXT NOT NULL, area_id TEXT, hour INTEGER, day_of_week INTEGER,
    triggered_by TEXT DEFAULT 'system', person TEXT DEFAULT 'unknown'
);
CREATE INDEX idx_sc_ts ON state_changes(timestamp);
CREATE TABLE commands (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
    command TEXT, entity_id TEXT, hour INTEGER, day_of_week INTEGER,
    person TEXT DEFAULT 'unknown');
CREATE TABLE person_patterns (id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT, pattern_type TEXT, description TEXT, entity_ids TEXT,
    confidence REAL, details TEXT, created TEXT, last_seen TEXT);
"""


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


def _ins(conn, eid, st, when):
    conn.execute(
        "INSERT INTO state_changes (timestamp, entity_id, domain, old_state, "
        "new_state, area_id, hour, day_of_week, person) VALUES (?,?,?,?,?,?,?,?,?)",
        (when.isoformat(), eid, eid.split(".")[0], "off", st, "",
         when.hour, when.weekday(), "unknown"))


def _capture(pa, db, monkeypatch):
    an = pa.PatternAnalyzer()
    an._db = db
    stored = []
    monkeypatch.setattr(an, "_store_suggestion",
                        lambda p: (stored.append(p), True)[1])
    monkeypatch.setattr(an, "_store_person_pattern", lambda p: False)
    monkeypatch.setattr(an, "_promote_to_knowledge", lambda pats: 0)
    monkeypatch.setattr(pa, "_learned_threshold_delta", lambda: 0.0)
    return an, stored


async def test_analyze_runs_time_routine_and_sequence(pa, tmp_path, monkeypatch, fake_hass):
    db = str(tmp_path / "a.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=14)
    for d in range(12):
        day = base + timedelta(days=d)
        _ins(conn, "light.porch", "on", day.replace(hour=18, minute=0))       # daily routine
        t = day.replace(hour=20, minute=0, second=17)                          # a sequence
        _ins(conn, "light.hall", "on", t)
        _ins(conn, "light.kitchen", "on", t + timedelta(seconds=45))
    conn.commit(); conn.close()

    an, stored = _capture(pa, db, monkeypatch)
    await an.analyze(fake_hass)

    types = {p.pattern_type for p in stored}
    assert "time_routine" in types, f"time detector didn't run in analyze(); got {types}"
    assert "sequence" in types, f"sequence detector didn't run in analyze(); got {types}"


async def test_analyze_survives_without_recorder(pa, tmp_path, monkeypatch, fake_hass):
    # fake_hass has no recorder → _fetch_numeric_sensor_history returns {}; the
    # sequence detector (which now takes sensor_hist) must still work with {}.
    db = str(tmp_path / "b.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=14)
    for d in range(12):
        t = (base + timedelta(days=d)).replace(hour=20, minute=0, second=17)
        _ins(conn, "light.hall", "on", t)
        _ins(conn, "light.kitchen", "on", t + timedelta(seconds=45))
    conn.commit(); conn.close()

    an, stored = _capture(pa, db, monkeypatch)
    await an.analyze(fake_hass)          # must not raise despite empty sensor_hist
    assert any(p.pattern_type == "sequence" for p in stored)


async def test_analyze_wires_numeric_trigger_from_sensor_history(pa, tmp_path, monkeypatch, fake_hass):
    # Prove the numeric detector is wired into analyze() AND receives the fetched
    # sensor history: a heater that comes on while a temp sensor reads cold.
    db = str(tmp_path / "c.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=16)
    action_eps = []
    for d in range(12):   # 12 occ -> confidence 0.8, clears the store bar
        t = (base + timedelta(days=d)).replace(hour=6, minute=2, second=17)
        _ins(conn, "switch.space_heater", "on", t)
        action_eps.append(t.timestamp())
    conn.commit(); conn.close()

    series = [(base.timestamp() - 3600 + i * 900, 72.0 + (i % 5)) for i in range(200)]
    series += [(ep - 1, 61.0) for ep in action_eps]           # cold right before each

    async def _fake_fetch(hass):
        return {"sensor.living_room_temperature": series}

    an, stored = _capture(pa, db, monkeypatch)
    monkeypatch.setattr(an, "_fetch_numeric_sensor_history", _fake_fetch)
    await an.analyze(fake_hass)

    nt = [p for p in stored if p.pattern_type == "numeric_trigger"]
    assert nt, "numeric_trigger detector not wired into analyze()"
    assert nt[0].details["op"] == "below"
    assert nt[0].details["action"]["entity"] == "switch.space_heater"

class _ThreadedHass(FakeHass):
    """Like FakeHass, but ``async_add_executor_job`` really hops onto a worker
    thread (via the event loop's default ThreadPoolExecutor) instead of
    calling the callable inline. FakeHass.async_add_executor_job runs
    synchronously on the caller's (event-loop) thread, so it can't catch a
    regression of the connection needing check_same_thread=False: this is
    what actually exercises the cross-thread contract."""

    async def async_add_executor_job(self, func, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)


class _QueuedThreadedHass(FakeHass):
    """A single-worker executor whose worker starts deliberately occupied."""

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.release_worker = threading.Event()
        self.blocker_started = threading.Event()
        self.job_submitted = asyncio.Event()
        self.job_finished = asyncio.Event()
        self.blocker = self.executor.submit(self._block_worker)

    def _block_worker(self):
        self.blocker_started.set()
        self.release_worker.wait()

    async def async_add_executor_job(self, func, *args):
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, func, *args)
        self.job_submitted.set()
        try:
            return await future
        finally:
            self.job_finished.set()

    async def shutdown(self):
        self.release_worker.set()
        await asyncio.wrap_future(self.blocker)
        self.executor.shutdown(wait=True)


async def test_analyze_finder_runs_on_a_different_thread(pa, tmp_path, monkeypatch):
    # Regression test for the "SQLite objects created in a thread can only be
    # used in that same thread" error: _connect() opens conn on this test's
    # thread, and _run_all_finders must be able to use (and close) it from a
    # genuinely different worker thread without sqlite3 raising.
    db = str(tmp_path / "d.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=14)
    for d in range(12):
        _ins(conn, "light.porch", "on", (base + timedelta(days=d)).replace(hour=18))
    conn.commit(); conn.close()

    an, stored = _capture(pa, db, monkeypatch)
    creator_thread = threading.current_thread().ident
    worker_threads: set[int] = set()
    real_find_time_routines = an._find_time_routines

    def _spy(conn, person_map):
        worker_threads.add(threading.current_thread().ident)
        return real_find_time_routines(conn, person_map)

    monkeypatch.setattr(an, "_find_time_routines", _spy)

    await an.analyze(_ThreadedHass())

    assert worker_threads, "detector never ran"
    assert creator_thread not in worker_threads, (
        "test setup issue: detector ran on the connection-creating thread, "
        "so this test can't distinguish the fix from its absence"
    )
    assert any(p.pattern_type == "time_routine" for p in stored)


async def test_analyze_cancellation_after_handoff_leaves_cleanup_to_worker(
        pa, tmp_path, monkeypatch):
    db = str(tmp_path / "cancel.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    an, _stored = _capture(pa, db, monkeypatch)
    opened_connections = []
    worker_completed = threading.Event()
    worker_errors = []
    real_connect = an._connect
    real_run_all_finders = an._run_all_finders

    def _capture_connection():
        connection = real_connect()
        opened_connections.append(connection)
        return connection

    def _record_worker_result(*args):
        try:
            return real_run_all_finders(*args)
        except BaseException as exc:
            worker_errors.append(exc)
            raise
        finally:
            worker_completed.set()

    monkeypatch.setattr(an, "_connect", _capture_connection)
    monkeypatch.setattr(an, "_run_all_finders", _record_worker_result)
    hass = _QueuedThreadedHass()

    try:
        assert await asyncio.to_thread(hass.blocker_started.wait, 2)
        analysis = asyncio.create_task(an.analyze(hass))
        await asyncio.wait_for(hass.job_submitted.wait(), timeout=2)

        analysis.cancel()
        with pytest.raises(asyncio.CancelledError):
            await analysis

        hass.release_worker.set()
        assert await asyncio.to_thread(worker_completed.wait, 2)
        await asyncio.wait_for(hass.job_finished.wait(), timeout=2)

        assert not worker_errors
        assert len(opened_connections) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened_connections[0].execute("SELECT 1")
    finally:
        await hass.shutdown()
