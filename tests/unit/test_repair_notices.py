"""LLM Repair-issue notices — non-blocking, idempotent, self-clearing.

Verifies the issue is raised with the specific reason, isn't churned while the
same outage persists, clears on recovery, and clears a stale issue once after a
restart even if this process never raised one.
"""
import pytest


@pytest.fixture
def rn(load):
    mod = load("repair_notices")
    # reset module state between tests (module may be cached by the loader)
    mod._state.update(active=False, detail=None, cleared_once=False)
    return mod


class _RecordingIR:
    def __init__(self):
        self.created = []
        self.deleted = []
        self.IssueSeverity = type("S", (), {"ERROR": "error"})

    def async_create_issue(self, hass, domain, issue_id, **kw):
        self.created.append((issue_id, kw.get("translation_placeholders", {})))

    def async_delete_issue(self, hass, domain, issue_id):
        self.deleted.append(issue_id)


@pytest.fixture
def ir(rn, monkeypatch):
    fake = _RecordingIR()
    monkeypatch.setattr(rn, "ir", fake)
    return fake


def test_note_raises_issue_with_reason(rn, ir):
    rn.note_llm_problem(object(), "model 'x' not found on groq")
    assert len(ir.created) == 1
    issue_id, placeholders = ir.created[0]
    assert issue_id == "llm_unavailable"
    assert "not found" in placeholders["detail"]


def test_note_is_idempotent_for_same_reason(rn, ir):
    rn.note_llm_problem(object(), "same reason")
    rn.note_llm_problem(object(), "same reason")
    assert len(ir.created) == 1          # no churn while the outage persists


def test_note_updates_when_reason_changes(rn, ir):
    rn.note_llm_problem(object(), "reason A")
    rn.note_llm_problem(object(), "reason B")
    assert len(ir.created) == 2


def test_clear_removes_raised_issue(rn, ir):
    rn.note_llm_problem(object(), "down")
    rn.clear_llm_problem(object())
    assert ir.deleted == ["llm_unavailable"]


def test_clear_once_after_restart_even_if_not_raised(rn, ir):
    # fresh process (nothing raised this run) still clears a possibly-stale issue
    rn.clear_llm_problem(object())
    assert ir.deleted == ["llm_unavailable"]
    # ...but not repeatedly afterwards
    rn.clear_llm_problem(object())
    assert ir.deleted == ["llm_unavailable"]


def test_recovery_cycle_reraises(rn, ir):
    rn.note_llm_problem(object(), "down")
    rn.clear_llm_problem(object())
    rn.note_llm_problem(object(), "down")   # a later outage raises again
    assert len(ir.created) == 2
