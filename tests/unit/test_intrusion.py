"""Tests for intrusion snapshots + false-alarm call-off (v6.68.0). Covers the
call-off suppression window (safety-relevant: a call-off must actually suppress
escalation), false-alarm recording, snapshot capture, and the status shape."""
import time

import pytest


@pytest.fixture
def intr(load, tmp_path, monkeypatch):
    m = load("intrusion")
    # isolate snapshot dir + reset module state each test
    monkeypatch.setattr(m, "SNAPSHOT_DIR", str(tmp_path / "snaps"))
    m._called_off_until = 0.0
    m._last_snapshot = {}
    m._false_alarms = []
    return m


# ── call-off suppression ─────────────────────────────────────────────────────

def test_not_called_off_by_default(intr):
    assert intr.is_called_off() is False


def test_dismiss_suppresses_escalation(intr):
    res = intr.dismiss_intrusion("it was the cat")
    assert res["ok"] is True
    assert res["suppressed_seconds"] > 0
    assert intr.is_called_off() is True          # escalation now suppressed


def test_calloff_expires(intr, monkeypatch):
    intr.dismiss_intrusion()
    assert intr.is_called_off() is True
    # jump past the cooldown (capture real now first to avoid recursion)
    future = time.time() + intr._CALLOFF_COOLDOWN + 5
    monkeypatch.setattr(intr.time, "time", lambda: future)
    assert intr.is_called_off() is False


def test_clear_calloff_resets(intr):
    intr.dismiss_intrusion()
    assert intr.is_called_off() is True
    intr.clear_calloff()
    assert intr.is_called_off() is False


# ── false-alarm recording ────────────────────────────────────────────────────

def test_false_alarm_recorded(intr):
    intr.dismiss_intrusion("cat again")
    assert intr.false_alarm_count() == 1


def test_false_alarm_count_windowed(intr, monkeypatch):
    intr.dismiss_intrusion()
    # an old false alarm outside the window shouldn't count
    intr._false_alarms.insert(0, {"ts": int(time.time()) - 200000, "reason": "old"})
    assert intr.false_alarm_count(within_seconds=86400) == 1   # only the recent one


def test_false_alarm_list_capped(intr):
    for _ in range(60):
        intr.dismiss_intrusion()
    assert len(intr._false_alarms) <= 50


# ── snapshot capture ─────────────────────────────────────────────────────────

async def test_capture_snapshot_writes_file(intr, tmp_path):
    class _Img:
        content = b"\xff\xd8\xff\xe0jpegbytes"
    class _Hass:
        async def async_add_executor_job(self, fn, *a):
            return fn(*a)
    hass = _Hass()

    import sys, types
    cam_mod = types.ModuleType("homeassistant.components.camera")
    async def _get_image(h, entity, timeout=10):
        return _Img()
    cam_mod.async_get_image = _get_image
    sys.modules["homeassistant.components.camera"] = cam_mod
    try:
        info = await intr.capture_snapshot(hass, "camera.dining_room")
    finally:
        sys.modules.pop("homeassistant.components.camera", None)

    assert info is not None
    assert info["camera"] == "camera.dining_room"
    assert info["url"].startswith("/local/jarvis/intrusion/")
    import os
    assert os.path.exists(info["path"])
    assert intr.last_snapshot()["camera"] == "camera.dining_room"


async def test_capture_snapshot_no_entity_returns_none(intr):
    class _Hass: ...
    assert await intr.capture_snapshot(_Hass(), "") is None


async def test_capture_snapshot_never_raises(intr):
    class _Hass:
        async def async_add_executor_job(self, fn, *a):
            return fn(*a)
    import sys, types
    cam_mod = types.ModuleType("homeassistant.components.camera")
    async def _boom(h, entity, timeout=10):
        raise RuntimeError("camera offline")
    cam_mod.async_get_image = _boom
    sys.modules["homeassistant.components.camera"] = cam_mod
    try:
        assert await intr.capture_snapshot(_Hass(), "camera.x") is None   # no crash
    finally:
        sys.modules.pop("homeassistant.components.camera", None)


# ── status shape ─────────────────────────────────────────────────────────────

def test_status_shape(intr):
    st = intr.status()
    assert set(("last_snapshot", "called_off", "suppressed_for", "false_alarms_24h")) <= set(st)
    assert st["called_off"] is False


# ── agent tool registration ──────────────────────────────────────────────────

def test_dismiss_intrusion_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "dismiss_intrusion" in names
    assert "dismiss_intrusion" in agent._TOOL_MAP


# ── acknowledge (hold auto-escalation without cancelling) v6.69.0 ────────────

def test_acknowledge_holds_escalation(intr):
    assert intr.is_acknowledged() is False
    res = intr.acknowledge("I'm looking")
    assert res["ok"] is True
    assert intr.is_acknowledged() is True


def test_acknowledge_expires(intr, monkeypatch):
    intr.acknowledge()
    assert intr.is_acknowledged() is True
    future = time.time() + intr._ACK_WINDOW + 5
    monkeypatch.setattr(intr.time, "time", lambda: future)
    assert intr.is_acknowledged() is False


def test_acknowledge_is_not_calloff(intr):
    # acknowledging must NOT suppress evidence-based escalation (not a false alarm)
    intr.acknowledge()
    assert intr.is_acknowledged() is True
    assert intr.is_called_off() is False           # distinct states


def test_clear_resets_acknowledge(intr):
    intr.acknowledge()
    intr.clear_calloff()
    assert intr.is_acknowledged() is False


def test_status_includes_acknowledged(intr):
    st = intr.status()
    assert "acknowledged" in st and st["acknowledged"] is False


# ── acknowledge tool registration ────────────────────────────────────────────

def test_acknowledge_tool_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "acknowledge_alert" in names
    assert "acknowledge_alert" in agent._TOOL_MAP
