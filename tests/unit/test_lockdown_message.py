"""Tests for the lockdown-engaged announcement (v6.30.1).

Guards against the nonsensical phrasing reported on-device: a lockdown that
announced "everything already locked" and then shrugged "1 opening already open
will be left as-is" — making it sound like it did nothing while ignoring the one
real gap. Open openings must now be named and framed as the thing to close.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


# ── _join_names ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("names,expected", [
    ([], ""),
    (["a"], "a"),
    (["a", "b"], "a and b"),
    (["a", "b", "c"], "a, b, and c"),
])
def test_join_names(cc, names, expected):
    assert cc._join_names(names) == expected


# ── the four message cases ───────────────────────────────────────────────────

def test_already_secured(cc):
    msg = cc.build_lockdown_message("sir", [], [])
    assert msg == "Sir, lockdown engaged — the home was already fully secured."


def test_locked_some_all_closed(cc):
    msg = cc.build_lockdown_message("sir", ["Front Lock", "Back Lock"], [])
    assert "I locked Front Lock and Back Lock" in msg
    assert "The home is secure." in msg


def test_open_opening_is_named_and_actionable(cc):
    # Sam's case: nothing to lock, one opening open.
    msg = cc.build_lockdown_message("sir", [], ["the Garage Door"])
    assert "the Garage Door is open" in msg
    assert "close it" in msg
    assert "already locked" in msg            # explains the locks were fine
    # the old nonsensical phrasing is gone
    assert "left as-is" not in msg
    assert "1 opening already open" not in msg


def test_locked_some_and_one_open(cc):
    msg = cc.build_lockdown_message("sir", ["Front Lock"], ["the cellar door"])
    assert "I locked Front Lock" in msg
    assert "the cellar door is open" in msg
    assert "close it" in msg


def test_multiple_open_named(cc):
    msg = cc.build_lockdown_message("sir", [], ["the garage", "a window"])
    assert "the garage and a window are open" in msg
    assert "close them" in msg


def test_many_open_summarised(cc):
    names = ["d1", "d2", "d3", "d4", "d5"]
    msg = cc.build_lockdown_message("sir", [], names)
    assert "5 openings are open" in msg
    assert "close them" in msg


def test_honorific_applied(cc):
    assert cc.build_lockdown_message("madam", [], []).startswith("Madam, lockdown engaged")
    # empty honorific falls back to 'Sir'
    assert cc.build_lockdown_message("", [], []).startswith("Sir, lockdown engaged")
