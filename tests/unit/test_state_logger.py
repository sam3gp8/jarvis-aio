"""Tests for StateLogger (person column + migration) and the state-change
listener's sole-occupant stamping — v6.41.0, groundwork for per-person
routine learning."""
import sqlite3

import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def logger(cc, tmp_path, monkeypatch):
    lg = cc.StateLogger.__new__(cc.StateLogger)
    lg._last_states = {}
    lg._db_path = str(tmp_path / "patterns.db")
    lg._init_db()
    return lg


def test_fresh_db_has_person_column(logger):
    with sqlite3.connect(logger._db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(state_changes)")}
    assert "person" in cols


def test_log_state_change_stores_person(logger):
    logger.log_state_change("light.kitchen", "off", "on", person="Sam")
    with sqlite3.connect(logger._db_path) as conn:
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id = ?",
            ("light.kitchen",),
        ).fetchone()
    assert row[0] == "Sam"


def test_log_state_change_defaults_to_unknown(logger):
    logger.log_state_change("light.kitchen", "off", "on")
    with sqlite3.connect(logger._db_path) as conn:
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id = ?",
            ("light.kitchen",),
        ).fetchone()
    assert row[0] == "unknown"


def test_migration_adds_person_to_existing_db(cc, tmp_path):
    """An install upgrading from pre-6.41 has state_changes without the
    person column — _init_db must migrate it in place, not just for fresh DBs."""
    db_path = tmp_path / "old_patterns.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE state_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, entity_id TEXT NOT NULL,
                domain TEXT NOT NULL, old_state TEXT, new_state TEXT NOT NULL,
                area_id TEXT, hour INTEGER, day_of_week INTEGER,
                triggered_by TEXT DEFAULT 'system'
            );
        """)
        conn.execute(
            "INSERT INTO state_changes (timestamp, entity_id, domain, "
            "old_state, new_state, hour, day_of_week) "
            "VALUES ('2026-01-01T00:00:00','light.x','light','off','on',0,0)")

    lg = cc.StateLogger.__new__(cc.StateLogger)
    lg._last_states = {}
    lg._db_path = str(db_path)
    lg._init_db()  # should migrate, not error

    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(state_changes)")}
        assert "person" in cols
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id='light.x'"
        ).fetchone()
        assert row[0] == "unknown"  # pre-existing rows get the default


# ── listener stamping (sole-occupant only) ───────────────────────────────────

@pytest.fixture
def core_state(cc, fake_hass, monkeypatch, tmp_path):
    core = cc._CoreState()
    core.hass = fake_hass
    core.running = True
    core.state_logger = cc.StateLogger.__new__(cc.StateLogger)
    core.state_logger._last_states = {}
    core.state_logger._db_path = str(tmp_path / "patterns.db")
    core.state_logger._init_db()
    monkeypatch.setattr(cc, "_CORE", core)
    return core


def _event(cc, entity_id, old, new):
    from homeassistant.core import State
    old_state = State(entity_id, old) if old is not None else None
    new_state = State(entity_id, new)
    return cc.Event("state_changed", {
        "entity_id": entity_id, "old_state": old_state, "new_state": new_state,
    })


def test_listener_stamps_unknown_with_no_presence_signal(cc, core_state):
    # No identity.quick_person patch needed — with nothing home, it's a
    # real "unknown" from the resolver, not just an untouched default.
    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id='light.den'"
        ).fetchone()
    assert row is not None and row[0] == "unknown"


def test_listener_stamps_known_person_when_home(cc, core_state, load, monkeypatch):
    identity = load("identity")
    monkeypatch.setattr(identity, "quick_person", lambda hass: "Sam")

    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id='light.den'"
        ).fetchone()
    assert row is not None and row[0] == "Sam"


def test_listener_survives_identity_failure(cc, core_state, load, monkeypatch):
    identity = load("identity")

    def _boom(hass):
        raise RuntimeError("boom")
    monkeypatch.setattr(identity, "quick_person", _boom)

    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)  # must not raise

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id='light.den'"
        ).fetchone()
    assert row is not None and row[0] == "unknown"
