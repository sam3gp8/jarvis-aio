"""Regression tests for the 8.0.1 audit fixes: blocking file/SQLite I/O moved
off Home Assistant's event loop, and HA state/registry reads kept ON the loop
(never inside executor jobs). A recording fake executor proves the routing."""
import json
import pathlib
import sys
import types

import pytest


class _RecordingHass:
    """Wraps FakeHass so tests can see exactly what ran in the executor."""

    def __init__(self, fake):
        self._fake = fake
        self.executor_calls: list[str] = []

    def __getattr__(self, name):
        return getattr(self._fake, name)

    async def async_add_executor_job(self, func, *args):
        self.executor_calls.append(getattr(func, "__name__", repr(func)))
        return func(*args)


@pytest.fixture
def rhass(fake_hass):
    return _RecordingHass(fake_hass)


# ── intrusion event log ──────────────────────────────────────────────────────

@pytest.fixture
def intr(load, tmp_path, monkeypatch):
    m = load("intrusion")
    monkeypatch.setattr(m, "LOG_PATH", pathlib.Path(tmp_path / "intrusion_log.json"))
    m._log = []
    m._log_loaded = False
    return m


async def test_intrusion_record_event_writes_off_loop(intr, rhass):
    ev = await intr.async_record_event(rhass, "investigating", reason="motion",
                                       breach_area="kitchen")
    assert ev["kind"] == "investigating"
    assert "_load_log" in rhass.executor_calls      # one-time read off-loop
    assert "_write_log" in rhass.executor_calls     # write off-loop
    saved = json.loads(intr.LOG_PATH.read_text())
    assert saved and saved[-1]["id"] == ev["id"]


async def test_intrusion_label_event_writes_off_loop(intr, rhass):
    ev = await intr.async_record_event(rhass, "unresolved")
    rhass.executor_calls.clear()
    res = await intr.async_label_event(rhass, ev["id"], "false")
    assert res["ok"] is True
    assert rhass.executor_calls == ["_write_log"]
    assert json.loads(intr.LOG_PATH.read_text())[-1]["label"] == "false"


async def test_intrusion_label_unknown_event_does_not_write(intr, rhass):
    await intr.async_load(rhass)
    rhass.executor_calls.clear()
    res = await intr.async_label_event(rhass, "evt_missing", "real")
    assert res["ok"] is False and rhass.executor_calls == []


def test_intrusion_sync_api_still_persists(intr):
    intr._log_loaded = True
    ev = intr.record_event("confirmed")
    assert json.loads(intr.LOG_PATH.read_text())[-1]["id"] == ev["id"]


# ── cognitive ignore list ────────────────────────────────────────────────────

async def test_async_ignore_mutates_on_loop_and_writes_off_loop(
        load, rhass, tmp_path, monkeypatch):
    cc = load("cognitive_core")
    monkeypatch.setattr(cc, "IGNORE_FILE", str(tmp_path / "ignore.json"))
    cc._CORE.ignore_mgr = cc.IgnoreManager()
    res = await cc.async_ignore(rhass, "sensor.noisy_*", 30, "too chatty")
    assert res["success"] is True
    assert cc._CORE.ignore_mgr.is_ignored("sensor.noisy_one")
    assert rhass.executor_calls == ["write_snapshot"]
    assert json.loads(pathlib.Path(tmp_path / "ignore.json").read_text())[0][
        "entity_pattern"] == "sensor.noisy_*"

    rhass.executor_calls.clear()
    res = await cc.async_unignore(rhass, "sensor.noisy_*")
    assert res["success"] is True
    assert not cc._CORE.ignore_mgr.is_ignored("sensor.noisy_one")
    assert rhass.executor_calls == ["write_snapshot"]


async def test_async_unignore_missing_pattern_skips_write(load, rhass, tmp_path,
                                                          monkeypatch):
    cc = load("cognitive_core")
    monkeypatch.setattr(cc, "IGNORE_FILE", str(tmp_path / "ignore.json"))
    cc._CORE.ignore_mgr = cc.IgnoreManager()
    res = await cc.async_unignore(rhass, "sensor.never_added")
    assert res["success"] is False and rhass.executor_calls == []


# ── home context: state/registry reads on the loop, file read off it ────────

def test_build_home_context_uses_supplied_learned_without_file_read(
        load, fake_hass, monkeypatch):
    agent = load("agent")
    monkeypatch.setattr(agent, "_load_learned",
                        lambda: pytest.fail("must not read the file when given data"))
    fake_hass.states.set("light.kitchen", "on", friendly_name="Kitchen")
    ctx = agent._build_home_context(
        fake_hass, {"alias": {"the big light": "light.kitchen"}, "preference": {}})
    assert "Kitchen" in ctx and "the big light" in ctx


# ── camera: config-entry reads resolved on the loop ─────────────────────────

async def test_async_make_client_resolves_base_cfg_on_loop(load, rhass, monkeypatch):
    if "aiohttp" not in sys.modules:
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    cam = load("camera")
    seen = {}

    def _fake_make(hass, provider, model, fallback, base_cfg=None):
        seen["base_cfg"] = base_cfg
        return "CLIENT"

    monkeypatch.setattr(cam, "_make_client", _fake_make)
    monkeypatch.setattr(cam, "_base_url_cfg", lambda h: {"llm_base_url": "http://x"})
    assert await cam.async_make_client(rhass, "groq", "m", "FB") == "CLIENT"
    assert seen["base_cfg"] == {"llm_base_url": "http://x"}   # passed in, not re-read
