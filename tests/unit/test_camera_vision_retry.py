"""#520: intermittent 'no usable view' from a reasoning vision model.

After the #514 /no_think nudge, Qwen describes the frame most turns but on some
turns reasons anyway — the tight non-thinking budget truncates the <think> block
and `_strip_think` leaves nothing, so the frame is skipped. `_vision_describe`
now retries ONCE with the larger thinking budget when the no-think pass comes
back empty, so the model can finish reasoning and still emit the description.
Only the empty path pays the retry.
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def cam(load):
    return load("camera")


def _queue_chat(cam, monkeypatch, texts):
    calls = {"n": 0, "max_tokens": []}

    async def _fake(hass, client, **kw):
        calls["n"] += 1
        calls["max_tokens"].append(kw.get("max_tokens"))
        return {"text": texts[min(calls["n"] - 1, len(texts) - 1)]}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake)
    return calls


async def test_happy_path_no_retry(cam, monkeypatch):
    calls = _queue_chat(cam, monkeypatch, ["A person stands at the door."])
    out = await cam._vision_describe(FakeHass(), object(), [], "qwen3-vl", False, "Cam 1")
    assert out == "A person stands at the door."
    assert calls["n"] == 1                         # described first try, no retry


async def test_empty_then_retry_recovers(cam, monkeypatch):
    # First call: a truncated (unterminated) think block that strips to nothing.
    # Retry (bigger budget): the model finishes and emits a usable description.
    calls = _queue_chat(cam, monkeypatch, [
        "<think>let me look at the frame and reason about what I see",
        "A grey car is parked in the driveway.",
    ])
    out = await cam._vision_describe(FakeHass(), object(), [], "qwen3-vl", False, "Cam 1")
    assert out == "A grey car is parked in the driveway."
    assert calls["n"] == 2                         # retried once
    assert calls["max_tokens"][1] > calls["max_tokens"][0]   # larger budget on retry


async def test_retry_also_empty_stays_empty(cam, monkeypatch):
    calls = _queue_chat(cam, monkeypatch, [
        "<think>reasoning that never closes",
        "<think>still reasoning, no answer",
    ])
    out = await cam._vision_describe(FakeHass(), object(), [], "qwen3-vl", False, "Cam 1")
    assert len(out) < 3                            # nothing usable → caller skips
    assert calls["n"] == 2


async def test_thinking_mode_does_not_retry(cam, monkeypatch):
    # With vision thinking ON the caller opted into reasoning; the empty-retry is
    # a no-think-path recovery only, so it must not fire here.
    calls = _queue_chat(cam, monkeypatch, ["<think>only thinking</think>"])
    out = await cam._vision_describe(FakeHass(), object(), [], "qwen3-vl", True, "Cam 1")
    assert len(out) < 3
    assert calls["n"] == 1
