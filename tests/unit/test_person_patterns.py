"""Tests for the per-person routine store (person_patterns, v6.85.0)."""
import json

import pytest


@pytest.fixture
def pp(load):
    load("identity")            # so _normalize resolves
    return load("person_patterns")


def test_store_and_read(pp, tmp_path):
    db = str(tmp_path / "p.db")
    assert pp.store("sam", "time_routine", "start the coffee",
                    data={"hour": 6}, confidence=0.8, occurrences=7, db_path=db) is True
    rows = pp.read(db_path=db)
    assert len(rows) == 1
    r = rows[0]
    assert r["person"] == "sam" and r["description"] == "start the coffee"
    assert r["confidence"] == 0.8 and r["occurrences"] == 7
    assert json.loads(r["data"])["hour"] == 6


def test_upsert_refreshes_in_place(pp, tmp_path):
    db = str(tmp_path / "p.db")
    pp.store("sam", "time_routine", "start the coffee", confidence=0.5, occurrences=5, db_path=db)
    pp.store("sam", "time_routine", "start the coffee", confidence=0.9, occurrences=9, db_path=db)
    rows = pp.read("sam", db_path=db)
    assert len(rows) == 1                              # not duplicated
    assert rows[0]["confidence"] == 0.9 and rows[0]["occurrences"] == 9


def test_normalizes_person(pp, tmp_path):
    db = str(tmp_path / "p.db")
    pp.store("Sam Smith", "time_routine", "x", db_path=db)
    assert pp.read("sam_smith", db_path=db)            # normalized key matches
    assert pp.read(db_path=db)[0]["person"] == "sam_smith"


def test_read_filtered_by_person(pp, tmp_path):
    db = str(tmp_path / "p.db")
    pp.store("sam", "time_routine", "a", db_path=db)
    pp.store("alex", "time_routine", "b", db_path=db)
    assert {r["person"] for r in pp.read("sam", db_path=db)} == {"sam"}
    assert len(pp.read(db_path=db)) == 2


def test_empty_person_rejected(pp, tmp_path):
    assert pp.store("", "t", "d", db_path=str(tmp_path / "p.db")) is False


def test_read_missing_db_empty(pp, tmp_path):
    assert pp.read(db_path=str(tmp_path / "nope.db")) == []


def test_ensure_schema_idempotent(pp, tmp_path):
    db = str(tmp_path / "p.db")
    pp.ensure_schema(db)
    pp.ensure_schema(db)
    assert pp.read(db_path=db) == []
