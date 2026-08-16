"""Tests for the briefing presence gate (presence.everyone_confidently_away, v6.95.0).

Scheduled briefings must fire unless the house is CONFIDENTLY empty; uncertain
presence must not suppress them (fail open).
"""
import pytest


@pytest.fixture
def p(load):
    return load("presence")


def test_all_away_is_true(p, fake_hass, monkeypatch):
    monkeypatch.setattr(p, "get_presence_summary",
                        lambda h: {"people": [{"state": "not_home"}, {"state": "away"}], "home_count": 0})
    assert p.everyone_confidently_away(fake_hass) is True


def test_someone_home_is_false(p, fake_hass, monkeypatch):
    monkeypatch.setattr(p, "get_presence_summary",
                        lambda h: {"people": [{"state": "home"}, {"state": "not_home"}], "home_count": 1})
    assert p.everyone_confidently_away(fake_hass) is False


def test_unknown_presence_is_false(p, fake_hass, monkeypatch):
    # the bug: unknown presence used to suppress briefings — must fail open now
    monkeypatch.setattr(p, "get_presence_summary",
                        lambda h: {"people": [{"state": "unknown"}], "home_count": 0})
    assert p.everyone_confidently_away(fake_hass) is False


def test_no_people_is_false(p, fake_hass, monkeypatch):
    monkeypatch.setattr(p, "get_presence_summary",
                        lambda h: {"people": [], "home_count": 0})
    assert p.everyone_confidently_away(fake_hass) is False


def test_never_raises(p, fake_hass, monkeypatch):
    def _boom(h):
        raise RuntimeError("presence down")
    monkeypatch.setattr(p, "get_presence_summary", _boom)
    assert p.everyone_confidently_away(fake_hass) is False
