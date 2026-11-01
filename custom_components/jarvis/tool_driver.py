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


def _iter_json_objects(text: str):
    """Yield each top-level ``{...}`` span in ``text``, honoring quoted strings
    and escapes — so braces inside a string VALUE (e.g. a Home Assistant template
    ``"{{ is_state('x','on') }}"``) don't throw off the brace count. Skips a
    truncated/unbalanced object rather than mis-pairing. Best-effort; never raises."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        matched = False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    matched = True
                    break
            j += 1
        i = j + 1 if matched else i + 1


# Keys that mark an object as an intended action/automation rather than a stray
# JSON blob in the model's reasoning. Checked strongest-first in _extract_json.
def _extract_json(text: str):
    """Best-effort: pull the INTENDED JSON object out of a model reply. Handles
    ```json fences, surrounding prose/reasoning, several objects in one reply, and
    braces inside string values (HA templates). Prefers an action/automation-shaped
    object over a stray blob; falls back to the first object that parses. Returns a
    dict/list, or None. Shared so every JSON-protocol caller parses the same way."""
    if not text:
        return None
    t = re.sub(r"```(?:json)?", " ", text)
    objs: list = []
    for span in _iter_json_objects(t):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        if isinstance(obj, (dict, list)):
            objs.append(obj)
    if not objs:
        return None
    # 1) a clear action/automation: a tool verb, a wrapped automation, or a bare
    #    automation (trigger + action).
    for obj in objs:
        if isinstance(obj, dict) and (
                "tool" in obj or "automation" in obj
                or ("trigger" in obj and "action" in obj)):
            return obj
    # 2) a weaker signal (a final answer, or a named draft).
    for obj in objs:
        if isinstance(obj, dict) and (
                "alias" in obj or "answer" in obj or "text" in obj):
            return obj
    # 3) whatever parsed first.
    return objs[0]


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
    lines.append(
        "Use a tool only when it adds something new. NEVER repeat a tool with the "
        "same or similar arguments — if it did not help once it will not help "
        "again; give your final answer instead. Prefer finishing over exploring: "
        "as soon as you can answer or act, do it.")
    if hint:
        lines.append(hint.strip())
    return "\n".join(lines)


def _extract_json_actions(text: str) -> list:
    """Every parseable JSON object in a model reply, in order. A weak local model
    often emits several actions in ONE message — e.g. two `search_entities` AND
    the `create_automation` — so the loop must see all of them, not just the first
    (observed with gemma4:26b: it plans the whole sequence up front)."""
    if not text:
        return []
    t = re.sub(r"```(?:json)?", " ", text)
    out: list = []
    for span in _iter_json_objects(t):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _choose_action(actions: list, handlers: dict, default_tool):
    """Pick which of several emitted actions to run. Prefer the FINISHING action
    (the one that shares a handler with ``default_tool`` — the composer's create)
    over an intermediate one (a search), so a model that emits its whole plan in
    one reply reaches the goal instead of looping on the first step. Returns
    ``(name, args)`` or ``(None, {})``."""
    resolved = []
    for a in actions:
        t, ar = _resolve_target(a, handlers, default_tool)
        if t is not None:
            resolved.append((t, ar))
    if not resolved:
        return None, {}
    if default_tool and default_tool in handlers:
        dh = handlers.get(default_tool)
        for t, ar in resolved:
            if handlers.get(t) is dh:
                return t, ar
    return resolved[0]


def _resolve_target(action, handlers: dict, default_tool):
    """From a parsed JSON action, work out which handler to run and with what
    args. Returns ``(name, args)`` or ``(None, {})``. A known ``tool`` wins; an
    unlabelled object (or one naming the default tool) routes to ``default_tool``
    so a model that emits the bare payload still reaches the right handler."""
    if not isinstance(action, dict):
        return None, {}
    tool = action.get("tool")
    target = tool if (tool and tool in handlers) else None
    if target is None and default_tool in handlers and (tool is None or tool == default_tool):
        target = default_tool
    if target is None:
        return None, {}
    args = action.get("args")
    if not isinstance(args, dict):
        args = {k: v for k, v in action.items() if k != "tool"}
    return target, args


