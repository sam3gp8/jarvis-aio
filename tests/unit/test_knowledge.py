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


# ── provenance shadow (#237) ───────────────────────────────────────────────────

def test_all_facts_emits_provenance_shadow_without_changing_rows(knowledge):
    knowledge.remember("coffee", "oat milk", subject="Sam",
                       source="stated", confidence=0.9, now=1000.0)
    facts = knowledge.all_facts(now=1000.0)
    assert len(facts) == 1 and facts[0]["value"] == "oat milk"
    # Kill-switch off → still exactly the same rows (shadow is observe-only).
    knowledge.PROVENANCE_SHADOW = False
    try:
        assert knowledge.all_facts(now=1000.0) == facts
    finally:
        knowledge.PROVENANCE_SHADOW = True


def test_emit_provenance_shadow_is_defensive(knowledge):
    # Mixed good/bad rows must never raise from the log-only shadow path.
    knowledge._emit_provenance_shadow(
        [{"value": 1, "source": "x", "confidence": 0.5}, "not-a-dict", None])
    knowledge._emit_provenance_shadow([])
    knowledge._emit_provenance_shadow(None)


# ── knowledge-graph shadow (roadmap Phase T) ──────────────────────────────────
# all_facts() also folds facts (+ relations) into a kernel.graph.KnowledgeGraph
# and logs a one-line summary. Observe-only: the returned rows are unchanged.

def test_all_facts_graph_shadow_is_behaviour_preserving(knowledge, caplog):
    import logging
    knowledge.remember("trash day", "Tuesday", kind="fact", now=1000.0)
    with caplog.at_level(logging.DEBUG):
        facts = knowledge.all_facts()
    assert len(facts) == 1 and facts[0]["key"] == "trash day"   # rows unchanged
    assert any("graph(shadow)" in r.message for r in caplog.records)


def test_graph_shadow_never_breaks_all_facts(knowledge, monkeypatch):
    # A failing relations read inside the shadow emit must not affect all_facts.
    def boom(*a, **k):
        raise RuntimeError("related down")
    monkeypatch.setattr(knowledge, "related", boom)
    knowledge.remember("k", "v", kind="fact", now=1000.0)
    assert len(knowledge.all_facts()) == 1


# ── knowledge-graph enforce path (roadmap Phase T, gated off by default) ──────
# _graph_expand_facts does 1-hop relation expansion; prompt_block_async uses it
# only when KNOWLEDGE_GRAPH_ENFORCE is on (default off = plain recall).

def test_graph_expand_facts_pulls_related_entity(knowledge):
    knowledge.remember("role", "resident", subject="sam", now=1000.0)
    knowledge.remember("color", "red", subject="car", now=1000.0)
    knowledge.relate("sam", "owns", "car", now=1000.0)
    seed = [f for f in knowledge.all_facts() if f["subject"] == "sam"]
    out = knowledge._graph_expand_facts(seed)
    subjects = {f["subject"] for f in out}
    assert "sam" in subjects and "car" in subjects    # 1-hop expansion over the edge
    assert out[0]["subject"] == "sam"                 # seed first, preserved


def test_graph_expand_facts_respects_cap(knowledge, monkeypatch):
    monkeypatch.setattr(knowledge, "_GRAPH_EXPAND_CAP", 1)
    knowledge.remember("role", "resident", subject="sam", now=1000.0)
    knowledge.remember("a", "1", subject="car", now=1000.0)
    knowledge.remember("b", "2", subject="car", now=1001.0)
    knowledge.relate("sam", "owns", "car", now=1000.0)
    seed = [f for f in knowledge.all_facts() if f["subject"] == "sam"]
    out = knowledge._graph_expand_facts(seed)
    assert len([f for f in out if f["subject"] == "car"]) == 1   # capped


def test_graph_expand_facts_defensive(knowledge, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("related down")
    monkeypatch.setattr(knowledge, "related", boom)
    seed = [{"subject": "sam", "key": "role", "value": "resident",
             "source": "stated", "confidence": 1.0}]
    assert knowledge._graph_expand_facts(seed) == seed           # unchanged, no raise


def test_prompt_block_enforce_off_is_plain_recall(knowledge):
    import asyncio
    from fakes import FakeHass
    knowledge.remember("role", "resident", subject="sam", now=1000.0)
    knowledge.remember("color", "red", subject="car", now=1000.0)
    knowledge.relate("sam", "owns", "car", now=1000.0)
    # default KNOWLEDGE_GRAPH_ENFORCE is False → block is plain recall, no expansion
    block = asyncio.run(knowledge.prompt_block_async(FakeHass(), "", subject="sam"))
    assert "resident" in block and "red" not in block


def test_prompt_block_enforce_on_expands_one_hop(knowledge, monkeypatch):
    import asyncio
    from fakes import FakeHass
    monkeypatch.setattr(knowledge, "KNOWLEDGE_GRAPH_ENFORCE", True)
    knowledge.remember("role", "resident", subject="sam", now=1000.0)
    knowledge.remember("color", "red", subject="car", now=1000.0)
    knowledge.relate("sam", "owns", "car", now=1000.0)
    block = asyncio.run(knowledge.prompt_block_async(FakeHass(), "", subject="sam"))
    assert "resident" in block and "red" in block     # car facts pulled in 1 hop


# ── Phase T parity: measure the would-be 1-hop expansion, observe-only ────────

def test_graph_parity_logs_would_add_without_expanding(knowledge, monkeypatch, caplog):
    import asyncio
    import logging
    from fakes import FakeHass
    # Enforce OFF (default), parity ON → the block stays flat recall but the
    # parity line reports what the graph expansion would have added.
    monkeypatch.setattr(knowledge, "KNOWLEDGE_GRAPH_ENFORCE", False)
    monkeypatch.setattr(knowledge, "GRAPH_PARITY", True)
    knowledge.remember("role", "resident", subject="sam", now=1000.0)
    knowledge.remember("color", "red", subject="car", now=1000.0)
    knowledge.relate("sam", "owns", "car", now=1000.0)
    with caplog.at_level(logging.DEBUG):
        block = asyncio.run(knowledge.prompt_block_async(FakeHass(), "", subject="sam"))
    # Block unchanged (observe-only): the related car fact is NOT injected.
    assert "resident" in block and "red" not in block
    parity = [r.message for r in caplog.records if "graph(parity)" in r.message]
    assert parity and "would add 1" in parity[-1]


def test_graph_parity_kill_switch(knowledge, monkeypatch, caplog):
    import asyncio
    import logging
    from fakes import FakeHass
    monkeypatch.setattr(knowledge, "KNOWLEDGE_GRAPH_ENFORCE", False)
    monkeypatch.setattr(knowledge, "GRAPH_PARITY", False)
    knowledge.remember("role", "resident", subject="sam", now=1000.0)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(knowledge.prompt_block_async(FakeHass(), "", subject="sam"))
    assert not [r for r in caplog.records if "graph(parity)" in r.message]


def test_log_graph_parity_counts_delta(knowledge, caplog):
    import logging
    seed = [{"subject": "sam", "key": "role"}]
    expanded = seed + [{"subject": "car", "key": "color"},
                       {"subject": "car", "key": "year"}]
    with caplog.at_level(logging.DEBUG):
        knowledge._log_graph_parity(seed, expanded)
    msg = [r.message for r in caplog.records if "graph(parity)" in r.message][-1]
    assert "recall=1" in msg and "would add 2" in msg and "total 3" in msg
