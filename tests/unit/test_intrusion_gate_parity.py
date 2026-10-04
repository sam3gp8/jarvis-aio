"""MCU Phase R (R3a): intrusion entry-gate decision-parity.

The kernel computes the pure precondition that decides whether a possible-
intrusion investigation opens (`kernel.situation.intrusion_gate`), and the live
SafetyManager logs it against its inline decision. LOG-ONLY — the legacy path
still owns the intrusion decision entirely.

This is the groundwork and burn-in for the eventual enforce flip (R3b). Intrusion
has no safe fail-toward direction — a false positive re-creates the prior
false-alarm bug, a false negative misses a break-in — so the flip must wait until
real traffic shows the kernel and legacy agree.
"""
import logging

import pytest


@pytest.fixture
def sit(load):
    return load("kernel.situation")


@pytest.fixture
def intr(load):
    mod = load("intrusion")
    mod._last_gate_parity = None
    return mod


# ── the pure kernel gate ────────────────────────────────────────────────────

def test_gate_opens_when_away_motion_and_corroborated(sit):
    assert sit.intrusion_gate(away=True, qualifying_motion=True,
                              require_corroboration=True, alarm_armed=True,
                              open_entry=False) is True
    assert sit.intrusion_gate(away=True, qualifying_motion=True,
                              require_corroboration=True, alarm_armed=False,
                              open_entry=True) is True


def test_gate_closed_when_not_away(sit):
    assert sit.intrusion_gate(away=False, qualifying_motion=True,
                              require_corroboration=True, alarm_armed=True,
                              open_entry=True) is False


def test_gate_closed_without_motion(sit):
    assert sit.intrusion_gate(away=True, qualifying_motion=False,
                              require_corroboration=True, alarm_armed=True,
                              open_entry=True) is False


def test_gate_closed_when_corroboration_required_but_absent(sit):
    # The false-alarm-critical case: away + motion but no armed alarm and no open
    # entry → do NOT open (a lone curtain-flutter is not an intrusion).
    assert sit.intrusion_gate(away=True, qualifying_motion=True,
                              require_corroboration=True, alarm_armed=False,
                              open_entry=False) is False


def test_gate_opens_without_corroboration_when_not_required(sit):
    assert sit.intrusion_gate(away=True, qualifying_motion=True,
                              require_corroboration=False, alarm_armed=False,
                              open_entry=False) is True


# ── the parity recorder ─────────────────────────────────────────────────────

def test_recorder_agrees_open(intr):
    kv = intr.record_gate_parity(True, away=True, qualifying_motion=True,
                                 require_corroboration=True, alarm_armed=True,
                                 open_entry=False)
    assert kv is True
    assert intr._last_gate_parity["agree"] is True
    assert intr._last_gate_parity["kernel_open"] is True


def test_recorder_agrees_closed(intr):
    kv = intr.record_gate_parity(False, away=True, qualifying_motion=True,
                                 require_corroboration=True, alarm_armed=False,
                                 open_entry=False)
    assert kv is False
    assert intr._last_gate_parity["agree"] is True


def test_recorder_logs_divergence(intr, caplog):
    # Legacy claims it opened, but the kernel gate says no (no corroboration):
    # divergence is logged. (Constructed — in the live wiring they agree.)
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.intrusion"):
        intr.record_gate_parity(True, away=True, qualifying_motion=True,
                                require_corroboration=True, alarm_armed=False,
                                open_entry=False)
    assert intr._last_gate_parity["agree"] is False
    assert any("gate parity DIVERGENCE" in r.message for r in caplog.records)


# ── the live path drives the parity recorder, behaviour unchanged ───────────

@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(fake_hass, {"honorific": "sir",
                                                    "intrusion_require_corroboration": True})


async def test_live_no_corroboration_records_closed_and_stays_silent(safety, fake_hass,
                                                                     intr, monkeypatch):
    # Away + motion but neither an armed alarm nor an open entry → no alert, and
    # the parity recorder sees legacy_open=False agreeing with the kernel gate.
    monkeypatch.setattr(safety, "_residents_away", lambda: True)
    monkeypatch.setattr(safety, "_resident_on_camera", lambda: False)
    monkeypatch.setattr(safety, "_qualifying_motion",
                        lambda sleeping: [("binary_sensor.m", "the hall")])
    monkeypatch.setattr(safety, "_alarm_armed", lambda: False)
    monkeypatch.setattr(safety, "_open_entry", lambda: None)
    safety._last_intrusion_alert = 0.0
    action = await safety._check_intrusion(anyone_home=False, sleeping=False)
    assert action is None                                  # behaviour unchanged
    assert intr._last_gate_parity is not None
    assert intr._last_gate_parity["legacy_open"] is False
    assert intr._last_gate_parity["agree"] is True
