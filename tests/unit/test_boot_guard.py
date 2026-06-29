"""Regression tests for the boot-guard AlertBuffer.

Stdlib-only; loads standalone. Pins buffering-while-not-ready, in-order replay on
ready, idempotent ready, reload re-gating, and the drop-oldest overflow policy.
"""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bg = _load_standalone("jarvis_boot_guard", "boot_guard.py")


def _collector():
    seen: list[dict] = []

    async def dispatch(data):
        seen.append(data)

    return seen, dispatch


@pytest.mark.asyncio
async def test_buffers_until_ready_then_replays_in_order():
    buf = bg.AlertBuffer()
    assert buf.ready is False
    for i in range(3):
        buf.enqueue({"message": f"alert {i}", "target_area": "office"})
    assert buf.pending() == 3

    seen, dispatch = _collector()
    replayed = await buf.mark_ready(dispatch)
    assert replayed == 3
    assert [d["message"] for d in seen] == ["alert 0", "alert 1", "alert 2"]
    assert buf.ready is True
    assert buf.pending() == 0


@pytest.mark.asyncio
async def test_mark_ready_is_idempotent():
    buf = bg.AlertBuffer()
    buf.enqueue({"message": "x", "target_area": "office"})
    seen, dispatch = _collector()
    assert await buf.mark_ready(dispatch) == 1
    # Second call replays nothing and doesn't re-dispatch.
    assert await buf.mark_ready(dispatch) == 0
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_reload_regate_keeps_and_replays_buffer():
    buf = bg.AlertBuffer()
    seen, dispatch = _collector()
    await buf.mark_ready(dispatch)          # ready
    buf.begin()                              # reload → re-gate
    assert buf.ready is False
    buf.enqueue({"message": "during reload", "target_area": "office"})
    replayed = await buf.mark_ready(dispatch)
    assert replayed == 1
    assert seen[-1]["message"] == "during reload"


@pytest.mark.asyncio
async def test_overflow_drops_oldest():
    buf = bg.AlertBuffer(maxsize=3)
    for i in range(5):                        # 0,1 should be dropped
        buf.enqueue({"message": f"m{i}", "target_area": "office"})
    assert buf.pending() == 3
    seen, dispatch = _collector()
    await buf.mark_ready(dispatch)
    assert [d["message"] for d in seen] == ["m2", "m3", "m4"]


@pytest.mark.asyncio
async def test_enqueue_copies_data():
    buf = bg.AlertBuffer()
    original = {"message": "m", "target_area": "office"}
    buf.enqueue(original)
    original["message"] = "mutated"           # must not affect the buffered copy
    seen, dispatch = _collector()
    await buf.mark_ready(dispatch)
    assert seen[0]["message"] == "m"
