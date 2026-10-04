"""MCU Phase R (R2b): the kernel freeze verdict is AUTHORITATIVE in
SafetyManager._check_freeze — and it FAILS TOWARD ALERTING.

Under enforce the branch is driven by the kernel verdict, but via a max-severity
rule: the chosen verdict is the MORE severe of (kernel, legacy inline), so a
kernel fault can never produce a less-severe outcome than the raw thresholds —
a freeze alert is never missed. On any kernel error it falls back to legacy, and
a kill-switch reverts to pure legacy. (The behaviour-identity across normal temps
is already pinned by test_cognitive_core_freeze.py.)
"""
import pytest


@pytest.fixture
def safety(cognitive_core, fake_hass):
    return cognitive_core.SafetyManager(fake_hass, {"honorific": "sir"})


async def test_kill_switch_off_uses_pure_legacy(cognitive_core, safety, fake_hass,
                                                monkeypatch, load):
    # Kill-switch off + a kernel verdict that would say "none" at a critical temp:
    # legacy must still drive, so the critical alert fires (kernel is ignored).
    monkeypatch.setattr(cognitive_core, "HAZARD_SITUATION_ENFORCE", False)
    ksit = load("kernel.situation")
    monkeypatch.setattr(ksit, "freeze_verdict", lambda *a, **k: "none")
    fake_hass.states.set("weather.home", "snowy", temperature=15)
    action = await safety._check_freeze()
    assert action is not None and action["type"] == "freeze_critical"


async def test_kernel_error_fails_toward_alert(cognitive_core, safety, fake_hass,
                                               monkeypatch, load):
    # If the kernel verdict raises, the legacy threshold still fires the alert.
    ksit = load("kernel.situation")

    def _boom(*a, **k):
        raise RuntimeError("kernel down")
    monkeypatch.setattr(ksit, "freeze_verdict", _boom)
    fake_hass.states.set("weather.home", "snowy", temperature=15)
    action = await safety._check_freeze()
    assert action is not None and action["type"] == "freeze_critical"


async def test_less_severe_kernel_verdict_still_alerts(cognitive_core, safety,
                                                       fake_hass, monkeypatch, load):
    # The safety belt: even if the kernel returns a LESS-severe verdict than the
    # raw thresholds imply, the max-severity rule keeps the alert.
    ksit = load("kernel.situation")
    monkeypatch.setattr(ksit, "freeze_verdict", lambda *a, **k: "none")
    fake_hass.states.set("weather.home", "snowy", temperature=15)   # legacy=critical
    action = await safety._check_freeze()
    assert action is not None and action["type"] == "freeze_critical"


async def test_more_severe_kernel_verdict_wins(cognitive_core, safety, fake_hass,
                                               monkeypatch, load):
    # If the kernel were MORE severe than legacy, enforce takes the kernel verdict
    # (alert-biased). At a warm temp (legacy=none) a kernel "critical" fires.
    ksit = load("kernel.situation")
    monkeypatch.setattr(ksit, "freeze_verdict", lambda *a, **k: "critical")
    fake_hass.states.set("weather.home", "sunny", temperature=55)   # legacy=none
    action = await safety._check_freeze()
    assert action is not None and action["type"] == "freeze_critical"


def test_more_severe_helper(cognitive_core):
    m = cognitive_core._more_severe_freeze
    assert m("critical", "warning") == "critical"
    assert m("warning", "critical") == "critical"
    assert m("none", "warning") == "warning"
    assert m("clear", "none") == "clear"
    assert m("none", "none") == "none"


def test_default_kill_switch_is_enforce(cognitive_core):
    assert cognitive_core.HAZARD_SITUATION_ENFORCE is True
