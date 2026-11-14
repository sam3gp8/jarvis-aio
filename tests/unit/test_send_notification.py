"""#513: send_notification tool — JARVIS reaches the owner with free text via the
notification destination already configured in Settings → Notifications
(notify_service). Guardrails: clear error when none is set, a char cap, and a
rolling-window rate limit so a looping model can't flood the owner's phone.
"""
import json

import pytest

from fakes import FakeHass


@pytest.fixture
def agent(load):
    return load("agent")


@pytest.fixture
def jc(load):
    return load("jarvis_config")


@pytest.fixture(autouse=True)
def _reset_rate(agent):
    agent._NOTIFY_SEND_TS.clear()
    yield
    agent._NOTIFY_SEND_TS.clear()


def _set_notify(jc, monkeypatch, svc):
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: svc if k == "notify_service" else d)


def _hass_recording():
    h = FakeHass()
    calls = []

    async def _ac(domain, service, data=None, blocking=True, **kw):
        calls.append((domain, service, dict(data or {})))
    h.services.async_call = _ac
    return h, calls


# ── happy path ──────────────────────────────────────────────────────────────

async def test_sends_via_configured_service(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "notify.mobile_app_pixel")
    h, calls = _hass_recording()
    out = json.loads(await agent._exec_send_notification(h, {"message": "Dryer's done, sir."}))
    assert out.get("success") is True
    assert out.get("sent_via") == "notify.mobile_app_pixel"
    assert calls == [("notify", "mobile_app_pixel",
                      {"message": "Dryer's done, sir.", "title": "JARVIS"})]


async def test_telegram_entity_style_service(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "notify.my_telegram")
    h, calls = _hass_recording()
    out = json.loads(await agent._exec_send_notification(h, {"message": "Hello"}))
    assert out.get("success") is True
    assert calls[0][:2] == ("notify", "my_telegram")


# ── guardrails ──────────────────────────────────────────────────────────────

async def test_empty_message_errors(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "notify.mobile_app_pixel")
    h, calls = _hass_recording()
    out = json.loads(await agent._exec_send_notification(h, {"message": "   "}))
    assert "error" in out and calls == []


async def test_no_destination_configured_errors(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "")                 # none set
    h, calls = _hass_recording()
    out = json.loads(await agent._exec_send_notification(h, {"message": "hi"}))
    assert "error" in out and "Settings" in out["error"]
    assert calls == []                               # nothing sent


async def test_malformed_service_errors(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "mobile_app_pixel")  # no "notify." domain
    h, calls = _hass_recording()
    out = json.loads(await agent._exec_send_notification(h, {"message": "hi"}))
    assert "error" in out and calls == []


async def test_message_is_capped(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "notify.mobile_app_pixel")
    h, calls = _hass_recording()
    await agent._exec_send_notification(h, {"message": "x" * 5000})
    assert len(calls[0][2]["message"]) == agent._NOTIFY_MAX_CHARS


async def test_rate_limit_blocks_a_flood(agent, jc, monkeypatch):
    _set_notify(jc, monkeypatch, "notify.mobile_app_pixel")
    h, calls = _hass_recording()
    ok = 0
    for i in range(agent._NOTIFY_MAX_PER_WINDOW):
        out = json.loads(await agent._exec_send_notification(h, {"message": f"m{i}"}))
        ok += 1 if out.get("success") else 0
    assert ok == agent._NOTIFY_MAX_PER_WINDOW
    # the next one in the same window is refused and sends nothing
    blocked = json.loads(await agent._exec_send_notification(h, {"message": "flood"}))
    assert "error" in blocked and "rate" in blocked["error"].lower()
    assert len(calls) == agent._NOTIFY_MAX_PER_WINDOW


# ── wiring ──────────────────────────────────────────────────────────────────

def test_registered_in_tool_map_and_slim_and_schema(agent):
    assert "send_notification" in agent._TOOL_MAP
    assert "send_notification" in agent._SLIM_TOOLS
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "send_notification" in names
