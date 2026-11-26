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
    def __init__(self, result, *, supports=False, oneshot=False):
        self._result = result
        self._supports = supports
        self._oneshot = oneshot
        self.seen = None

    def supports_tools(self):
        return self._supports

    def prefers_oneshot_compose(self):
        return self._oneshot

    def chat(self, **kw):
        self.seen = kw
        return self._result


class _ScriptClient:
    """LLMProvider stand-in that returns a scripted sequence of chat results,
    recording the messages it was called with each round (for agentic tests)."""
    def __init__(self, results, *, supports=False, oneshot=False):
        self._results = list(results)
        self._supports = supports
        self._oneshot = oneshot
        self.calls = 0
        self.seen = []

    def supports_tools(self):
        return self._supports

    def prefers_oneshot_compose(self):
        return self._oneshot

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
    # one-shot offers ONLY the native create_automation tool — never a search tool
    tool_names = {t["function"]["name"] for t in (client.seen.get("tools") or [])}
    assert tool_names == {"create_automation"}
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


@pytest.mark.asyncio
async def test_oneshot_empty_response_reports_thinking_budget(creator, tmp_path):
    """A thinking model (e.g. gemma4) can return EMPTY content — its whole budget
    spent on hidden reasoning. The composer must say so and name the remedy, not
    the misleading 'describe it more specifically' (the real-world #462 failure)."""
    hass = _home(tmp_path)
    client = _Client({"text": "", "finish_reason": "length", "reasoning": "",
                      "tool_calls": []}, supports=False)
    res = await creator.draft_automation(hass, "porch light at dusk", client=client)
    assert res["ok"] is False
    assert "empty response" in res["error"].lower()
    assert "thinking" in res["error"].lower()
    assert "describe it more specifically" not in res["error"].lower()


