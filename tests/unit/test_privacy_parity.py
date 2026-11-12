"""MCU Tranche 1 (foundation trust) — privacy gate PARITY against the live requester.

9.90.0 landed the first live consult of ``kernel.privacy`` in SHADOW: it ran
recalled facts through the policy against a *hypothetical* different resident.
This is the parity rung — the recall-for-reply block now runs the facts it is
ABOUT TO DISCLOSE through the policy against the REAL requester identity (the
resolved speaker) and logs how many would be withheld from THAT person. A
non-zero count is a real finding: a cross-subject fact reached this person's
context, which the enforce rung would redact. Observe-only throughout — the
block the model sees is unchanged; enforce stays a later, owner-gated decision.
"""
import logging

import pytest

from fakes import FakeHass

@pytest.fixture
def knowledge(load):
    return load("knowledge")


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch, knowledge):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "knowledge.db"))
    yield


def _fact(subject, key="k", value="v"):
    return {"subject": subject, "key": key, "value": value}


# ── _emit_privacy_parity: the core consult (no DB needed) ────────────────────

def test_parity_all_disclosable_logs_agreement(knowledge, caplog):
    facts = [_fact("alice"), _fact("household"), _fact("alice", key="k2")]
    with caplog.at_level(logging.DEBUG):
        knowledge._emit_privacy_parity(facts, "alice")
    msgs = [r.message for r in caplog.records]
    assert any("privacy(parity)" in m and "disclosable" in m for m in msgs)
    # none withheld → no WARNING
    assert not any(r.levelno >= logging.WARNING and "privacy(parity)" in r.message
                   for r in caplog.records)


def test_parity_cross_subject_warns(knowledge, caplog):
    # A fact about bob reached alice's recall context: the policy would withhold it.
    facts = [_fact("alice"), _fact("bob")]
    with caplog.at_level(logging.DEBUG):
        knowledge._emit_privacy_parity(facts, "alice")
    warns = [r for r in caplog.records
             if r.levelno >= logging.WARNING and "privacy(parity)" in r.message]
    assert warns, "a cross-subject fact must raise the parity warning"
    assert "WITHHELD" in warns[0].message


def test_parity_household_facts_always_disclosable(knowledge, caplog):
    facts = [_fact("household"), _fact("household", key="k2")]
    with caplog.at_level(logging.DEBUG):
        knowledge._emit_privacy_parity(facts, "bob")
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_parity_kill_switch_silences(knowledge, monkeypatch, caplog):
    monkeypatch.setattr(knowledge, "PRIVACY_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        knowledge._emit_privacy_parity([_fact("alice"), _fact("bob")], "alice")
    assert not any("privacy(parity)" in r.message for r in caplog.records)


def test_parity_blank_requester_is_noop(knowledge, caplog):
    with caplog.at_level(logging.DEBUG):
        knowledge._emit_privacy_parity([_fact("alice")], "")
        knowledge._emit_privacy_parity([_fact("alice")], None)
    assert not any("privacy(parity)" in r.message for r in caplog.records)


def test_parity_is_defensive_on_garbage(knowledge):
    # Non-dict rows and an empty list must never raise.
    knowledge._emit_privacy_parity([None, "x", 5], "alice")
    knowledge._emit_privacy_parity([], "alice")
    knowledge._emit_privacy_parity(None, "alice")


# ── prompt_block_async wiring: parity runs, block unchanged ──────────────────

async def test_prompt_block_async_runs_parity_without_changing_block(knowledge, caplog):
    hass = FakeHass()
    # a cross-subject fact in the store; recall (no subjects filter) returns it
    knowledge.remember("project", "apollo", subject="bob")
    with caplog.at_level(logging.DEBUG):
        block_with = await knowledge.prompt_block_async(
            hass, now=2000.0, requester="alice")
        block_without = await knowledge.prompt_block_async(hass, now=2000.0)
    # the parity consult fired for the requester-aware call …
    assert any("privacy(parity)" in r.message for r in caplog.records)
    # … and did not change the block (observe-only): same text either way
    assert block_with == block_without


async def test_prompt_block_async_no_requester_skips_parity(knowledge, caplog):
    hass = FakeHass()
    knowledge.remember("project", "apollo", subject="bob")
    with caplog.at_level(logging.DEBUG):
        await knowledge.prompt_block_async(hass, now=2000.0)   # no requester
    assert not any("privacy(parity)" in r.message for r in caplog.records)
