"""Tests for continuity-of-self agency state (roadmap Phase I, I1 — pure).

Real on-disk SQLite via tmp_path (the persistence seam needs a file). No Home
Assistant: capture takes plain extracted data, so everything is deterministic.
"""
import pytest


@pytest.fixture
def ag(load):
    return load("kernel.agency_state")


def _store(ag, tmp_path, **kw):
    return ag.AgencyStore(str(tmp_path / "agency.db"), **kw)


# ── capture ──────────────────────────────────────────────────────────────────
def test_capture_builds_commitments_from_plain_data(ag):
    st = ag.capture(
        mode="home",
        goals=[{"id": "g1", "label": "warm the house", "status": "active"}],
        situations=[{"id": "s1", "label": "porch delivery", "status": "open"}],
        now=lambda: 1000.0,
    )
    assert st.mode == "home"
    assert st.captured_ts == 1000.0
    assert st.schema_version == ag.SCHEMA_VERSION
    assert [c.id for c in st.goals] == ["g1"]
    assert [c.id for c in st.situations] == ["s1"]
    assert st.goals[0].kind == ag.GOAL and st.situations[0].kind == ag.SITUATION


def test_capture_skips_rows_without_an_id(ag):
    st = ag.capture(goals=[{"label": "no id"}, {"id": "", "label": "blank"},
                           {"id": "g2"}])
    assert [c.id for c in st.goals] == ["g2"]


def test_capture_accepts_extra_commitments_of_other_kinds(ag):
    extra = [ag.Commitment(kind="intent", id="i1", label="greet on arrival")]
    st = ag.capture(extra=extra, now=lambda: 1.0)
    assert st.of_kind("intent")[0].id == "i1"
    # extra of the wrong type is ignored, never raises.
    st2 = ag.capture(extra=["not a commitment"], now=lambda: 1.0)
    assert st2.commitments == ()


# ── JSON round-trip ────────────────────────────────────────────────────────────
def test_state_json_round_trips(ag):
    st = ag.capture(
        mode="away",
        goals=[{"id": "g1", "label": "L", "status": "active", "detail": "d"}],
        situations=[{"id": "s1"}],
        now=lambda: 5.0,
    )
    back = ag.AgencyState.from_json(st.to_json())
    assert back == st


# ── store round-trip + newest-wins + prune ────────────────────────────────────
def test_store_save_and_load_latest(ag, tmp_path):
    store = _store(ag, tmp_path)
    assert store.load_latest() is None
    store.save(ag.capture(mode="home", goals=[{"id": "g1"}], now=lambda: 10.0))
    store.save(ag.capture(mode="away", goals=[{"id": "g2"}], now=lambda: 20.0))
    latest = store.load_latest()
    assert latest.mode == "away" and [c.id for c in latest.goals] == ["g2"]


def test_store_survives_reopen(ag, tmp_path):
    db = str(tmp_path / "agency.db")
    ag.AgencyStore(db).save(ag.capture(mode="home", now=lambda: 7.0))
    # A fresh store on the same file reads the persisted snapshot back.
    assert ag.AgencyStore(db).load_latest().mode == "home"


def test_store_prunes_to_keep_newest(ag, tmp_path):
    store = _store(ag, tmp_path, keep=3)
    for i in range(6):
        store.save(ag.capture(goals=[{"id": f"g{i}"}], now=lambda i=i: float(i)))
    assert store.count() == 3
    # The newest survives.
    assert [c.id for c in store.load_latest().goals] == ["g5"]


def test_store_clear(ag, tmp_path):
    store = _store(ag, tmp_path)
    store.save(ag.capture(now=lambda: 1.0))
    store.clear()
    assert store.count() == 0 and store.load_latest() is None


def test_load_latest_skips_snapshot_from_a_newer_schema(ag, tmp_path):
    db = str(tmp_path / "agency.db")
    store = ag.AgencyStore(db)
    store.save(ag.capture(mode="home", now=lambda: 1.0))
    # Inject a snapshot stamped with a future schema version directly, through the
    # module's own persistence seam.
    with ag.persistence.transaction(db) as conn:
        conn.execute(
            "INSERT INTO agency_snapshots (captured_ts, schema_version, payload) "
            "VALUES (?,?,?)", (99.0, ag.SCHEMA_VERSION + 1, "{}"))
    # The future-schema row is newest but unreadable → falls back to ours.
    assert store.load_latest().mode == "home"