@pytest.mark.asyncio
async def test_oneshot_does_not_recover_automation_from_reasoning_channel(creator, tmp_path):
    """The private reasoning channel is scratch work — discarded drafts and
    half-formed JSON — not a committed answer. Even when a parseable automation
    lands there (content still empty), the composer must NOT lift it into a draft:
    only a native tool call or visible content counts. (Spec #40 Phase 1, 2D.)"""
    states = [_State("light.chaise_lamp", "Chaise Lamp"),
              _State("binary_sensor.living_room_presence", "Living Room Presence")]
    services = {"light": {"turn_on": None, "turn_off": None}}
    hass = _Hass(states, services, tmp_path / "automations.yaml")
    jdoc = json.dumps({
        "alias": "Chaise on presence",
        "trigger": [{"trigger": "state", "entity_id": "binary_sensor.living_room_presence", "to": "on"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.chaise_lamp"}}]})
    client = _Client({"text": "", "finish_reason": "length",
                      "reasoning": "Let me work it out. " + jdoc, "tool_calls": []},
                     supports=False)
    res = await creator.draft_automation(
        hass, "turn on the chaise lamp when living room presence detects someone",
        client=client)
    assert res["ok"] is False
    # nothing was lifted out of the scratchpad into an installable draft
    assert not res.get("draft")
    # and nothing was written
    assert not (tmp_path / "automations.yaml").exists()


@pytest.mark.asyncio
async def test_local_model_uses_oneshot_even_when_tool_capable(creator, tmp_path):
    """A local model that passes the tool probe (supports_tools=True) but declares
    prefers_oneshot_compose() must STILL draft one-shot — not be rerouted into the
    agentic loop it can't drive. This is the real v9.68.0 regression: Gemma began
    passing the probe and the composer logged 'agentic draft produced nothing'."""
    states = [_State("light.chaise_lamp", "Chaise Lamp"),
              _State("binary_sensor.living_room_presence", "Living Room Presence")]
    services = {"light": {"turn_on": None, "turn_off": None}}
    hass = _Hass(states, services, tmp_path / "automations.yaml")
    jdoc = json.dumps({
        "alias": "Chaise on presence",
        "trigger": [{"trigger": "state", "entity_id": "binary_sensor.living_room_presence", "to": "on"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.chaise_lamp"}}]})
    client = _Client({"text": jdoc, "tool_calls": []}, supports=True, oneshot=True)
    res = await creator.draft_automation(
        hass, "turn on the chaise lamp when living room presence detects someone",
        client=client)
    assert res["ok"] is True
    # one-shot path: only the native create_automation tool, never a search tool
    tool_names = {t["function"]["name"] for t in (client.seen.get("tools") or [])}
    assert tool_names == {"create_automation"}
    # the system prompt carried pre-ranked candidates (one-shot), not the agentic hint
    assert "binary_sensor.living_room_presence" in client.seen["messages"][0]["content"]


@pytest.mark.asyncio
async def test_tool_capable_cloud_model_still_uses_agentic(creator, tmp_path):
    """A model that passes the probe and does NOT prefer one-shot keeps the agentic
    loop — the local-model reroute must not swallow capable cloud providers."""
    hass = _home(tmp_path)   # light.porch
    client = _ScriptClient([
        {"text": "", "tool_calls": [{"id": "c1", "name": "create_automation", "args": {
            "alias": "Porch at dusk", "trigger": [{"trigger": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}}]},
    ], supports=True, oneshot=False)
    res = await creator.draft_automation(hass, "porch light at sunset", client=client)
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]
    # agentic path offers the search + compose tools (one-shot never would)
    assert any("tools" in s for s in client.seen)


@pytest.mark.asyncio
async def test_oneshot_uses_native_create_automation_tool_call(creator, tmp_path):
    """The one-shot path offers a single native create_automation tool and parses
    the model's NATIVE function call — Gemma 4's strength — not just raw JSON. Here
    the content is empty and the automation arrives purely as a tool call."""
    states = [_State("light.chaise_lamp", "Chaise Lamp"),
              _State("binary_sensor.living_room_presence", "Living Room Presence")]
    services = {"light": {"turn_on": None, "turn_off": None}}
    hass = _Hass(states, services, tmp_path / "automations.yaml")
    client = _Client({"text": "", "tool_calls": [{"id": "c1", "name": "create_automation",
        "args": {"alias": "Chaise on presence",
                 "trigger": [{"trigger": "state", "entity_id": "binary_sensor.living_room_presence", "to": "on"}],
                 "action": [{"action": "light.turn_on", "target": {"entity_id": "light.chaise_lamp"}}]}}]},
        supports=True, oneshot=True)
    res = await creator.draft_automation(
        hass, "chaise lamp on living room presence", client=client)
    assert res["ok"] is True
    assert res["draft"]["args"]["alias"] == "Chaise on presence"
    assert "light.chaise_lamp" in res["draft"]["yaml"]


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


# ── §2F: prompt, thinking, tools-free fallback, retry memory ─────────────────

@pytest.mark.asyncio
async def test_oneshot_prompt_teaches_correct_sun_condition_and_branching(creator, tmp_path):
    hass = _home(tmp_path)
    client = _Client({"text": "", "tool_calls": [{"id": "c1", "name": "create_automation",
        "args": {"alias": "Porch at dusk",
                 "trigger": [{"trigger": "sun", "event": "sunset"}],
                 "action": [{"action": "light.turn_on",
                             "target": {"entity_id": "light.porch"}}]}}]},
        supports=False, oneshot=True)
    res = await creator.draft_automation(hass, "porch light at dusk", client=client)
    assert res["ok"] is True
    sysmsg = client.seen["messages"][0]["content"]
    # teaches the correct night-only condition, not the always-false sun window
    assert "sun.sun" in sysmsg and "below_horizon" in sysmsg
    assert '"after":"sunset","before":"sunrise"' not in sysmsg
    assert "choose" in sysmsg                      # bidirectional branching example
    assert client.seen.get("thinking") is False    # passed defensively


@pytest.mark.asyncio
async def test_oneshot_falls_back_to_tools_free_on_tool_error(creator, tmp_path):
    hass = _home(tmp_path)
    jdoc = json.dumps({"alias": "Porch at dusk",
                       "trigger": [{"trigger": "sun", "event": "sunset"}],
                       "action": [{"action": "light.turn_on",
                                   "target": {"entity_id": "light.porch"}}]})
    seen = {"with_tools": 0, "without_tools": 0}

    class _ToolBreaker:
        def supports_tools(self):
            return False

        def prefers_oneshot_compose(self):
            return True

        def chat(self, **kw):
            if "tools" in kw:
                seen["with_tools"] += 1
                raise RuntimeError("this model does not support tool calling")
            seen["without_tools"] += 1
            return {"text": jdoc, "tool_calls": []}

    res = await creator.draft_automation(hass, "porch light at dusk", client=_ToolBreaker())
    assert res["ok"] is True
    assert "light.porch" in res["draft"]["yaml"]
    # tried tools once, then dropped to raw-JSON mode instead of failing the draft
    assert seen["with_tools"] == 1 and seen["without_tools"] >= 1


@pytest.mark.asyncio
async def test_oneshot_retry_remembers_native_draft(creator, tmp_path):
    hass = _home(tmp_path)   # has light.porch, not light.ghost
    bad = {"alias": "Ghostly", "trigger": [{"trigger": "sun", "event": "sunset"}],
           "action": [{"action": "light.turn_on", "target": {"entity_id": "light.ghost"}}]}
    good = {"alias": "Ghostly", "trigger": [{"trigger": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]}
    client = _ScriptClient([
        {"text": "", "tool_calls": [{"id": "c1", "name": "create_automation", "args": bad}]},
        {"text": "", "tool_calls": [{"id": "c2", "name": "create_automation", "args": good}]},
    ], supports=False, oneshot=True)
    res = await creator.draft_automation(hass, "turn on the ghost light at dusk", client=client)
    assert res["ok"] is True
    # the 2nd call's history carries the first (native, text-empty) draft — the
    # serialized args — not a blank assistant turn the model would forget.
    second_msgs = client.seen[1]["messages"]
    assert any(m["role"] == "assistant" and "Ghostly" in (m.get("content") or "")
               for m in second_msgs)
