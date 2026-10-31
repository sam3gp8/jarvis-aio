"""Natural-language automation composer (Suggestions tab).

create_automation(install=False) is a dry run — validate + return the built
config/YAML without writing. draft_automation turns a plain-language request into
a reviewed, NOT-installed draft via one LLM tool-call, validating every name
against the live home. Install is a separate, explicit step.
"""
from __future__ import annotations

import sys
import types

import pytest
import yaml


@pytest.fixture
def creator(load):
    module = load("automation_creator")
    sys.modules["jc"].__dict__["automation_creator"] = module
    return module


class _State:
    def __init__(self, eid, fn=None):
        self.entity_id = eid
        self.attributes = {"friendly_name": fn} if fn else {}


class _States:
    def __init__(self, states):
        self._states = states

    def async_all(self, domain=None):
        if domain is None:
            return list(self._states)
        return [s for s in self._states if s.entity_id.split(".")[0] == domain]


class _Services:
    def __init__(self, services):
        self._services = services
        self.calls = []

    def async_services(self):
        return self._services

    async def async_call(self, domain, service, **kw):
        self.calls.append((domain, service, kw))


class _Hass:
    def __init__(self, states, services, path):
        self.states = _States(states)
        self.services = _Services(services)
        self.config = types.SimpleNamespace(path=lambda f: str(path))

    async def async_add_executor_job(self, func, *a):
        return func(*a)


class _Client:
    """Minimal LLMProvider stand-in: chat() returns a canned result dict."""
    def __init__(self, result):
        self._result = result
        self.seen = None

    def chat(self, **kw):
        self.seen = kw
        return self._result


class _ScriptClient:
    """LLMProvider stand-in that returns a scripted sequence of chat results,
    recording the messages it was called with each round (for agentic tests)."""
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.seen = []

    def chat(self, **kw):
        self.calls += 1
        self.seen.append(kw)
        return self._results.pop(0) if self._results else {"text": "done", "tool_calls": []}


def _home(tmp_path):
    states = [_State("light.porch", "Porch Light"),
              _State("binary_sensor.front_door", "Front Door"),
              _State("device_tracker.phone", "Phone")]
    services = {"light": {"turn_on": None, "turn_off": None}}
    return _Hass(states, services, tmp_path / "automations.yaml")


# ── create_automation dry run (install=False) ────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_returns_preview_without_writing(creator, tmp_path):
    hass = _home(tmp_path)
    res = await creator.create_automation(
        hass, alias="Porch at dusk",
        trigger={"platform": "sun", "event": "sunset"},
        action={"service": "light.turn_on", "entity_id": "light.porch"},
        install=False)
    assert res["success"] is True and res["preview"] is True
    assert res["alias"] == "JARVIS · Porch at dusk"
    assert "light.porch" in res["yaml"]
    assert res["config"]["id"] == "jarvis_auto_porch_at_dusk"
    # nothing written or reloaded
    assert hass.services.calls == []
    assert not (tmp_path / "automations.yaml").exists()


@pytest.mark.asyncio
async def test_dry_run_still_validates_bad_names(creator, tmp_path):
    hass = _home(tmp_path)
    res = await creator.create_automation(
        hass, alias="x", trigger={"platform": "sun", "event": "sunset"},
        action={"service": "light.turn_on", "entity_id": "light.ghost"},
        validate=True, install=False)
    assert res["success"] is False
    assert "light.ghost" in res["invalid_entities"]


