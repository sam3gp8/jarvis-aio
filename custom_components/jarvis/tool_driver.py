"""
JARVIS — shared agentic tool-driver (local-capability parity).

A reusable native-or-JSON tool-calling loop so every agentic feature behaves the
SAME whether the configured model supports function-calling (every cloud model,
and some local ones) or not (e.g. Gemma via Ollama). This is the concrete form
of JARVIS's local-parity principle: *if the configured model lacks a capability
a feature needs, JARVIS provides it* — here, agentic tool use for a model that
can't call functions.

Two execution modes behind one interface:

  • native — the provider's own function-calling. The model emits tool_calls,
    which we execute via the handlers and feed back as ``tool`` messages.
  • json   — a tools-free protocol any instruction-following model can satisfy.
    The model emits ONE JSON *action* per turn, ``{"tool": <name>, "args": {…}}``,
    which we execute via the SAME handlers and feed back as a user message.

``supports_tools()`` chooses the mode up front with a cheap capability probe
(cached per model). A model the probe can't classify (e.g. a test stub, or a
client that exposes no capability signal) runs native with a JSON fallback: if
it never emits a tool call, the loop retries the request in JSON mode.

A handler is ``handler(args: dict) -> Done | str | <json-able> | awaitable``:
  • return :class:`Done` (``Done(value)``) to END the loop with ``value`` as
    the result (``run_tool_loop(...)["done"] is value``);
  • return a str (or any json-serialisable value) to feed it back to the model
    and CONTINUE the loop.

The loop never raises into its caller; a provider error ends it with the error
recorded on the returned dict.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

# How many model turns an agentic loop gets before it gives up. Shared default;
# callers can lower it (the composer runs a tighter budget).
MAX_DRIVER_STEPS = 8


class Done:
    """Sentinel wrapper a handler returns to END the loop.

    ``Done(value)`` stops the loop immediately and makes ``value`` the loop's
    result (surfaced as ``run_tool_loop(...)["done"]``). A handler that returns a
    plain string/dict instead feeds that back to the model and the loop
    continues. ``Done(None)`` is a valid "stop with no structured result".
    """

    __slots__ = ("value",)

    def __init__(self, value: Any = None):
        self.value = value


def _extract_json(text: str):
    """Best-effort: pull the first JSON object out of a model reply — handles
    ```json fences and surrounding prose. Returns a dict/list, or None. Shared so
    every JSON-protocol caller parses model output the same way."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def _as_text(value: Any) -> str:
    """Render a handler's return value as the text we feed back to the model."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except Exception:  # pragma: no cover - defensive
        return str(value)


# ── Capability probe ─────────────────────────────────────────────────────────
# "<provider>:<model>" → True/False, session-scoped. A probe is one cheap call,
# so caching per model keeps the overhead to once per distinct backend.
_TOOL_SUPPORT_CACHE: dict[str, bool] = {}

_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "ack",
        "description": "Acknowledge that you can call tools by calling this with ok=true.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}


def _cache_key(client) -> str:
    return f"{getattr(client, 'name', '?')}:{getattr(client, 'model', '?')}"


def reset_tool_support_cache() -> None:
    """Forget every probed capability (used by tests; harmless in production)."""
    _TOOL_SUPPORT_CACHE.clear()


async def supports_tools(hass, client) -> Optional[bool]:
    """Whether ``client`` can do native function-calling.

    Returns True / False when the provider states it (``client.supports_tools()``
    — cloud providers return True; a local provider that can't know returns None,
    which triggers a one-time probe cached per model). Returns None when the
    client exposes no capability signal at all (e.g. a bare test stub) — the
    caller then runs native with a JSON fallback rather than probing, so it never
    burns a scripted turn on a probe. Never raises.
    """
    fn = getattr(client, "supports_tools", None)
    if not callable(fn):
        return None
    try:
        raw = fn()
    except Exception:  # pragma: no cover - defensive
        return None
    if raw is True or raw is False:
        return raw
    # raw is None → the provider is telling us to probe (e.g. Ollama: depends on
    # the pulled model). Probe once and cache the verdict per model.
    key = _cache_key(client)
    if key in _TOOL_SUPPORT_CACHE:
        return _TOOL_SUPPORT_CACHE[key]
    verdict = await _probe_tools(hass, client)
    _TOOL_SUPPORT_CACHE[key] = verdict
    _LOGGER.debug("tool-support probe: %s → %s", key, verdict)
    return verdict


async def _probe_tools(hass, client) -> bool:
    """Ask the model to call one trivial tool. If it emits a tool_call, it can do
    function-calling; if it answers in prose (or the call errors), it can't."""
    def _call():
        return client.chat(
            messages=[
                {"role": "system",
                 "content": "You can call tools. Call the `ack` tool with ok=true."},
                {"role": "user", "content": "ack"},
            ],
            tools=[_PROBE_TOOL], max_tokens=64, temperature=0.0)
    try:
        res = await hass.async_add_executor_job(_call)
    except Exception as exc:
        _LOGGER.debug("tool-support probe failed (%s) — assuming no tools", exc)
        return False
    return bool((res or {}).get("tool_calls"))


# ── The loop ─────────────────────────────────────────────────────────────────

def _json_protocol_prompt(tools: list[dict], hint: str = "") -> str:
    """Describe the available tools as a tools-free JSON protocol a non-function-
    calling model can follow. Generated from the SAME tool schemas the native
    path uses, so the two modes stay in lock-step."""
    lines = [
        "You do not have a function-calling API. Instead, use tools by replying "
        "with EXACTLY ONE JSON object and NOTHING else:",
        '  {"tool": "<name>", "args": { ... }}',
        "Available tools:",
    ]
    for t in tools or []:
        fn = (t or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        desc = (fn.get("description") or "").strip()
        params = ((fn.get("parameters") or {}).get("properties") or {})
        required = set((fn.get("parameters") or {}).get("required") or [])
        arg_bits = ", ".join(
            f'"{k}"' + (" (required)" if k in required else "")
            for k in params) or "(none)"
        lines.append(f'  • {name} — {desc}  args: {{{arg_bits}}}')
    lines.append('When you are done and need no more tools, reply with '
                 '{"tool": "final", "text": "<your answer>"}.')
    if hint:
        lines.append(hint.strip())
    return "\n".join(lines)


async def _dispatch(handlers: dict, name: str, args: dict):
    """Run the handler for ``name``. Returns its value (``Done`` or json-able),
    or an error string the loop feeds back. Never raises."""
    handler = handlers.get(name)
    if handler is None:
        return f'unknown tool "{name}"'
    try:
        out = handler(args or {})
        if inspect.isawaitable(out):
            out = await out
    except Exception as exc:
        _LOGGER.debug("tool handler %s failed: %s", name, exc)
        return f"tool {name} failed: {exc}"
    return out


async def _run_native(hass, client, *, tools, handlers, max_steps, temperature,
                      allow_fallback, system=None, user=None, messages=None) -> dict:
    """Native function-calling loop. Returns the standard result dict, or — when
    ``allow_fallback`` and the model never emits a tool call — a marker dict
    ``{"_fallback": True, "prose": …}`` so the caller can retry in JSON mode.

    Starts from a pre-built ``messages`` history when given (the system turn plus
    conversation), otherwise from ``system`` + ``user``."""
    working: list[dict] = (
        [dict(m) for m in messages] if messages is not None
        else [{"role": "system", "content": system or ""},
              {"role": "user", "content": user or ""}])
    saw_call = False
    for step in range(max_steps):
        def _call():
            return client.chat(messages=working, tools=tools,
                               max_tokens=1024, temperature=temperature)
        try:
            res = await hass.async_add_executor_job(_call)
        except Exception as exc:
            return {"done": None, "text": "", "used": "native",
                    "steps": step + 1, "error": str(exc)}
        calls = (res or {}).get("tool_calls") or []
        text = ((res or {}).get("text") or "").strip()
        if not calls:
            if not saw_call and allow_fallback:
                return {"_fallback": True, "prose": text}
            return {"done": None, "text": text, "used": "native",
                    "steps": step + 1, "saw_tool_call": saw_call}
        saw_call = True
        working.append({
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {"id": c.get("id") or f"call_{i}", "type": "function",
                 "function": {"name": c.get("name"),
                              "arguments": json.dumps(c.get("args") or {})}}
                for i, c in enumerate(calls)],
        })
        for i, call in enumerate(calls):
            cid = call.get("id") or f"call_{i}"
            out = await _dispatch(handlers, call.get("name"), call.get("args") or {})
            if isinstance(out, Done):
                return {"done": out.value, "text": text, "used": "native",
                        "steps": step + 1, "saw_tool_call": True}
            working.append({"role": "tool", "tool_call_id": cid,
                            "content": _as_text(out)})
    return {"done": None, "text": "", "used": "native", "steps": max_steps,
            "saw_tool_call": saw_call, "exhausted": True}


_FINAL_TOOLS = ("final", "answer", "done")


async def _run_json(hass, client, *, tools, handlers, max_steps, temperature,
                    default_tool=None, json_hint="", prior_prose=None,
                    system=None, user=None, messages=None) -> dict:
    """Tools-free JSON-action loop for a model without function-calling. The
    model drives the SAME handlers by emitting one JSON action per turn.

    Starts from a pre-built ``messages`` history when given — the JSON protocol is
    appended to its system turn (or a system turn is prepended) — otherwise from
    ``system`` + ``user`` with the protocol appended to the system prompt."""
    proto = _json_protocol_prompt(tools, json_hint)
    if messages is not None:
        base = [dict(m) for m in messages]
        if base and base[0].get("role") == "system":
            base[0]["content"] = (base[0].get("content") or "") + "\n\n" + proto
        else:
            base = [{"role": "system", "content": proto}] + base
        working: list[dict] = base
    else:
        working = [{"role": "system", "content": (system or "") + "\n\n" + proto},
                   {"role": "user", "content": user or ""}]
    messages = working
    # JSON following is most reliable at a low temperature regardless of the
    # caller's native setting.
    temp = min(temperature, 0.3)
    for step in range(max_steps):
        def _call():
            return client.chat(messages=messages, max_tokens=1024, temperature=temp)
        try:
            res = await hass.async_add_executor_job(_call)
        except Exception as exc:
            return {"done": None, "text": "", "used": "json",
                    "steps": step + 1, "error": str(exc)}
        raw = ((res or {}).get("text") or "")
        messages.append({"role": "assistant", "content": raw[:1200]})
        action = _extract_json(raw)
        tool = action.get("tool") if isinstance(action, dict) else None

        # Explicit "I'm done" → final answer.
        if tool in _FINAL_TOOLS:
            final = (action.get("text") or action.get("answer") or "").strip()
            return {"done": None, "text": final or raw.strip(),
                    "used": "json", "steps": step + 1}

        # A known tool, OR an unlabelled JSON object routed to the default tool
        # (so a model that emits the bare payload, e.g. an automation object with
        # no "tool" key, still reaches the right handler).
        target = tool if (tool and tool in handlers) else None
        if target is None and isinstance(action, dict) and default_tool in handlers \
                and (tool is None or tool == default_tool):
            target = default_tool
        if target is not None:
            args = action.get("args") if isinstance(action, dict) else None
            if not isinstance(args, dict):
                args = {k: v for k, v in action.items() if k != "tool"} \
                    if isinstance(action, dict) else {}
            out = await _dispatch(handlers, target, args)
            if isinstance(out, Done):
                return {"done": out.value, "text": "", "used": "json",
                        "steps": step + 1}
            messages.append({"role": "user",
                             "content": _as_text(out) +
                             "\nReply with your next JSON action."})
            continue

        # Unparseable / unknown — nudge back to the protocol and try again.
        messages.append({"role": "user", "content":
                         'Reply with ONE JSON action only: '
                         '{"tool":"<name>","args":{...}} '
                         'or {"tool":"final","text":"<answer>"}.'})

    return {"done": None, "text": (prior_prose or "").strip(),
            "used": "json", "steps": max_steps, "exhausted": True}


async def run_tool_loop(hass, client, *, system=None, user=None, messages=None,
                        tools, handlers, json_system=None, json_hint="",
                        default_tool=None, max_steps=MAX_DRIVER_STEPS,
                        temperature=0.4, native=None) -> dict:
    """Drive an agentic tool loop, native or JSON, picking the mode by capability.

    Args:
      system:       system prompt for native mode (task + any context).
      user:         the user request.
      messages:     a pre-built message history ([system, …conversation]) to use
                    INSTEAD of ``system``+``user`` — for an agent with prior turns.
                    In JSON mode the protocol is appended to its system turn.
      tools:        OpenAI-style function tool schemas — used directly in native
                    mode and to generate the JSON protocol description.
      handlers:     ``{name: handler}``; a handler takes an args dict and returns
                    :class:`Done` to finish or a json-able value to continue. Keep
                    every name a model might use (native tool names AND any short
                    JSON verbs) in this map.
      json_system:  system prompt for JSON mode, if it should differ from
                    ``system`` (e.g. a request-biased entity hint). Defaults to
                    ``system``. Ignored when ``messages`` is given.
      json_hint:    extra protocol guidance appended to the JSON system prompt
                    (concrete examples for the task).
      default_tool: the handler an unlabelled JSON object is routed to, for a
                    model that emits a bare payload without a ``tool`` key.
      native:       force the mode (True/False). Omit to decide by capability.

    Returns a dict: ``{"done": <Done value or None>, "text": <final prose>,
    "used": "native"|"json", "steps": n, …}``. ``done`` is set iff a handler
    finished the loop with :class:`Done`.
    """
    cap = native if native is not None else await supports_tools(hass, client)
    # cap: True → native only · False → JSON only · None → native w/ JSON fallback.
    if cap is False:
        return await _run_json(
            hass, client, system=(json_system or system), user=user,
            messages=messages, tools=tools, handlers=handlers,
            max_steps=max_steps, temperature=temperature,
            default_tool=default_tool, json_hint=json_hint)

    res = await _run_native(
        hass, client, system=system, user=user, messages=messages, tools=tools,
        handlers=handlers, max_steps=max_steps, temperature=temperature,
        allow_fallback=(cap is None))
    if res.get("_fallback"):
        return await _run_json(
            hass, client, system=(json_system or system), user=user,
            messages=messages, tools=tools, handlers=handlers,
            max_steps=max_steps, temperature=temperature,
            default_tool=default_tool, json_hint=json_hint,
            prior_prose=res.get("prose"))
    return res
