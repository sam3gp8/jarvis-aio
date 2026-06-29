"""Regression tests for the StateLedger write-ahead recovery log.

Stdlib-only; tested against tmp_path. Pins intent/complete bookkeeping, pending
detection, reconciliation against device state, compaction, persistence, and
tolerance of a torn final line from a crash mid-write.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sl = _load_standalone("jarvis_state_ledger", "state_ledger.py")


@pytest.fixture
def ledger(tmp_path):
    return sl.StateLedger(path=str(tmp_path / "ledger.jsonl"))


def test_record_then_pending(ledger):
    ledger.record_intent("lock.front_door", "locked")
    pending = ledger.pending_intents()
    assert len(pending) == 1
    assert pending[0]["entity_id"] == "lock.front_door"
    assert pending[0]["desired_state"] == "locked"


def test_complete_clears_pending(ledger):
    txn = ledger.record_intent("lock.front_door", "locked")
    ledger.mark_complete(txn)
    assert ledger.pending_intents() == []


def test_reconcile_flags_mismatch(ledger):
    ledger.record_intent("lock.front_door", "locked")
    disc = ledger.reconcile(lambda eid: "unlocked")  # crash before it locked
    assert len(disc) == 1
    assert disc[0]["actual"] == "unlocked"
    assert disc[0]["desired_state"] == "locked"


def test_reconcile_clean_when_state_matches(ledger):
    ledger.record_intent("lock.front_door", "locked")
    assert ledger.reconcile(lambda eid: "locked") == []


def test_reconcile_ignores_completed(ledger):
    txn = ledger.record_intent("lock.front_door", "locked")
    ledger.mark_complete(txn)
    assert ledger.reconcile(lambda eid: "unlocked") == []  # not outstanding


def test_reconcile_verify_exception_is_mismatch(ledger):
    ledger.record_intent("lock.x", "locked")

    def boom(eid):
        raise RuntimeError("state read failed")

    disc = ledger.reconcile(boom)
    assert len(disc) == 1
    assert disc[0]["actual"] is None


def test_compact_keeps_only_pending(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = sl.StateLedger(path=str(path))
    done = ledger.record_intent("lock.a", "locked")
    ledger.mark_complete(done)
    ledger.record_intent("cover.b", "closed")  # still pending
    ledger.compact()
    records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(records) == 1
    assert records[0]["entity_id"] == "cover.b"
    assert records[0]["op"] == "intent"


def test_persistence_across_instances(tmp_path):
    path = str(tmp_path / "l.jsonl")
    a = sl.StateLedger(path=path)
    a.record_intent("lock.x", "locked")
    b = sl.StateLedger(path=path)
    assert len(b.pending_intents()) == 1


def test_torn_final_line_tolerated(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = sl.StateLedger(path=str(path))
    ledger.record_intent("lock.x", "locked")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"txn": "broken", "op": "inte')  # crash mid-write
    assert len(ledger.pending_intents()) == 1  # good record survives, torn skipped


def test_missing_file_is_empty(tmp_path):
    ledger = sl.StateLedger(path=str(tmp_path / "nope.jsonl"))
    assert ledger.pending_intents() == []
    assert ledger.reconcile(lambda eid: "x") == []
