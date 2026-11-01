"""Natural-language automation composer (Suggestions tab).

create_automation(install=False) is a dry run — validate + return the built
config/YAML without writing. draft_automation turns a plain-language request into
a reviewed, NOT-installed draft via one LLM tool-call, validating every name
against the live home. Install is a separate, explicit step.
"""
from __future__ import annotations

import json
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
    """Minimal LLMProvider stand-in: chat() returns a canned result dict.
    `supports=True` routes draft_automation down the agentic (native tool) path;
    `supports=False` routes it down the one-shot (local) path."""
    def __init__(self, result, *, supports=False):
        self._result = result
        self._supports = supports
        self.seen = None

    def supports_tools(self):
        return self._supports

    def chat(self, **kw):
        self.seen = kw
        return self._result


class _ScriptClient:
    """LLMProvider stand-in that returns a scripted sequence of chat results,
    recording the messages it was called with each round (for agentic tests)."""
    def __init__(self, results, *, supports=False):
        self._results = list(results)
        self._supports = supports
        self.calls = 0
        self.seen = []

    def supports_tools(self):
        return self._supports

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
    }}]}, supports=True)
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
    ], supports=True)
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
    ], supports=True)
    res = await creator.draft_automation(hass, "porch at dusk", client=client)
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]
    second_round = client.seen[1]["messages"]
    assert any(m.get("role") == "tool" and "light.ghost" in (m.get("content") or "")
               for m in second_round)


@pytest.mark.asyncio
async def test_draft_surfaces_invalid_names(creator, tmp_path):
    hass = _home(tmp_path)
    client = _Client({"text": "", "tool_calls": [{"id": "c1", "name": "create_automation", "args": {
        "alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.ghost"}}],
    }}]}, supports=True)
    res = await creator.draft_automation(hass, "...", client=client)
    assert res["ok"] is False
    assert "light.ghost" in (res.get("invalid_entities") or {})
    assert res["draft"]["args"]["action"]  # the attempted draft is still returned


@pytest.mark.asyncio
async def test_oneshot_compose_for_local_model(creator, tmp_path):
    """A local model (no function-calling) drafts via the ONE-SHOT path: JARVIS
    hands it the ranked candidate devices and it returns the automation in one
    reply — no search loop to get stuck on."""
    states = [_State("light.chaise_lamp", "Chaise Lamp"),
              _State("binary_sensor.living_room_presence", "Living Room Presence"),
              _State("binary_sensor.bedroom_presence", "Bedroom Presence")]
    services = {"light": {"turn_on": None, "turn_off": None}}
    hass = _Hass(states, services, tmp_path / "automations.yaml")
    jdoc = json.dumps({
        "alias": "Chaise on presence",
        "trigger": [{"trigger": "state", "entity_id": "binary_sensor.living_room_presence", "to": "on"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.chaise_lamp"}}]})
    client = _Client({"text": jdoc, "tool_calls": []}, supports=False)
    res = await creator.draft_automation(
        hass, "turn on the chaise lamp when the living room presence sensor detects someone",
        client=client)
    assert res["ok"] is True
    assert "light.chaise_lamp" in res["draft"]["yaml"]
    assert "tools" not in client.seen           # one-shot never offers function-calling tools
    sysmsg = client.seen["messages"][0]["content"]
    assert "binary_sensor.living_room_presence" in sysmsg   # ranked candidates handed over
    assert "light.chaise_lamp" in sysmsg
    assert hass.services.calls == []


@pytest.mark.asyncio
async def test_oneshot_handles_code_fences(creator, tmp_path):
    states = [_State("light.chaise_lamp", "Chaise Lamp")]
    services = {"light": {"turn_on": None}}
    hass = _Hass(states, services, tmp_path / "automations.yaml")
    fenced = "Sure:\n```json\n" + json.dumps({
        "alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.chaise_lamp"}}]}) + "\n```"
    client = _Client({"text": fenced, "tool_calls": []}, supports=False)
    res = await creator.draft_automation(hass, "chaise lamp at sunset", client=client)
    assert res["ok"] is True
    assert "light.chaise_lamp" in res["draft"]["yaml"]


@pytest.mark.asyncio
async def test_oneshot_picks_automation_among_extra_objects(creator, tmp_path):
    """gemma4:26b often emits stray objects alongside the automation (a leftover
    search, prose); one-shot picks the object that IS an automation."""
    hass = _home(tmp_path)   # light.porch
    reply = "\n".join([
        '```json\n{"tool": "search_entities", "args": {"query": "porch"}}\n```',
        '```json\n{"alias": "Porch at dusk", "trigger": [{"trigger": "sun", "event": "sunset"}],'
        ' "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}\n```',
    ])
    client = _Client({"text": reply, "tool_calls": []}, supports=False)
    res = await creator.draft_automation(hass, "porch light at sunset", client=client)
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]


@pytest.mark.asyncio
async def test_oneshot_corrects_bad_name(creator, tmp_path):
    """One-shot retry: the first reply uses a wrong id; validation fails, the bad
    name is fed back, and the second reply fixes it."""
    hass = _home(tmp_path)   # light.porch exists
    bad = json.dumps({"alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
                      "action": [{"action": "light.turn_on", "target": {"entity_id": "light.ghost"}}]})
    good = json.dumps({"alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
                       "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]})
    client = _ScriptClient([{"text": bad, "tool_calls": []},
                            {"text": good, "tool_calls": []}], supports=False)
    res = await creator.draft_automation(hass, "porch light at sunset", client=client)
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]
    assert client.calls == 2
    second = client.seen[1]["messages"]
    assert any(m.get("role") == "user" and "light.ghost" in (m.get("content") or "")
               for m in second)


@pytest.mark.asyncio
async def test_oneshot_surfaces_specific_error_on_giveup(creator, tmp_path):
    """If the model never produces a valid automation, the composer returns the
    SPECIFIC bad names (not the generic message), so the failure is diagnosable."""
    hass = _home(tmp_path)
    bad = json.dumps({"alias": "x", "trigger": [{"trigger": "sun", "event": "sunset"}],
                      "action": [{"action": "light.turn_on", "target": {"entity_id": "light.ghost"}}]})
    client = _Client({"text": bad, "tool_calls": []}, supports=False)   # always wrong
    res = await creator.draft_automation(hass, "ghost light at dusk", client=client)
    assert res["ok"] is False
    assert "light.ghost" in (res.get("invalid_entities") or {})


def test_extract_json_handles_prose_and_fences(creator):
    assert creator._extract_json('{"a": 1}') == {"a": 1}
    assert creator._extract_json('Here: {"a": {"b": 2}} done')["a"]["b"] == 2
    assert creator._extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert creator._extract_json("no json here") is None
    assert creator._extract_json("") is None


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
