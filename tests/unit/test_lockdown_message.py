"""Tests for the lockdown-engaged announcement (v6.31.0).

Reports three outcomes distinctly — locks locked, closeable openings closed
(garage doors), and openings that can't be secured remotely (windows). Open
openings are named and framed as the gap to close, never the old "left as-is"
shrug, and the message is never self-contradictory.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.mark.parametrize("names,expected", [
    ([], ""),
    (["a"], "a"),
    (["a", "b"], "a and b"),
    (["a", "b", "c"], "a, b, and c"),
])
def test_join_names(cc, names, expected):
    assert cc._join_names(names) == expected


def test_already_secured(cc):
    assert cc.build_lockdown_message("sir", [], [], []) == \
        "Sir, lockdown engaged — the home was already fully secured."


def test_locked_only(cc):
    msg = cc.build_lockdown_message("sir", ["Front Lock", "Back Lock"], [], [])
    assert "I locked Front Lock and Back Lock" in msg and "The home is secure." in msg


def test_closed_a_garage(cc):
    msg = cc.build_lockdown_message("sir", [], ["the Garage Door"], [])
    assert "I closed the Garage Door" in msg and "The home is secure." in msg


def test_locked_and_closed(cc):
    msg = cc.build_lockdown_message("sir", ["Front Lock"], ["the Garage Door"], [])
    assert "I locked Front Lock and closed the Garage Door" in msg


def test_open_window_named_and_actionable(cc):
    # Sam's case: nothing closeable, locks already locked, one window open.
    msg = cc.build_lockdown_message("sir", [], [], ["Sam's Window 1"])
    assert "Sam's Window 1 is open" in msg
    assert "close it" in msg
    assert "already secured" in msg
    assert "left as-is" not in msg and "1 opening already open" not in msg


def test_closed_garage_but_window_open(cc):
    msg = cc.build_lockdown_message("sir", [], ["the Garage Door"], ["Sam's Window 1"])
    assert "I closed the Garage Door" in msg
    assert "Sam's Window 1 is open" in msg
    assert "close it" in msg


def test_multiple_open_named(cc):
    msg = cc.build_lockdown_message("sir", [], [], ["the garage", "a window"])
    assert "the garage and a window are open" in msg and "close them" in msg


def test_many_open_summarised(cc):
    msg = cc.build_lockdown_message("sir", [], [], ["d1", "d2", "d3", "d4", "d5"])
    assert "5 openings are open" in msg and "close them" in msg


def test_honorific_applied(cc):
    assert cc.build_lockdown_message("madam", [], [], []).startswith("Madam, lockdown engaged")
    assert cc.build_lockdown_message("", [], [], []).startswith("Sir, lockdown engaged")