# Default closing instruction when a JSON loop runs out of steps without finishing.
_DEFAULT_CLOSING = (
    "You cannot use any more tools now. Give your FINAL answer to the user in your "
    "own words, based on what you already have. Reply with "
    '{"tool":"final","text":"<your answer>"} — or just the answer as plain text.')


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
                    closing_prompt=None, system=None, user=None, messages=None) -> dict:
    """Tools-free JSON-action loop for a model without function-calling. The
    model drives the SAME handlers by emitting one JSON action per turn.

    Starts from a pre-built ``messages`` history when given — the JSON protocol is
    appended to its system turn (or a system turn is prepended) — otherwise from
    ``system`` + ``user`` with the protocol appended to the system prompt.

    Two convergence guards keep a weak local model from spinning (observed with
    gemma4:26b looping web_research nine times then returning nothing):
      - a repeat guard tells the model when it just reran a tool to no effect, and
      - a CLOSING step: if the loop runs out of steps without finishing, it asks
        once more for the final answer (or, for a composer with ``default_tool``,
        the create), so the loop never ends empty."""
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
    last_raw = ""
    seen_calls: set = set()

    async def _ask():
        def _call():
            return client.chat(messages=messages, max_tokens=1024, temperature=temp)
        return await hass.async_add_executor_job(_call)

    for step in range(max_steps):
        try:
            res = await _ask()
        except Exception as exc:
            return {"done": None, "text": (prior_prose or "").strip(), "used": "json",
                    "steps": step + 1, "error": str(exc), "last_raw": last_raw[:300]}
        raw = ((res or {}).get("text") or "")
        last_raw = raw or last_raw
        messages.append({"role": "assistant", "content": raw[:1200]})
        actions = _extract_json_actions(raw)

        # Explicit "I'm done" → final answer (whichever action carries it).
        fin = next((a for a in actions if a.get("tool") in _FINAL_TOOLS), None)
        if fin is not None:
            final = (fin.get("text") or fin.get("answer") or "").strip()
            return {"done": None, "text": final or raw.strip(),
                    "used": "json", "steps": step + 1, "last_raw": raw[:300]}

        # A weak model often emits its whole plan in one reply (two searches AND
        # the create); prefer the finishing action so it reaches the goal.
        target, args = _choose_action(actions, handlers, default_tool)
        if target is not None:
            out = await _dispatch(handlers, target, args)
            if isinstance(out, Done):
                return {"done": out.value, "text": "", "used": "json",
                        "steps": step + 1}
            sig = f"{target}:{json.dumps(args, sort_keys=True, default=str)[:160]}"
            repeat = sig in seen_calls
            seen_calls.add(sig)
            nudge = (" You already tried that and it did not help — do NOT repeat it. "
                     "Try something clearly different, or finish with "
                     '{"tool":"final","text":"..."}.') if repeat else ""
            messages.append({"role": "user",
                             "content": _as_text(out) +
                             "\nReply with your next JSON action." + nudge})
            continue

        # Unparseable / unknown — nudge back to the protocol and try again.
        messages.append({"role": "user", "content":
                         'Reply with ONE JSON action only: '
                         '{"tool":"<name>","args":{...}} '
                         'or {"tool":"final","text":"<answer>"}.'})

    # Out of steps — one closing attempt so the loop never ends empty.
    messages.append({"role": "user", "content": closing_prompt or _DEFAULT_CLOSING})
    try:
        res = await _ask()
    except Exception as exc:
        return {"done": None, "text": (prior_prose or "").strip(), "used": "json",
                "steps": max_steps + 1, "exhausted": True, "error": str(exc),
                "last_raw": last_raw[:300]}
    raw = ((res or {}).get("text") or "")
    last_raw = raw or last_raw
    actions = _extract_json_actions(raw)
    closing_final = next((a for a in actions if a.get("tool") in _FINAL_TOOLS), None)
    # A composer (default_tool set) may close by finally emitting its create
    # (possibly among other actions in the same reply).
    if default_tool is not None and closing_final is None:
        target, args = _choose_action(actions, handlers, default_tool)
        if target is not None:
            out = await _dispatch(handlers, target, args)
            if isinstance(out, Done):
                return {"done": out.value, "text": "", "used": "json",
                        "steps": max_steps + 1}
    # Otherwise the closing reply is the best-effort final answer.
    final = ""
    if closing_final is not None:
        final = (closing_final.get("text") or closing_final.get("answer") or "").strip()
    return {"done": None,
            "text": (final or raw.strip() or (prior_prose or "")).strip(),
            "used": "json", "steps": max_steps + 1, "exhausted": True,
            "last_raw": last_raw[:300]}


async def run_tool_loop(hass, client, *, system=None, user=None, messages=None,
                        tools, handlers, json_system=None, json_hint="",
                        default_tool=None, closing_prompt=None,
                        max_steps=MAX_DRIVER_STEPS,
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
            default_tool=default_tool, json_hint=json_hint,
            closing_prompt=closing_prompt)

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
            closing_prompt=closing_prompt, prior_prose=res.get("prose"))
    return res
