"""Shared agentic tool-driver (local-capability parity).

run_tool_loop drives a native function-calling loop OR a tools-free JSON-action
loop over the SAME handlers, picking the mode by the model's probed capability,
so a model without function-calling gets the same agentic capability. A handler
returns Done(value) to end the loop or a value to feed back and continue.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def td(load):
    return load("tool_driver")


class _Hass:
    async def async_add_executor_job(self, func, *a):
        return func(*a)


class _Client:
    """Scripted client that exposes a fixed supports_tools() verdict."""
    def __init__(self, results, *, supports=True, name="fake", model="m"):
        self._results = list(results)
        self._supports = supports
        self.name = name
        self.model = model
        self.calls = 0
        self.seen = []

    def supports_tools(self):
        return self._supports

    def chat(self, **kw):
        self.calls += 1
        self.seen.append(kw)
        return self._results.pop(0) if self._results else {"text": "", "tool_calls": []}


class _BareClient:
    """A client with NO capability signal — supports_tools is decided by runtime
    fallback (native first, then JSON on no tool call)."""
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.seen = []

    def chat(self, **kw):
        self.calls += 1
        self.seen.append(kw)
        return self._results.pop(0) if self._results else {"text": "", "tool_calls": []}


def _tool(name, desc="", props=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}}}}


# ── _extract_json ─────────────────────────────────────────────────────────────

def test_extract_json(td):
    assert td._extract_json('{"a": 1}') == {"a": 1}
    assert td._extract_json('here: {"a": {"b": 2}} ok')["a"]["b"] == 2
    assert td._extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert td._extract_json("no json") is None
    assert td._extract_json("") is None


def test_extract_json_handles_template_braces(td):
    # A Home Assistant template value contains { } inside a STRING — the brace
    # count must not be thrown off by it (this broke the pre-9.63 parser).
    s = json.dumps({"tool": "create_automation", "args": {
        "alias": "x",
        "trigger": [{"trigger": "template",
                     "value_template": "{{ is_state('binary_sensor.x','on') }}"}],
        "action": [{"action": "light.turn_on", "target": {"entity_id": "light.y"}}]}})
    obj = td._extract_json("Sure, here it is: " + s + "\nLet me know!")
    assert obj["tool"] == "create_automation"
    assert "{{" in obj["args"]["trigger"][0]["value_template"]
    assert obj["args"]["action"][0]["action"] == "light.turn_on"


def test_extract_json_prefers_action_object(td):
    # A reasoning blob precedes the real action — the action must win.
    s = 'Planning: {"note":"let me think"} and then {"tool":"final","text":"hi"}'
    assert td._extract_json(s) == {"tool": "final", "text": "hi"}


# ── capability probe ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supports_tools_explicit(td):
    assert await td.supports_tools(_Hass(), _Client([], supports=True)) is True
    assert await td.supports_tools(_Hass(), _Client([], supports=False)) is False
    # a client without the method → unknown, no probe
    assert await td.supports_tools(_Hass(), _BareClient([])) is None


@pytest.mark.asyncio
async def test_supports_tools_probes_once_and_caches(td):
    td.reset_tool_support_cache()

    class _Probe:
        name = "ollama"

        def __init__(self, emits_call, model):
            self._emits = emits_call
            self.model = model
            self.chat_calls = 0

        def supports_tools(self):
            return None  # "probe me"

        def chat(self, **kw):
            self.chat_calls += 1
            return {"text": "", "tool_calls": (
                [{"id": "a", "name": "ack", "args": {"ok": True}}] if self._emits else [])}

    gemma = _Probe(False, "gemma")
    assert await td.supports_tools(_Hass(), gemma) is False
    assert gemma.chat_calls == 1
    # cached — no second probe
    assert await td.supports_tools(_Hass(), gemma) is False
    assert gemma.chat_calls == 1

    llama = _Probe(True, "llama3.1")
    assert await td.supports_tools(_Hass(), llama) is True
    assert llama.chat_calls == 1


@pytest.mark.asyncio
async def test_probe_failure_means_no_tools(td):
    td.reset_tool_support_cache()

    class _Boom:
        name = "ollama"
        model = "broken"

        def supports_tools(self):
            return None

        def chat(self, **kw):
            raise RuntimeError("no tool support")

    assert await td.supports_tools(_Hass(), _Boom()) is False


# ── native loop ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_native_done_ends_loop(td):
    client = _Client([{"text": "", "tool_calls": [
        {"id": "1", "name": "finish", "args": {"x": 1}}]}], supports=True)

    async def _finish(args):
        return td.Done({"got": args.get("x")})

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u",
        tools=[_tool("finish")], handlers={"finish": _finish})
    assert res["done"] == {"got": 1}
    assert res["used"] == "native"
    assert "tools" in client.seen[0]       # native offered tools


@pytest.mark.asyncio
async def test_native_threads_results_across_steps(td):
    client = _Client([
        {"tool_calls": [{"id": "s", "name": "lookup", "args": {"q": "alpha"}}]},
        {"tool_calls": [{"id": "f", "name": "finish", "args": {}}]},
    ], supports=True)

    def _lookup(args):
        return f"result for {args['q']}"

    async def _finish(args):
        return td.Done("ok")

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u",
        tools=[_tool("lookup"), _tool("finish")],
        handlers={"lookup": _lookup, "finish": _finish})
    assert res["done"] == "ok"
    assert client.calls == 2
    second = client.seen[1]["messages"]
    assert any(m.get("role") == "tool" and "result for alpha" in (m.get("content") or "")
               for m in second)


@pytest.mark.asyncio
async def test_native_prose_is_final_for_capable_model(td):
    client = _Client([{"text": "the answer is 42", "tool_calls": []}], supports=True)
    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("x")], handlers={})
    assert res["done"] is None
    assert res["text"] == "the answer is 42"
    assert res["used"] == "native"


@pytest.mark.asyncio
async def test_native_handler_error_feeds_back_and_continues(td):
    client = _Client([
        {"tool_calls": [{"id": "1", "name": "boom", "args": {}}]},
        {"tool_calls": [{"id": "2", "name": "finish", "args": {}}]},
    ], supports=True)

    def _boom(args):
        raise ValueError("kaboom")

    async def _finish(args):
        return td.Done("recovered")

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u",
        tools=[_tool("boom"), _tool("finish")],
        handlers={"boom": _boom, "finish": _finish})
    assert res["done"] == "recovered"
    assert any("kaboom" in (m.get("content") or "") for m in client.seen[1]["messages"])


@pytest.mark.asyncio
async def test_native_exhaustion(td):
    # Never returns a Done and keeps calling the same tool → exhausts max_steps.
    client = _Client([{"tool_calls": [{"id": "1", "name": "spin", "args": {}}]}] * 10,
                     supports=True)

    def _spin(args):
        return "still going"

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("spin")],
        handlers={"spin": _spin}, max_steps=3)
    assert res["done"] is None
    assert res.get("exhausted") is True
    assert res["steps"] == 3


# ── JSON loop ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_json_mode_when_not_capable(td):
    client = _Client([
        {"text": json.dumps({"tool": "finish", "args": {"x": 2}}), "tool_calls": []},
    ], supports=False)

    async def _finish(args):
        return td.Done(args.get("x"))

    res = await td.run_tool_loop(
        _Hass(), client, system="base", user="u",
        tools=[_tool("finish", "end it", {"x": {"type": "integer"}})],
        handlers={"finish": _finish})
    assert res["done"] == 2
    assert res["used"] == "json"
    assert "tools" not in client.seen[0]              # JSON mode offers no tools
    sys_prompt = client.seen[0]["messages"][0]["content"]
    assert "JSON" in sys_prompt and "finish" in sys_prompt   # protocol injected


@pytest.mark.asyncio
async def test_json_final_answer(td):
    client = _Client([
        {"text": json.dumps({"tool": "final", "text": "done here"}), "tool_calls": []},
    ], supports=False)
    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[], handlers={})
    assert res["done"] is None
    assert res["text"] == "done here"
    assert res["used"] == "json"


@pytest.mark.asyncio
async def test_json_search_then_finish(td):
    client = _Client([
        {"text": json.dumps({"tool": "lookup", "args": {"q": "alpha"}}), "tool_calls": []},
        {"text": json.dumps({"tool": "finish", "args": {}}), "tool_calls": []},
    ], supports=False)

    def _lookup(args):
        return f"found {args['q']}"

    async def _finish(args):
        return td.Done("ok")

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u",
        tools=[_tool("lookup"), _tool("finish")],
        handlers={"lookup": _lookup, "finish": _finish})
    assert res["done"] == "ok"
    assert client.calls == 2
    # the lookup result was threaded back as a user message before the finish
    assert any(m.get("role") == "user" and "found alpha" in (m.get("content") or "")
               for m in client.seen[1]["messages"])


@pytest.mark.asyncio
async def test_json_default_tool_routes_bare_object(td):
    client = _Client([
        {"text": json.dumps({"alias": "x", "trigger": [1], "action": [2]}),
         "tool_calls": []},
    ], supports=False)

    async def _create(args):
        return td.Done(args)

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("create")],
        handlers={"create": _create}, default_tool="create")
    assert res["done"]["alias"] == "x"


@pytest.mark.asyncio
async def test_json_nudges_on_garbage_then_recovers(td):
    client = _Client([
        {"text": "I am not sure what to do", "tool_calls": []},
        {"text": json.dumps({"tool": "finish", "args": {}}), "tool_calls": []},
    ], supports=False)

    async def _finish(args):
        return td.Done("ok")

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("finish")],
        handlers={"finish": _finish})
    assert res["done"] == "ok"
    # a protocol nudge was sent after the unparseable reply
    assert any(m.get("role") == "user" and "JSON action" in (m.get("content") or "")
               for m in client.seen[1]["messages"])


# ── native → JSON fallback (unknown capability) ───────────────────────────────

@pytest.mark.asyncio
async def test_fallback_native_to_json_when_unknown(td):
    client = _BareClient([
        {"text": "", "tool_calls": []},                                   # native: no call
        {"text": json.dumps({"tool": "finish", "args": {}}), "tool_calls": []},  # JSON
    ])

    async def _finish(args):
        return td.Done("done")

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("finish")],
        handlers={"finish": _finish})
    assert res["used"] == "json"
    assert res["done"] == "done"
    assert "tools" in client.seen[0]          # native tried first
    assert "tools" not in client.seen[1]      # then JSON


@pytest.mark.asyncio
async def test_json_closing_gives_final_answer_on_exhaustion(td):
    # The model loops a tool without ever finishing; the loop must not end empty —
    # the closing step asks once more and returns the answer (fixes the gemma
    # "web_research nine times then nothing" symptom).
    client = _Client([
        {"text": json.dumps({"tool": "lookup", "args": {"q": "a"}}), "tool_calls": []},
        {"text": json.dumps({"tool": "lookup", "args": {"q": "a"}}), "tool_calls": []},
        {"text": "The answer is 42.", "tool_calls": []},   # closing reply
    ], supports=False)

    def _lookup(args):
        return "nothing useful"

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("lookup")],
        handlers={"lookup": _lookup}, max_steps=2)
    assert res["exhausted"] is True
    assert res["text"] == "The answer is 42."
    assert client.calls == 3   # 2 steps + 1 closing


@pytest.mark.asyncio
async def test_json_closing_dispatches_default_tool(td):
    # A composer-style loop (default_tool set) that only searched can still close
    # by finally emitting its create on the closing step.
    client = _Client([
        {"text": json.dumps({"tool": "search", "args": {"q": "x"}}), "tool_calls": []},
        {"text": json.dumps({"tool": "search", "args": {"q": "x"}}), "tool_calls": []},
        {"text": json.dumps({"alias": "A", "trigger": [1], "action": [2]}),
         "tool_calls": []},                                 # closing create (bare)
    ], supports=False)

    def _search(args):
        return "no matches"

    async def _create(args):
        return td.Done(args)

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u",
        tools=[_tool("search"), _tool("create")],
        handlers={"search": _search, "create": _create},
        default_tool="create", max_steps=2)
    assert res["done"]["alias"] == "A"
    assert client.calls == 3


@pytest.mark.asyncio
async def test_json_repeat_guard_nudges(td):
    client = _Client([
        {"text": json.dumps({"tool": "lookup", "args": {"q": "a"}}), "tool_calls": []},
        {"text": json.dumps({"tool": "lookup", "args": {"q": "a"}}), "tool_calls": []},
        {"text": json.dumps({"tool": "final", "text": "done"}), "tool_calls": []},
    ], supports=False)

    def _lookup(args):
        return "nope"

    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("lookup")],
        handlers={"lookup": _lookup}, max_steps=5)
    assert res["text"] == "done"
    # the repeated lookup got a "do NOT repeat" nudge, visible to the 3rd call
    assert any("do NOT repeat" in (m.get("content") or "")
               for m in client.seen[2]["messages"])


@pytest.mark.asyncio
async def test_messages_history_native(td):
    # A pre-built history is used verbatim as the loop's starting messages.
    history = [{"role": "system", "content": "SYS"},
               {"role": "user", "content": "u1"},
               {"role": "assistant", "content": "a1"},
               {"role": "user", "content": "u2"}]
    client = _Client([{"text": "final answer", "tool_calls": []}], supports=True)
    res = await td.run_tool_loop(
        _Hass(), client, messages=history, tools=[_tool("x")], handlers={})
    assert res["text"] == "final answer"
    sent = client.seen[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[0]["content"] == "SYS"


@pytest.mark.asyncio
async def test_messages_history_json_appends_protocol(td):
    history = [{"role": "system", "content": "SYS"},
               {"role": "user", "content": "do it"}]
    client = _Client([
        {"text": json.dumps({"tool": "finish", "args": {}}), "tool_calls": []},
    ], supports=False)

    async def _finish(args):
        return td.Done("ok")

    res = await td.run_tool_loop(
        _Hass(), client, messages=history, tools=[_tool("finish", "end")],
        handlers={"finish": _finish})
    assert res["done"] == "ok"
    # the protocol was appended to the EXISTING system turn, not a new one
    sys_turn = client.seen[0]["messages"][0]
    assert sys_turn["role"] == "system"
    assert sys_turn["content"].startswith("SYS")
    assert "JSON" in sys_turn["content"] and "finish" in sys_turn["content"]


@pytest.mark.asyncio
async def test_native_forced_skips_probe(td):
    # native=True forces the native path even for a no-signal client, and prose
    # is the final answer (no fallback).
    client = _BareClient([{"text": "hi there", "tool_calls": []}])
    res = await td.run_tool_loop(
        _Hass(), client, system="s", user="u", tools=[_tool("x")],
        handlers={}, native=True)
    assert res["used"] == "native"
    assert res["text"] == "hi there"
    assert client.calls == 1
