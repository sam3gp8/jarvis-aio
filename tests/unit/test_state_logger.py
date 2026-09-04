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
    cc._PATTERN_LOG_LAST.clear()   # reset per-entity pattern-log rate limit
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
    # v6.77.0: the listener now uses the room-aware quick_identify, which
    # carries confidence as well as the name.
    identity = load("identity")
    monkeypatch.setattr(identity, "quick_identify",
                        lambda hass, area=None: identity.Identification(
                            "Sam", 0.6, "sole_occupant", {"Sam": 0.6}))

    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person, person_confidence FROM state_changes "
            "WHERE entity_id='light.den'"
        ).fetchone()
    assert row is not None and row[0] == "Sam"
    assert row[1] == pytest.approx(0.6)


def test_listener_records_probable_person_below_threshold(cc, core_state, load, monkeypatch):
    """v6.77.0: a clear front-runner that didn't clear the confidence bar is
    recorded WITH low confidence rather than discarded as unknown — this is what
    lets a multi-occupant house build per-person routines at all."""
    identity = load("identity")
    monkeypatch.setattr(identity, "quick_identify",
                        lambda hass, area=None: identity.Identification(
                            identity.UNKNOWN, 0.0, "low_confidence",
                            {"Eliana": 0.42, "Sam": 0.10}))

    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person, person_confidence FROM state_changes "
            "WHERE entity_id='light.den'"
        ).fetchone()
    assert row[0] == "Eliana"          # the clear front-runner is kept
    assert 0 < row[1] < 0.45           # but flagged as uncertain


def test_listener_keeps_unknown_when_candidates_are_tied(cc, core_state, load, monkeypatch):
    """A genuine coin-flip must stay unknown — we don't invent attribution."""
    identity = load("identity")
    monkeypatch.setattr(identity, "quick_identify",
                        lambda hass, area=None: identity.Identification(
                            identity.UNKNOWN, 0.0, "low_confidence",
                            {"Eliana": 0.30, "Sam": 0.28}))

    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person FROM state_changes WHERE entity_id='light.den'"
        ).fetchone()
    assert row[0] == "unknown"


def test_listener_records_clear_leader_as_best_guess(cc, core_state, load, monkeypatch):
    """A clear leader below the certainty bar is recorded as a low-confidence
    best-guess (v7.9.0) so per-person routines accumulate and firm up."""
    identity = load("identity")
    monkeypatch.setattr(identity, "quick_identify",
                        lambda hass, area=None: identity.Identification(
                            identity.UNKNOWN, 0.0, "low_confidence",
                            {"Sam": 0.30, "Eliana": 0.15}))

    ev = _event(cc, "light.den", "off", "on")
    cc._on_state_changed(ev)

    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        row = conn.execute(
            "SELECT person, person_confidence FROM state_changes "
            "WHERE entity_id='light.den'"
        ).fetchone()
    assert row[0] == "Sam"
    assert 0.0 < row[1] <= 0.44


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


def test_log_state_change_skips_noisy_domain_by_default(core_state):
    """binary_sensor/device_tracker are excluded from pattern learning by default."""
    core_state.state_logger.log_state_change("binary_sensor.garage_door_1", "off", "on")
    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        rows = conn.execute(
            "SELECT 1 FROM state_changes WHERE entity_id='binary_sensor.garage_door_1'"
        ).fetchall()
    assert rows == []


def test_log_state_change_force_include_records_noisy_domain(core_state):
    """force_include (the user opt-in) records a normally-skipped entity (v7.11.0)."""
    core_state.state_logger.log_state_change(
        "binary_sensor.garage_door_1", "off", "on", force_include=True)
    with sqlite3.connect(core_state.state_logger._db_path) as conn:
        rows = conn.execute(
            "SELECT 1 FROM state_changes WHERE entity_id='binary_sensor.garage_door_1'"
        ).fetchall()
    assert len(rows) == 1