# ── draft_automation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_builds_validated_preview_without_installing(creator, tmp_path):
    hass = _home(tmp_path)
    client = _Client({"text": "", "tool_calls": [{"id": "1", "name": "create_automation", "args": {
        "alias": "Porch at dusk",
        "trigger": [{"trigger": "sun", "event": "sunset"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
    }}]})
    res = await creator.draft_automation(
        hass, "turn on the porch light at sunset", client=client)
    assert res["ok"] is True
    assert res["draft"]["args"]["alias"] == "Porch at dusk"
    assert "light.porch" in res["draft"]["yaml"]
    # the model was offered the home's real entities to reference
    sys_prompt = client.seen["messages"][0]["content"]
    assert "light.porch" in sys_prompt
    # nothing installed by a draft
    assert hass.services.calls == []
    assert not (tmp_path / "automations.yaml").exists()


@pytest.mark.asyncio
async def test_draft_searches_then_composes(creator, tmp_path):
    """Agentic path: the model resolves the device via search_entities, then
    composes with the real id — and the search result is threaded back to it."""
    hass = _home(tmp_path)
    client = _ScriptClient([
        {"text": "", "tool_calls": [{"id": "s1", "name": "search_entities",
                                     "args": {"query": "porch", "domain": "light"}}]},
        {"text": "", "tool_calls": [{"id": "c1", "name": "create_automation", "args": {
            "alias": "Porch at dusk",
            "trigger": [{"trigger": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}}]},
    ])
    res = await creator.draft_automation(hass, "porch light at sunset", client=client)
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]
    assert client.calls == 2
    second_round = client.seen[1]["messages"]
    assert any(m.get("role") == "tool" and "light.porch" in (m.get("content") or "")
               for m in second_round)
    assert hass.services.calls == []        # drafting never installs


@pytest.mark.asyncio
async def test_draft_retries_after_a_bad_name(creator, tmp_path):
    """Agentic retry: a first compose with an unknown entity fails validation; the
    failure is fed back and the model corrects it on the next round."""
    hass = _home(tmp_path)
    client = _ScriptClient([
        {"tool_calls": [{"id": "c1", "name": "create_automation", "args": {
            "alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.ghost"}}]}}]},
        {"tool_calls": [{"id": "c2", "name": "create_automation", "args": {
            "alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}}]},
    ])
    res = await creator.draft_automation(hass, "porch at dusk", client=client)
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]
    second_round = client.seen[1]["messages"]
    assert any(m.get("role") == "tool" and "light.ghost" in (m.get("content") or "")
               for m in second_round)


@pytest.mark.asyncio
async def test_draft_surfaces_invalid_names(creator, tmp_path):
    hass = _home(tmp_path)
    client = _Client({"tool_calls": [{"id": "c1", "name": "create_automation", "args": {
        "alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.ghost"}}],
    }}]})
    res = await creator.draft_automation(hass, "...", client=client)
    assert res["ok"] is False
    assert "light.ghost" in (res.get("invalid_entities") or {})
    assert res["draft"]["args"]["action"]  # the attempted draft is still returned


@pytest.mark.asyncio
async def test_draft_without_tool_call_returns_prose(creator, tmp_path):
    hass = _home(tmp_path)
    client = _Client({"text": "I need to know which light.", "tool_calls": []})
    res = await creator.draft_automation(hass, "do a thing", client=client)
    assert res["ok"] is False
    assert "which light" in res["error"]


@pytest.mark.asyncio
async def test_draft_requires_text_and_client(creator, tmp_path):
    hass = _home(tmp_path)
    assert (await creator.draft_automation(hass, "   ", client=_Client({})))["ok"] is False
    no_client = await creator.draft_automation(hass, "porch light at dusk", client=None)
    assert no_client["ok"] is False
    assert "reasoning provider" in no_client["error"]


@pytest.mark.asyncio
async def test_reviewed_draft_installs_through_create_automation(creator, tmp_path):
    """The install step writes the reviewed draft's args to automations.yaml."""
    hass = _home(tmp_path)
    args = {"alias": "Porch at dusk", "description": "",
            "trigger": [{"trigger": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
            "mode": "single"}
    res = await creator.create_automation(hass, validate=True, install=True, **args)
    assert res["success"] is True
    saved = yaml.safe_load((tmp_path / "automations.yaml").read_text())
    assert saved[0]["id"] == "jarvis_auto_porch_at_dusk"
    assert hass.services.calls == [("automation", "reload", {"blocking": True})]