# ── continuity summary ─────────────────────────────────────────────────────────
def test_continuity_summary_none(ag):
    assert ag.continuity_summary(None) == "continuity: no prior agency snapshot"


def test_continuity_summary_counts_and_age(ag):
    st = ag.capture(
        mode="home",
        goals=[{"id": "g1"}, {"id": "g2"}],
        situations=[{"id": "s1"}],
        now=lambda: 100.0,
    )
    s = ag.continuity_summary(st, now=lambda: 130.0)
    assert "mode=home" in s and "2 goals" in s and "1 open situation" in s
    assert "captured 30s ago" in s


def test_continuity_summary_empty_state(ag):
    st = ag.capture(now=lambda: 0.0)
    assert "nothing in flight" in ag.continuity_summary(st, now=lambda: 0.0)


# ── reconcile ──────────────────────────────────────────────────────────────────
def test_reconcile_splits_live_from_vanished(ag):
    st = ag.capture(
        goals=[{"id": "g1"}, {"id": "g2"}],
        situations=[{"id": "s1"}, {"id": "s2"}],
        now=lambda: 0.0,
    )
    rep = ag.reconcile(st, live_goal_ids=["g1"], live_situation_ids=["s2"])
    assert {c.id for c in rep.still_live} == {"g1", "s2"}
    assert {c.id for c in rep.vanished} == {"g2", "s1"}
    assert rep.resumable is True
    assert rep.to_dict()["live_count"] == 2 and rep.to_dict()["vanished_count"] == 2


def test_reconcile_keeps_unknown_kinds_live(ag):
    st = ag.capture(extra=[ag.Commitment(kind="intent", id="i1")], now=lambda: 0.0)
    rep = ag.reconcile(st)  # no live ids given
    assert [c.id for c in rep.still_live] == ["i1"] and rep.vanished == ()


def test_reconcile_none_is_empty(ag):
    rep = ag.reconcile(None, live_goal_ids=["g1"])
    assert rep.still_live == () and rep.vanished == () and rep.resumable is False


# ── I-B: cognitive context (pure schema extension) ────────────────────────────

def test_cognitive_context_round_trips(ag):
    cog = ag.CognitiveContext(
        identity="sam", intent="dim the office", plan="light.turn_off office",
        beliefs=("office occupied",), attention=("office",),
        pending_verifications=("office lights off?",))
    st = ag.capture(mode="home", goals=[{"id": "g1"}], cognitive=cog, now=lambda: 9.0)
    back = ag.AgencyState.from_json(st.to_json())
    assert back == st
    assert back.cognitive is not None
    assert back.cognitive.intent == "dim the office"
    assert back.cognitive.beliefs == ("office occupied",)


def test_empty_cognitive_is_dropped_and_byte_identical_to_commitment_only(ag):
    # An empty CognitiveContext must not change the serialized payload (I-A compat).
    plain = ag.capture(mode="away", goals=[{"id": "g1"}], now=lambda: 1.0)
    withempty = ag.capture(mode="away", goals=[{"id": "g1"}],
                           cognitive=ag.CognitiveContext(), now=lambda: 1.0)
    assert withempty.cognitive is None
    assert withempty.to_json() == plain.to_json()
    assert "cognitive" not in plain.to_json()


def test_old_commitment_only_snapshot_still_loads(ag):
    # A payload written before I-B (no "cognitive" key) loads with cognitive=None.
    import json
    raw = json.dumps({"schema_version": 1, "captured_ts": 2.0, "mode": "home",
                      "commitments": [{"kind": "goal", "id": "g1"}]})
    st = ag.AgencyState.from_json(raw)
    assert st.cognitive is None and st.goals[0].id == "g1"


def test_continuity_summary_surfaces_intent_and_plan(ag):
    cog = ag.CognitiveContext(intent="dim the office", plan="turn_off office")
    st = ag.capture(cognitive=cog, now=lambda: 10.0)
    line = ag.continuity_summary(st, now=lambda: 10.0)
    assert "intent=dim the office" in line and "plan=turn_off office" in line


def test_cognitive_context_is_empty(ag):
    assert ag.CognitiveContext().is_empty() is True
    assert ag.CognitiveContext(intent="x").is_empty() is False
    assert ag.CognitiveContext(beliefs=("b",)).is_empty() is False
