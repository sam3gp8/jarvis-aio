"""Tests for the central periodic-task scheduler (scheduler.py)."""
from __future__ import annotations


class _FakeTracker:
    """Captures registered callbacks + intervals and hands back an unsub that
    records when it's called — a stand-in for async_track_time_interval."""
    def __init__(self):
        self.registered = []   # (callback, interval_seconds)
        self.unsubbed = 0

    def __call__(self, hass, cb, interval):
        self.registered.append((cb, interval.total_seconds()))
        def _unsub():
            self.unsubbed += 1
        return _unsub


def _sched(load, tracker):
    return load("scheduler").JarvisScheduler(hass=None, tracker=tracker)


async def test_add_registers_with_interval(load):
    tr = _FakeTracker()
    s = _sched(load, tr)
    async def cb(now=None):
        pass
    assert s.add("package", 900, cb) is True
    assert len(tr.registered) == 1
    assert tr.registered[0][1] == 900.0
    assert s.task_names() == ["package"]


async def test_duplicate_name_is_skipped(load):
    tr = _FakeTracker()
    s = _sched(load, tr)
    async def cb(now=None):
        pass
    assert s.add("dup", 60, cb) is True
    assert s.add("dup", 60, cb) is False       # reload-safe: no double registration
    assert len(tr.registered) == 1


async def test_run_records_success(load):
    tr = _FakeTracker()
    s = _sched(load, tr)
    hits = {"n": 0}
    async def cb(now=None):
        hits["n"] += 1
    s.add("t", 10, cb)
    wrapped = tr.registered[0][0]
    await wrapped(None)
    st = s.status()[0]
    assert hits["n"] == 1
    assert st["runs"] == 1 and st["errors"] == 0 and st["healthy"] is True
    assert st["last_run"] is not None and st["last_success"] is not None


async def test_run_records_and_swallows_failure(load):
    tr = _FakeTracker()
    s = _sched(load, tr)
    async def boom(now=None):
        raise RuntimeError("sweep exploded")
    s.add("t", 10, boom)
    wrapped = tr.registered[0][0]
    await wrapped(None)          # must NOT raise — an errored tick can't crash the timer
    st = s.status()[0]
    assert st["errors"] == 1 and st["consecutive_errors"] == 1
    assert st["healthy"] is False
    assert "sweep exploded" in st["last_error"]


async def test_consecutive_errors_reset_on_success(load):
    tr = _FakeTracker()
    s = _sched(load, tr)
    state = {"fail": True}
    async def cb(now=None):
        if state["fail"]:
            raise ValueError("x")
    s.add("t", 10, cb)
    wrapped = tr.registered[0][0]
    await wrapped(None)
    await wrapped(None)
    assert s.status()[0]["consecutive_errors"] == 2
    state["fail"] = False
    await wrapped(None)
    st = s.status()[0]
    assert st["consecutive_errors"] == 0 and st["healthy"] is True and st["errors"] == 2


async def test_shutdown_then_readd_is_clean(load):
    """setup -> shutdown -> setup leaves no duplicate tasks and unsubs everything
    (the reload-safety invariant for timers)."""
    tr = _FakeTracker()
    s = _sched(load, tr)
    async def cb(now=None):
        pass
    s.add("a", 10, cb)
    s.add("b", 20, cb)
    assert s.shutdown() == 2
    assert tr.unsubbed == 2
    assert s.task_names() == []
    # re-register after shutdown: same names, no leftovers
    assert s.add("a", 10, cb) is True
    assert s.task_names() == ["a"]


def test_shutdown_is_idempotent(load):
    tr = _FakeTracker()
    s = _sched(load, tr)
    async def cb(now=None):
        pass
    s.add("a", 10, cb)
    assert s.shutdown() == 1
    assert s.shutdown() == 0        # safe to call twice
