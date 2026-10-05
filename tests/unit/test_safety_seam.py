"""MCU Phase H (H8): the SAFETY policy-mode actuator seam.

Securing the home against a threat (intrusion lockdown, nighttime lockdown) was
the last actuator path that still called hass.services directly, bypassing the
universal seam. H8 routes it through ``actuation.execute_safety_actuator`` — but
under a FAIL-TOWARD-PROTECTION contract, the opposite of the discretionary path:

  * it is NEVER budget/loop-blocked (the seam doesn't consult those gates);
  * verification is mandatory (a securing action that didn't land is a failure);
  * on ANY seam/kernel fault — or any non-success — it FAILS OPEN to a direct
    hass.services call, so the home is still secured (lock/close_cover are
    idempotent, so the backstop can never leave the home less secure);
  * a one-line kill-switch (SAFETY_SEAM_ENFORCE) reverts it to the direct call.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    mod = load("actuation")
    mod._autonomous_budget = None
    mod._autonomous_loop = None
    mod._loop_detector = None
    mod._agency_budget = None
    return mod


def _locks(hass):
    return [c for c in hass.service_calls if c[0] == "lock" and c[1] == "lock"]


# ── happy path: routes through the seam ──────────────────────────────────────

async def test_safety_actuator_secures_through_seam(act):
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")
    ok = await act.execute_safety_actuator(
        hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock")
    await hass.drain()                      # run the scheduled verify-after-act
    assert ok is True
    # the seam performs the canonical per-entity service call (blocking)
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls
    assert len(_locks(hass)) == 1          # secured exactly once (no double-call)


async def test_safety_actuator_publishes_actuation_event(act, load, monkeypatch):
    ev = load("events")
    published = []
    monkeypatch.setattr(ev, "publish", lambda hass, e: published.append(e))
    hass = FakeHass()
    hass.states.set("cover.garage", "open")
    ok = await act.execute_safety_actuator(
        hass, capability="cover.close_cover", entity_id="cover.garage",
        domain="cover", service="close_cover",
        data={"entity_id": "cover.garage"}, action="close")
    await hass.drain()
    assert ok is True
    assert len(published) == 1
    assert published[0].data["capability"] == "cover.close_cover"
    assert published[0].subject == "cover.garage"


# ── never blocked by budget / loop ───────────────────────────────────────────

async def test_safety_is_never_budget_blocked(act):
    # Exhaust the autonomous budget and trip the loop detector; a safety securing
    # action still fires — it does not route through those gates at all.
    import time
    now = time.time()
    for i in range(60):
        act.agency_budget_check(enforce=True, now=now)
    for i in range(6):
        act.loop_detect_check("lock.lock:lock.front", enforce=True, now=now)
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")
    ok = await act.execute_safety_actuator(
        hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock")
    await hass.drain()
    assert ok is True
    assert len(_locks(hass)) == 1          # secured despite exhausted budget/loop


# ── fail toward protection ───────────────────────────────────────────────────

async def test_fails_open_to_direct_call_on_seam_error(act, monkeypatch, caplog):
    # If the seam itself raises, the home is still secured by a direct call.
    async def _boom(*a, **k):
        raise RuntimeError("kernel exploded")
    monkeypatch.setattr(act, "execute_actuator", _boom)
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.actuation"):
        ok = await act.execute_safety_actuator(
            hass, capability="lock.lock", entity_id="lock.front",
            domain="lock", service="lock", data={"entity_id": "lock.front"},
            action="lock")
    assert ok is True
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls
    assert any("failing open to a direct call" in r.message for r in caplog.records)


async def test_fails_open_when_seam_reports_non_success(act, monkeypatch):
    # If the seam returns (False, detail) — e.g. a precondition didn't hold — the
    # safety path still secures directly rather than silently withholding.
    async def _nope(*a, **k):
        return (False, "precondition failed")
    monkeypatch.setattr(act, "execute_actuator", _nope)
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")
    ok = await act.execute_safety_actuator(
        hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock")
    assert ok is True
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls


async def test_returns_false_only_when_even_direct_call_fails(act, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("seam down")
    monkeypatch.setattr(act, "execute_actuator", _boom)
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")

    async def _svc_boom(domain, service, data=None, blocking=False, **k):
        raise RuntimeError("HA down too")
    monkeypatch.setattr(hass.services, "async_call", _svc_boom)
    ok = await act.execute_safety_actuator(
        hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock")
    assert ok is False                     # honest: nothing could secure it


# ── kill switch ──────────────────────────────────────────────────────────────

async def test_kill_switch_reverts_to_direct_call(act, monkeypatch):
    monkeypatch.setattr(act, "SAFETY_SEAM_ENFORCE", False)
    seam_called = []

    async def _spy(*a, **k):
        seam_called.append(1)
        return (True, "")
    monkeypatch.setattr(act, "execute_actuator", _spy)
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")
    ok = await act.execute_safety_actuator(
        hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock")
    assert ok is True
    assert seam_called == []                # seam bypassed entirely
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls


def test_default_kill_switch_is_enforce(act):
    assert act.SAFETY_SEAM_ENFORCE is True


# ── verification is scheduled (mandatory) ────────────────────────────────────

async def test_verify_is_scheduled_for_securing(act, monkeypatch):
    scheduled = []
    real_exec = act.execute_actuator

    async def _capture(hass, **kw):
        scheduled.append(kw.get("verify"))
        return await real_exec(hass, **kw)
    monkeypatch.setattr(act, "execute_actuator", _capture)
    hass = FakeHass()
    hass.states.set("lock.front", "unlocked")
    await act.execute_safety_actuator(
        hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock")
    await hass.drain()
    assert scheduled and scheduled[0] is not None   # securing always verifies


# ── integration: the LockdownManager secures through the seam ─────────────────

@pytest.fixture
def cc(load, act):
    return load("cognitive_core")


@pytest.fixture(autouse=True)
def _isolate_lockdown(tmp_path, monkeypatch, cc):
    monkeypatch.setattr(cc, "LOCKDOWN_STATE_PATH", str(tmp_path / "lockdown.json"))
    yield


async def test_lock_all_secures_through_seam_and_is_unbudgeted(cc, act):
    # Exhaust the autonomous budget and trip the loop detector; the lockdown
    # _lock_all still secures — the safety seam never consults either gate.
    import time
    now = time.time()
    for i in range(60):
        act.agency_budget_check(enforce=True, now=now)
    for i in range(6):
        act.loop_detect_check("lock.lock:lock.back", enforce=True, now=now)
    hass = FakeHass()
    hass.states.set("lock.back", "unlocked", friendly_name="Back Door")
    mgr = cc.LockdownManager(hass, {"honorific": "sir"})
    locked = await mgr._lock_all()
    await hass.drain()
    assert "Back Door" in locked
    assert ("lock", "lock", {"entity_id": "lock.back"}) in hass.service_calls


async def test_secure_entity_routes_lock_and_cover_through_seam(cc):
    hass = FakeHass()
    hass.states.set("lock.side", "unlocked")
    hass.states.set("cover.gate", "open")
    mgr = cc.LockdownManager(hass, {"honorific": "sir"})
    assert await mgr._secure_entity("lock.side", "lock") is True
    assert await mgr._secure_entity("cover.gate", "cover") is True
    await hass.drain()
    assert ("lock", "lock", {"entity_id": "lock.side"}) in hass.service_calls
    assert ("cover", "close_cover", {"entity_id": "cover.gate"}) in hass.service_calls


async def test_secure_entity_still_secures_when_seam_errors(cc, act, monkeypatch):
    # Fail toward protection end-to-end: a seam fault doesn't stop the securing.
    async def _boom(*a, **k):
        raise RuntimeError("seam down")
    monkeypatch.setattr(act, "execute_actuator", _boom)
    hass = FakeHass()
    hass.states.set("lock.side", "unlocked")
    mgr = cc.LockdownManager(hass, {"honorific": "sir"})
    assert await mgr._secure_entity("lock.side", "lock") is True
    assert ("lock", "lock", {"entity_id": "lock.side"}) in hass.service_calls
