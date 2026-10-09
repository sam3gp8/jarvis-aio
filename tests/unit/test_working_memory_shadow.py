"""Phase K shadow: cognitive_core populates the kernel working memory per tick.

Observe-only — the populate drives nothing, is kill-switched two ways, and never
raises into the tick. These tests exercise the populate helper directly with a
faked `_CORE` so they stay pure (no HA tick).
"""
import sys

import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def wm(load):
    return load("kernel.working_memory")


@pytest.fixture
def log_capture(monkeypatch):
    logged = []
    monkeypatch.setattr(sys.modules["jc.websocket"], "jarvis_log",
                        lambda *a: logged.append(a))
    return logged


@pytest.fixture(autouse=True)
def _reset_wm(cc):
    # Each test starts from a clean module-level working set.
    cc._WORKING_MEMORY = None
    yield
    cc._WORKING_MEMORY = None


def test_populate_creates_and_fills_working_set(cc, wm, log_capture, monkeypatch):
    monkeypatch.setattr(cc, "WORKING_MEMORY_SHADOW", True)
    monkeypatch.setattr(cc, "_CORE", None)  # no config → default-on
    cc._populate_working_memory_shadow(people=2, anyone_home=True,
                                       sleeping=False, decided=1, emitted=1)
    mem = cc._WORKING_MEMORY
    assert mem is not None and len(mem) == 3  # situation + observation + action
    assert mem.get(wm.KIND_SITUATION, "home_occupancy").content == "occupied"
    assert mem.get(wm.KIND_OBSERVATION, "people_present").content == "2"
    assert mem.get(wm.KIND_ACTION, "tick_decisions").content == "1"
    assert log_capture and log_capture[0][0] == "WORKING_MEMORY"
    assert "shadow:" in log_capture[0][1]


def test_quiet_tick_records_no_action(cc, wm, log_capture, monkeypatch):
    monkeypatch.setattr(cc, "_CORE", None)
    cc._populate_working_memory_shadow(people=0, anyone_home=False,
                                       sleeping=True, decided=0, emitted=0)
    mem = cc._WORKING_MEMORY
    # No decisions this tick → no action item; situation reflects empty+asleep.
    assert mem.get(wm.KIND_ACTION, "tick_decisions") is None
    assert "asleep" in mem.get(wm.KIND_SITUATION, "home_occupancy").content


def test_decays_and_refreshes_across_ticks(cc, wm, log_capture, monkeypatch):
    monkeypatch.setattr(cc, "_CORE", None)
    # Two ticks refresh the same subjects rather than duplicating them.
    cc._populate_working_memory_shadow(people=1, anyone_home=True,
                                       sleeping=False, decided=0, emitted=0)
    cc._populate_working_memory_shadow(people=3, anyone_home=True,
                                       sleeping=False, decided=0, emitted=0)
    mem = cc._WORKING_MEMORY
    assert len(mem) == 2  # situation + observation, deduped
    assert mem.get(wm.KIND_OBSERVATION, "people_present").content == "3"


def test_module_kill_switch_disables(cc, monkeypatch):
    monkeypatch.setattr(cc, "WORKING_MEMORY_SHADOW", False)
    monkeypatch.setattr(cc, "_CORE", None)
    cc._populate_working_memory_shadow(people=2, anyone_home=True,
                                       sleeping=False, decided=1, emitted=1)
    assert cc._WORKING_MEMORY is None  # never created


def test_config_kill_switch_disables(cc, monkeypatch):
    import types
    monkeypatch.setattr(cc, "WORKING_MEMORY_SHADOW", True)
    fake_core = types.SimpleNamespace(config={"working_memory_shadow": False})
    monkeypatch.setattr(cc, "_CORE", fake_core)
    cc._populate_working_memory_shadow(people=2, anyone_home=True,
                                       sleeping=False, decided=1, emitted=1)
    assert cc._WORKING_MEMORY is None


def test_never_raises_on_bad_log(cc, monkeypatch):
    monkeypatch.setattr(cc, "_CORE", None)

    def boom(*a):
        raise RuntimeError("log down")

    monkeypatch.setattr(sys.modules["jc.websocket"], "jarvis_log", boom)
    # A broken logger must not stop the populate or escape the helper.
    cc._populate_working_memory_shadow(people=1, anyone_home=True,
                                       sleeping=False, decided=0, emitted=0)
    assert cc._WORKING_MEMORY is not None and len(cc._WORKING_MEMORY) == 2
