"""Tests for conversation memory threading (memory_thread, v6.86.0)."""
import pytest


@pytest.fixture
def mt(load):
    return load("memory_thread")


def test_shape_filters_and_orders(mt):
    rows = [{"role": "user", "content": "hi"},
            {"role": "system", "content": "x"},          # non-conversational dropped
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "   "}]           # empty dropped
    assert mt.shape_history(rows, limit=10) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_shape_truncates_long(mt):
    out = mt.shape_history([{"role": "user", "content": "x" * 1000}], char_cap=50)
    assert len(out[0]["content"]) <= 51 and out[0]["content"].endswith("\u2026")


def test_shape_caps_to_last_n(mt):
    rows = [{"role": "user", "content": str(i)} for i in range(20)]
    out = mt.shape_history(rows, limit=5)
    assert len(out) == 5 and out[-1]["content"] == "19"   # keeps the most recent


def test_shape_handles_junk(mt):
    assert mt.shape_history(None) == []
    assert mt.shape_history([None, "x", 3]) == []          # non-dicts skipped


async def test_load_recent_reads_and_shapes(mt, fake_hass, load, monkeypatch):
    db = load("database")
    monkeypatch.setattr(db, "get_recent_messages",
                        lambda hours, device_id, limit: [
                            {"role": "user", "content": "earlier q"},
                            {"role": "assistant", "content": "earlier a"}])
    assert await mt.load_recent(fake_hass, 48, 12) == [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
    ]


async def test_load_recent_passes_global_scope(mt, fake_hass, load, monkeypatch):
    seen = {}
    db = load("database")

    def _rec(hours, device_id, limit):
        seen.update(hours=hours, device_id=device_id, limit=limit)
        return []
    monkeypatch.setattr(db, "get_recent_messages", _rec)
    await mt.load_recent(fake_hass, 24, 7)
    assert seen == {"hours": 24, "device_id": None, "limit": 7}   # global, bounded


async def test_load_recent_db_error_empty(mt, fake_hass, load, monkeypatch):
    db = load("database")

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "get_recent_messages", boom)
    assert await mt.load_recent(fake_hass, 48, 12) == []


def test_config_reads_jarvis_config(mt, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: {
        "memory_threading_enabled": False, "memory_threading_hours": 24,
        "memory_threading_max": 5}.get(k, d))
    assert mt.config() == (False, 24, 5)


def test_config_defaults(mt, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)     # nothing set → defaults
    assert mt.config() == (mt.DEFAULT_ENABLED, mt.DEFAULT_HOURS, mt.DEFAULT_MAX)
