"""Tests for the curated knowledge store (v6.25.0)."""
import sqlite3

import pytest


@pytest.fixture
def knowledge(load):
    return load("knowledge")


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch, knowledge):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "knowledge.db"))
    yield


def test_legacy_schema_migrates_in_deleted_at_column(knowledge):
    # simulate a pre-tombstone DB: the facts table without the deleted_at column
    conn = sqlite3.connect(knowledge.DB_PATH)
    conn.execute(
        """
        CREATE TABLE facts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            kind            TEXT NOT NULL DEFAULT 'fact',
            subject         TEXT NOT NULL DEFAULT 'household',
            key             TEXT NOT NULL,
            value           TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'stated',
            confidence      REAL NOT NULL DEFAULT 1.0,
            salience        REAL NOT NULL DEFAULT 1.0,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL,
            last_referenced REAL,
            expires_at      REAL,
            UNIQUE(subject, key)
        )
        """
    )
    conn.execute(
        "INSERT INTO facts (subject, key, value, created_at, updated_at) "
        "VALUES ('household', 'trash day', 'Tuesday', 1000.0, 1000.0)")
    conn.commit()
    conn.close()

    facts = knowledge.all_facts(now=1000.0)
    assert len(facts) == 1 and facts[0]["key"] == "trash day"
    # the store can now tombstone/revive rows from the pre-migration schema
    assert knowledge.forget(key="trash day", now=1500.0) == 1
    assert knowledge.all_facts() == []


def test_remember_and_all_facts(knowledge):
    f = knowledge.remember("trash day", "Tuesday", kind="fact", now=1000.0)
    assert f and f["key"] == "trash day" and f["value"] == "Tuesday"
    facts = knowledge.all_facts()
    assert len(facts) == 1 and facts[0]["subject"] == "household"


def test_remember_rejects_empty(knowledge):
    assert knowledge.remember("", "x") is None
    assert knowledge.remember("k", "") is None
    assert knowledge.all_facts() == []


def test_upsert_updates_in_place(knowledge):
    knowledge.remember("trash day", "Tuesday", now=1000.0)
    knowledge.remember("trash day", "Wednesday", now=2000.0)
    facts = knowledge.all_facts()
    assert len(facts) == 1
    assert facts[0]["value"] == "Wednesday"


def test_subject_scoping(knowledge):
    knowledge.remember("bedtime", "10pm", subject="primary", now=1000.0)
    knowledge.remember("trash day", "Tuesday", subject="household", now=1000.0)
    assert len(knowledge.all_facts(subject="primary")) == 1
    assert len(knowledge.all_facts(subject="household")) == 1
    assert len(knowledge.all_facts()) == 2


def test_recall_ranks_query_match_first(knowledge):
    knowledge.remember("trash day", "Tuesday", now=1000.0)
    knowledge.remember("recycling day", "Friday", now=1000.0)
    knowledge.remember("favorite color", "teal", now=1000.0)
    hits = knowledge.recall("when is trash", k=3, now=1000.0)
    assert hits and hits[0]["key"] == "trash day"


def test_recall_empty_query_prefers_salient(knowledge):
    knowledge.remember("low", "x", salience=0.2, now=1000.0)
    knowledge.remember("high", "y", salience=5.0, now=1000.0)
    hits = knowledge.recall("", k=2, now=1000.0)
    assert hits[0]["key"] == "high"


def test_recall_drops_nonmatching_query(knowledge):
    knowledge.remember("trash day", "Tuesday", now=1000.0)
    hits = knowledge.recall("quantum chromodynamics", k=5, now=1000.0)
    assert hits == []


def test_expiry_hidden_then_purged(knowledge):
    knowledge.remember("pickup", "3pm", ttl_seconds=100, now=1000.0)
    assert len(knowledge.all_facts(now=1050.0)) == 1
    assert knowledge.all_facts(now=2000.0) == []
    assert knowledge.recall("pickup", now=2000.0) == []
    assert knowledge.purge_expired(now=2000.0) == 1


def test_forget_by_id_and_by_key(knowledge):
    f = knowledge.remember("trash day", "Tuesday", now=1000.0)
    assert knowledge.forget(fact_id=f["id"]) == 1
    assert knowledge.all_facts() == []
    knowledge.remember("bedtime", "10pm", subject="primary", now=1000.0)
    assert knowledge.forget(subject="primary", key="bedtime") == 1
    assert knowledge.all_facts() == []


def test_prompt_block_formats_and_hedges(knowledge):
    knowledge.remember("trash day", "Tuesday", source="stated", confidence=1.0, now=1000.0)
    knowledge.remember("usually wakes", "7am", source="observed", confidence=0.6,
                       subject="primary", now=1000.0)
    block = knowledge.prompt_block(now=1000.0)
    assert "What you know" in block
    assert "trash day: Tuesday" in block
    assert "(~)" in block
    assert "Tuesday (~)" not in block


def test_prompt_block_empty_is_blank(knowledge):
    assert knowledge.prompt_block(now=1000.0) == ""


def test_forget_tombstones_and_blocks_reobservation(knowledge):
    knowledge.remember("light turns on", "around 18:00 most days",
                       source="observed", confidence=0.8, now=1000.0)
    f = knowledge.all_facts()[0]
    assert knowledge.forget(fact_id=f["id"], now=1500.0) == 1
    assert knowledge.all_facts() == []
    # pattern analyzer re-detects the same routine on the next run — must not resurrect it
    result = knowledge.remember("light turns on", "around 18:00 most days",
                                source="observed", confidence=0.9, now=2000.0)
    assert result is None
    assert knowledge.all_facts() == []


def test_forget_tombstone_revived_by_explicit_stated_reteach(knowledge):
    knowledge.remember("trash day", "Tuesday", source="observed", now=1000.0)
    knowledge.forget(key="trash day", now=1500.0)
    assert knowledge.all_facts() == []
    # user explicitly re-teaches it — should revive, unlike a passive observation
    result = knowledge.remember("trash day", "Wednesday", source="stated", now=2000.0)
    assert result and result["value"] == "Wednesday"
    assert len(knowledge.all_facts()) == 1


def test_forget_by_key_only_tombstones_all_subjects(knowledge):
    knowledge.remember("bedtime", "10pm", subject="primary", now=1000.0)
    knowledge.remember("bedtime", "11pm", subject="household", now=1000.0)
    assert knowledge.forget(key="bedtime", now=1500.0) == 2
    assert knowledge.all_facts() == []
    # neither subject's row should be resurrected by passive re-observation
    assert knowledge.remember("bedtime", "10pm", subject="primary",
                              source="observed", now=2000.0) is None
    assert knowledge.remember("bedtime", "11pm", subject="household",
                              source="observed", now=2000.0) is None
    assert knowledge.all_facts() == []


def test_purge_expired_hard_deletes_old_tombstones(knowledge):
    knowledge.remember("trash day", "Tuesday", now=1000.0)
    knowledge.forget(key="trash day", now=1500.0)
    # tombstone not old enough yet
    assert knowledge.purge_expired(now=1500.0 + 86400.0) == 0
    assert knowledge.purge_expired(now=1500.0 + knowledge._TOMBSTONE_TTL + 1.0) == 1


def test_stats_counts_live_only(knowledge):
    knowledge.remember("a", "1", kind="fact", now=1000.0)
    knowledge.remember("b", "2", kind="preference", subject="primary", now=1000.0)
    knowledge.remember("c", "3", ttl_seconds=10, now=1000.0)
    s = knowledge.stats(now=2000.0)
    assert s["total"] == 2
    assert s["by_kind"].get("preference") == 1
