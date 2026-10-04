"""MCU Phase E (E4): the actuation envelope feeds the kernel loop detector and
agency budget in shadow.

Every actuation is fed into `kernel.loop_detect` (thrash detection) and
`kernel.budget` (self-imposed autonomous-action ceiling); a loop or an exhausted
budget is logged but NEVER acted on. Enforcing — actually suppressing an action —
gates live actuation and is owner-gated, deliberately not done here.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    mod = load("actuation")
    # Fresh detectors per test.
    mod._loop_detector = None
    mod._agency_budget = None
    return mod


def test_agency_shadow_never_raises(act):
    for _ in range(3):
        act._agency_shadow("light.turn_on", "light.bed")


def test_agency_shadow_flags_a_thrash_loop(act, caplog):
    # Firing the same action key rapidly within the detector window trips the
    # repetition loop — logged, not suppressed.
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.actuation"):
        for _ in range(6):
            act._agency_shadow("light.turn_on", "light.bed")
    assert any("actuation loop" in r.message for r in caplog.records)


def test_agency_shadow_flags_budget_exhaustion(act, caplog):
    # The default autonomous-action ceiling is 60/hour; the 61st within the
    # window logs an exhausted-budget warning (shadow — not enforced).
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.actuation"):
        for i in range(62):
            act._agency_shadow("light.turn_on", f"light.{i}")  # distinct keys → no loop
    assert any("budget exhausted" in r.message for r in caplog.records)


# ── behaviour preserved: emit_event still publishes with the shadow active ──────

def test_emit_event_still_publishes_with_agency_shadow(act, load):
    bus = load("kernel.event_bus").JarvisEventBus()
    captured = []
    bus.subscribe_all(lambda ev: captured.append(ev))
    hass = FakeHass()
    hass.data["jarvis"] = {"e": {"event_bus": bus}}

    act.emit_event(hass, "light", "light.bed", action="turn_on")
    actuation_events = [e for e in captured if e.type == "control.actuation"]
    assert len(actuation_events) == 1
    assert actuation_events[0].subject == "light.bed"
