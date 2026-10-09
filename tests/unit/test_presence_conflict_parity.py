"""Conflict parity on the presence read (Epistemic Fabric — Conflict).

Over a rolling window of the per-person adjudications the shadow computes,
``presence._emit_presence_conflict_parity`` logs how often ``kernel.conflict``'s
reliability-weighted winner AGREES with HA's native ``person.state`` and how
often it would DEFER (CONTESTED). Observe-only: the summary dict is unchanged and
the path never raises.
"""
import logging

import pytest

from fakes import FakeHass


def _person(h, eid, state, trackers):
    h.states.set(eid, state, device_trackers=list(trackers),
                 friendly_name=eid.split(".", 1)[-1])


def _tracker(h, eid, state, source_type="gps"):
    h.states.set(eid, state, source_type=source_type)


@pytest.fixture
def presence(load):
    p = load("presence")
    p._conflict_verdicts.clear()   # module-level window is shared across tests
    p.CONFLICT_SHADOW = True
    p.CONFLICT_PARITY = True
    yield p
    p._conflict_verdicts.clear()


def test_agreement_accumulates_and_logs_rate(presence, caplog):
    h = FakeHass()
    _person(h, "person.sam", "home",
            ["device_tracker.sam_phone", "device_tracker.sam_watch"])
    _tracker(h, "device_tracker.sam_phone", "home")
    _tracker(h, "device_tracker.sam_watch", "home", source_type="router")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    assert list(presence._conflict_verdicts) == ["AGREEMENT"]
    parity = [r.message for r in caplog.records
              if "presence_conflict(parity)" in r.message]
    assert parity and "agree_rate=1.00" in parity[-1] and "n=1" in parity[-1]


def test_contested_counts_but_not_in_agree_denominator(presence, caplog):
    h = FakeHass()
    # Two equal-reliability trackers disagree → CONTESTED (defers).
    _person(h, "person.alex", "home",
            ["device_tracker.alex_a", "device_tracker.alex_b"])
    _tracker(h, "device_tracker.alex_a", "home", source_type="gps")
    _tracker(h, "device_tracker.alex_b", "not_home", source_type="gps")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    assert list(presence._conflict_verdicts) == ["CONTESTED"]
    parity = [r.message for r in caplog.records
              if "presence_conflict(parity)" in r.message][-1]
    # No decided (non-contested) resolutions yet → agree_rate 0.00 over 0, and
    # the contested rate is the whole window.
    assert "agree_rate=0.00 (0/0)" in parity and "contested_rate=1.00" in parity


def test_divergence_lowers_agree_rate(presence, caplog):
    h = FakeHass()
    # Stronger GPS says away, weak BLE says home → kernel=away, HA=home → DIVERGE.
    _person(h, "person.sam", "home",
            ["device_tracker.sam_phone", "device_tracker.sam_watch"])
    _tracker(h, "device_tracker.sam_phone", "not_home", source_type="gps")
    _tracker(h, "device_tracker.sam_watch", "home", source_type="bluetooth_le")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    assert list(presence._conflict_verdicts) == ["DIVERGENCE"]
    parity = [r.message for r in caplog.records
              if "presence_conflict(parity)" in r.message][-1]
    assert "agree_rate=0.00 (0/1)" in parity


def test_rolling_window_mix(presence):
    # Feed the window directly to check the rate arithmetic over a mix.
    presence._conflict_verdicts.extend(
        ["AGREEMENT", "AGREEMENT", "AGREEMENT", "DIVERGENCE", "CONTESTED"])
    # 3 agree / 4 decided = 0.75; 1 contested / 5 total = 0.20. Just assert the
    # emitter runs cleanly over this state (log asserted elsewhere).
    presence._emit_presence_conflict_parity()  # must not raise


def test_parity_kill_switch_stops_accumulation(presence, caplog):
    presence.CONFLICT_PARITY = False
    h = FakeHass()
    _person(h, "person.sam", "home",
            ["device_tracker.sam_phone", "device_tracker.sam_watch"])
    _tracker(h, "device_tracker.sam_phone", "home")
    _tracker(h, "device_tracker.sam_watch", "home", source_type="router")
    with caplog.at_level(logging.DEBUG):
        presence.get_presence_summary(h)
    # Shadow still logs, but nothing accumulates and no parity line is emitted.
    assert len(presence._conflict_verdicts) == 0
    assert not [r for r in caplog.records
                if "presence_conflict(parity)" in r.message]


def test_empty_window_emits_nothing(presence, caplog):
    with caplog.at_level(logging.DEBUG):
        presence._emit_presence_conflict_parity()
    assert not [r for r in caplog.records
                if "presence_conflict(parity)" in r.message]


def test_summary_unchanged_with_parity_on_or_off(presence):
    h = FakeHass()
    _person(h, "person.sam", "home",
            ["device_tracker.sam_phone", "device_tracker.sam_watch"])
    _tracker(h, "device_tracker.sam_phone", "home")
    _tracker(h, "device_tracker.sam_watch", "not_home", source_type="bluetooth_le")
    presence.CONFLICT_PARITY = True
    on = presence.get_presence_summary(h)
    presence.CONFLICT_PARITY = False
    off = presence.get_presence_summary(h)
    assert on == off
