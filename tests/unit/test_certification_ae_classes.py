"""Phase AE — the last two scenario classes gain honest live observers (audit B1).

Both were deliberately unwired ("no honest live surface") until a real runtime
event could close them:

- **conflicting_priorities** (convenience vs. safety): the discretionary-autonomy
  path (``cognitive_core._execute_action_data``) withholds a convenience action
  while an active safety situation outranks it on the priority ladder — and
  observes the class *closed* (the loop ran: situation elevated → ladder →
  convenience suppressed).
- **cognitive_error** (bad belief → observation → correction): a live intrusion
  belief is corrected to BENIGN by a false-alarm dismissal
  (``intrusion._mirror_situation_sync``) — and observes the class *closed*.

With both wired, all ten AE classes fold into the one shared ledger, so the
certification dashboard can reach ``is_certified``. Observe-only throughout — the
emits never change the suppression or the transition.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture(autouse=True)
def _fresh_ledger(cc):
    saved = (cc._CORE.config, cc.CERTIFICATION_SHADOW, cc._CERT_LEDGER)
    cc._CORE.config = {}
    cc._CERT_LEDGER = None
    cc.CERTIFICATION_SHADOW = True
    yield
    cc._CORE.config, cc.CERTIFICATION_SHADOW, cc._CERT_LEDGER = saved


@pytest.fixture
def act(load):
    # Null the earlier discretionary-path gates (budget / loop detectors) so the
    # safety-situation suppression is the gate under test — mirrors
    # test_situation_reactor's harness.
    mod = load("actuation")
    mod._active_safety_situations = {}
    mod._stream_detector = None
    mod._autonomous_loop = None
    mod._loop_detector = None
    mod._agency_budget = None
    mod._autonomous_budget = None
    return mod


# ── conflicting_priorities ───────────────────────────────────────────────────

async def test_conflicting_priorities_observed_on_safety_suppression(cc, act):
    act._active_safety_situations = {"s1": "intrusion"}
    hass = FakeHass()
    hass.states.set("light.x", "off")
    ok = await cc._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.x"]})
    assert ok is False                      # convenience withheld
    assert hass.service_calls == []
    rep = cc.certification_ledger().report()
    assert "conflicting_priorities" in rep.closed_classes


async def test_no_conflicting_priorities_without_a_safety_situation(cc, act):
    # No safety situation → the action runs; the class is not observed.
    hass = FakeHass()
    hass.states.set("light.calm", "off")
    ok = await cc._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.calm"]})
    assert ok is True
    led = cc._CERT_LEDGER
    if led is not None:
        assert "conflicting_priorities" not in led.report().closed_classes


# ── cognitive_error ──────────────────────────────────────────────────────────

def test_cognitive_error_observed_on_false_alarm(cc, load):
    intrusion = load("intrusion")
    hass = FakeHass()
    intrusion._mirror_situation_sync(hass, "investigating", camera="camera.front")
    intrusion._mirror_situation_sync(hass, "dismissed", reason="false alarm")
    rep = cc.certification_ledger().report()
    assert "cognitive_error" in rep.closed_classes


def test_no_cognitive_error_without_a_live_belief(cc, load):
    # A dismissal with no open intrusion episode corrects nothing → no observation.
    intrusion = load("intrusion")
    hass = FakeHass()
    intrusion._mirror_situation_sync(hass, "dismissed", reason="nothing open")
    led = cc._CERT_LEDGER
    if led is not None:
        assert "cognitive_error" not in led.report().closed_classes


# ── the whole suite can now certify ──────────────────────────────────────────

def test_all_ten_classes_certify(cc, load):
    CERT = load("kernel.certification")
    for c in CERT.CLASSES:
        cc.certification_observe(c, closed_loop=True)
    rep = cc.certification_ledger().report()
    assert len(CERT.CLASSES) == 10
    assert len(rep.closed_classes) == 10
    assert rep.is_certified is True
