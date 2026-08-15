"""Tests for continued conversation / turn-taking (continued_conversation, v6.88.0)."""
import pytest


@pytest.fixture
def cc(load):
    return load("continued_conversation")


def test_continue_on_question(cc):
    assert cc.should_continue("Which room did you mean?") is True


def test_no_continue_on_statement(cc):
    assert cc.should_continue("Done. The kitchen light is on.") is False


def test_no_continue_on_empty(cc):
    assert cc.should_continue("") is False
    assert cc.should_continue("   ") is False


def test_continue_on_offer(cc):
    assert cc.should_continue("I can set that up — shall I?") is True
    assert cc.should_continue("Would you like me to schedule it.") is True
    assert cc.should_continue("Let me know how you'd like to proceed.") is True


def test_no_continue_on_midtext_question_mark(cc):
    assert cc.should_continue("You asked why? Here is the reason: it was off.") is False


def test_enabled_default_off(cc, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)          # nothing set
    assert cc.enabled() is False


def test_enabled_when_set(cc, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: True if k == "continued_conversation_enabled" else d)
    assert cc.enabled() is True
