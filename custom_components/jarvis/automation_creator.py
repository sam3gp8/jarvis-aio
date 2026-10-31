"""
JARVIS Automation Creator (v5.6.0).

Registers a custom HA service + tool that allows JARVIS to create
Home Assistant automations from natural language instructions.

Usage via conversation: "JARVIS, create an automation that turns off
the living room lights at midnight."

The LLM generates the automation YAML, this module validates and
registers it with HA.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import yaml
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# A HA entity_id / dotted service reference: domain.object_id, all lowercase.
_DOTTED_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")


def _collect_refs(obj: Any, entities: set, services: set) -> None:
    """Walk a trigger/condition/action config and collect the entity_ids and
    service names it references. Skips templates ({{ … }}) and the sentinel
    values ("all"/"none"); only dotted, HA-shaped names are collected so free
    text can't masquerade as a reference. Pure; never raises."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "entity_id":
                for e in (val if isinstance(val, list) else [val]):
                    if isinstance(e, str) and _DOTTED_RE.match(e):
                        entities.add(e)
            elif key in ("service", "action") and isinstance(val, str) \
                    and "{{" not in val and _DOTTED_RE.match(val):
                # action step service: old "service: light.turn_on" or new
                # HA "action: light.turn_on". Only a dotted value is a service.
                services.add(val)
            else:
                _collect_refs(val, entities, services)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, entities, services)


def _known_services(hass: HomeAssistant) -> set:
    """The set of "domain.service" strings HA currently has registered."""
    out: set = set()
    try:
        for domain, svcs in (hass.services.async_services() or {}).items():
            for svc in svcs:
                out.add(f"{domain}.{svc}")
    except Exception:  # pragma: no cover - defensive
        pass
    return out


def validate_automation_config(
    hass: HomeAssistant, *, trigger, condition, action
) -> dict[str, Any]:
    """Check every entity_id and service a would-be automation references against
    what Home Assistant actually has, so JARVIS never installs an automation that
    silently refers to a guessed/misspelled name. Returns
    ``{"ok": bool, "invalid_entities": {bad: closest|None},
       "invalid_services": {bad: closest|None}}``. Best-effort and never raises;
    on any internal error it reports ``ok=True`` (fail-open — validation must
    never be what blocks a legitimate automation)."""
    try:
        entities: set = set()
        services: set = set()
        for section in (trigger, condition, action):
            _collect_refs(section, entities, services)

        known_entities = {s.entity_id for s in hass.states.async_all()}
        known_services = _known_services(hass)

        invalid_entities: dict = {}
        for e in sorted(entities):
            if e not in known_entities:
                match = difflib.get_close_matches(e, known_entities, n=1, cutoff=0.6)
                invalid_entities[e] = match[0] if match else None

        invalid_services: dict = {}
        for s in sorted(services):
            if s not in known_services:
                match = difflib.get_close_matches(s, known_services, n=1, cutoff=0.6)
                invalid_services[s] = match[0] if match else None

        return {
            "ok": not invalid_entities and not invalid_services,
            "invalid_entities": invalid_entities,
            "invalid_services": invalid_services,
        }
    except Exception as exc:  # pragma: no cover - defensive, fail-open
        _LOGGER.debug("automation validation error (fail-open): %s", exc)
        return {"ok": True, "invalid_entities": {}, "invalid_services": {}}


async def create_automation(
    hass: HomeAssistant,
    *,
    alias: str,
    description: str = "",
    trigger: list[dict] | dict = None,
    condition: list[dict] | dict | None = None,
    action: list[dict] | dict = None,
    mode: str = "single",
    validate: bool = False,
    install: bool = True,
) -> dict[str, Any]:
    """
    Create and register a new HA automation programmatically.

    Args:
        alias: Human-readable name for the automation
        description: Optional description
        trigger: Trigger configuration (HA automation trigger format)
        condition: Optional condition(s)
        action: Action(s) to perform
        mode: Execution mode (single, restart, queued, parallel)

    Returns:
        {"success": True, "automation_id": "...", "alias": "..."}
        or {"success": False, "error": "..."}
    """
    if not trigger or not action:
        return {"success": False, "error": "Both trigger and action are required"}

    # Normalize to lists
    if isinstance(trigger, dict):
        trigger = [trigger]
    if isinstance(action, dict):
        action = [action]
    if condition and isinstance(condition, dict):
        condition = [condition]

    # Validate every referenced entity_id and service against the live HA
    # instance before writing anything (opt-in). An LLM can confidently emit a
    # plausible-but-wrong name; installing that produces an automation that
    # silently never fires. Refuse up front and hand back the closest real name
    # so the caller can correct and retry. Off by default, so existing callers
    # (the HA service + the learned-suggestion installer) are unchanged.
    if validate:
        report = validate_automation_config(
            hass, trigger=trigger, condition=condition, action=action)
        if not report["ok"]:
            parts = []
            for bad, near in report["invalid_entities"].items():
                parts.append(f"entity '{bad}'" + (f" (did you mean '{near}'?)" if near else " (no close match)"))
            for bad, near in report["invalid_services"].items():
                parts.append(f"service '{bad}'" + (f" (did you mean '{near}'?)" if near else " (no close match)"))
            return {
                "success": False,
                "error": "Validation failed — these names don't exist in Home "
                         "Assistant: " + "; ".join(parts) + ". Use search_entities "
                         "to find the real entity_id, then retry.",
                "invalid_entities": report["invalid_entities"],
                "invalid_services": report["invalid_services"],
            }

    # Build the automation config
    automation_id = f"jarvis_auto_{alias.lower().replace(' ', '_')[:40]}"
    auto_config = {
        "id": automation_id,
        "alias": f"JARVIS · {alias}",
        "description": description or f"Created by JARVIS: {alias}",
        "mode": mode,
        "triggers": trigger,
        "actions": action,
    }
    if condition:
        auto_config["conditions"] = condition

    # Validate the YAML is well-formed
    try:
        yaml_str = yaml.dump([auto_config], default_flow_style=False)
        _LOGGER.debug("JARVIS automation YAML:\n%s", yaml_str)
    except Exception as exc:
        return {"success": False, "error": f"YAML generation failed: {exc}"}

    # Dry-run (review before install): validation has already run above, so hand
    # back the built config + its YAML for the caller to show the user, WITHOUT
    # writing anything. Used by the Suggestions-tab composer so a drafted
    # automation is reviewed and only installed on an explicit second step.
    if not install:
        return {
            "success": True,
            "preview": True,
            "automation_id": automation_id,
            "alias": f"JARVIS · {alias}",
            "config": auto_config,
            "yaml": yaml_str,
        }

    # Write to automations.yaml
    try:
        automations_path = hass.config.path("automations.yaml")

        def _write_automation():
            # Read existing
            try:
                with open(automations_path) as f:
                    existing = yaml.safe_load(f) or []
            except FileNotFoundError:
                existing = []
            except Exception:
                existing = []

            if not isinstance(existing, list):
                existing = []

            # Check for duplicate ID
            existing = [a for a in existing if a.get("id") != automation_id]

            # Append new
            existing.append(auto_config)

            # Write back
            with open(automations_path, "w") as f:
                yaml.dump(existing, f, default_flow_style=False, sort_keys=False)

        await hass.async_add_executor_job(_write_automation)

        # Reload automations
        await hass.services.async_call("automation", "reload", blocking=True)

        _LOGGER.info("JARVIS created automation: %s (id=%s)", alias, automation_id)
        return {
            "success": True,
            "automation_id": automation_id,
            "alias": f"JARVIS · {alias}",
        }

    except Exception as exc:
        _LOGGER.error("JARVIS automation creation failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ── Natural-language composer (Suggestions tab) ──────────────────────────────
# The chat agent already authors automations from plain language via its
# create_automation tool. The Suggestions tab had no equivalent — only a review
# list of machine-learned patterns. draft_automation gives the panel a one-shot
# "describe it → review it → install it" path that reuses the SAME validation,
# so a drafted automation is checked against the real home before it is written,
# and only installed on an explicit second step (review before install).

_COMPOSER_TOOL = {
    "type": "function",
    "function": {
        "name": "create_automation",
        "description": ("Define the single automation the user described, using "
                        "ONLY real entity_ids and services from the home."),
        "parameters": {
            "type": "object",
            "properties": {
                "alias": {"type": "string", "description": "Short human name."},
                "description": {"type": "string"},
                "trigger": {"type": "array", "items": {"type": "object"},
                            "description": ("HA trigger(s), e.g. "
                                            "[{\"trigger\":\"sun\",\"event\":\"sunset\"}] "
                                            "or [{\"trigger\":\"state\",\"entity_id\":"
                                            "\"binary_sensor.front_door\",\"to\":\"on\"}].")},
                "condition": {"type": "array", "items": {"type": "object"}},
                "action": {"type": "array", "items": {"type": "object"},
                           "description": ("HA action(s), e.g. "
                                           "[{\"action\":\"light.turn_on\",\"target\":"
                                           "{\"entity_id\":\"light.porch\"}}].")},
                "mode": {"type": "string"},
            },
            "required": ["alias", "trigger", "action"],
        },
    },
}

# Domains worth offering the drafting model as references.
_ACTUATABLE = ("light", "switch", "fan", "lock", "cover", "climate", "media_player",
               "input_boolean", "scene", "script", "humidifier", "vacuum", "siren")
_TRIGGERABLE = ("binary_sensor", "device_tracker", "person", "sensor", "input_boolean")


def _entity_hint(hass: HomeAssistant, limit: int = 160) -> str:
    """A compact 'entity_id — friendly name' list so the drafting model references
    real ids instead of inventing them. Capped; best-effort; never raises."""
    lines: list[str] = []
    seen: set = set()

    def _add(domains, cap):
        for dom in domains:
            try:
                for st in hass.states.async_all(dom):
                    if st.entity_id in seen:
                        continue
                    seen.add(st.entity_id)
                    fn = st.attributes.get("friendly_name") or st.entity_id
                    lines.append(f"{st.entity_id} — {fn}")
                    if len(lines) >= cap:
                        return
            except Exception:
                return

    _add(_ACTUATABLE, limit)
    _add(_TRIGGERABLE, limit * 2)
    return "\n".join(lines[:limit * 2])


_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_entities",
        "description": ("Find the real entity_ids this automation needs, by name "
                        "and (optionally) domain. Call this FIRST to resolve devices "
                        "before composing, and again to correct an unknown name."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A name or keyword, e.g. 'porch'."},
                "domain": {"type": "string", "description": "Optional HA domain, e.g. 'light'."},
            },
            "required": ["query"],
        },
    },
}

# How many compose/search rounds the drafter gets before giving up.
_DRAFT_MAX_STEPS = 5


def _search_entities_for_draft(hass: HomeAssistant, query: str,
                               domain: str | None = None, limit: int = 12) -> list[dict]:
    """Lightweight name search over the live home for the composer's agentic loop
    — enough to resolve real entity_ids without importing the agent. Best-effort."""
    q = (query or "").lower().strip()
    qwords = [w for w in q.split() if w]
    domains = [domain] if domain else list(_ACTUATABLE + _TRIGGERABLE)
    out: list[dict] = []
    seen: set = set()
    for dom in domains:
        try:
            for st in hass.states.async_all(dom):
                if st.entity_id in seen:
                    continue
                fn = str(st.attributes.get("friendly_name") or "").lower()
                hay = fn + " " + st.entity_id.lower().replace(".", " ").replace("_", " ")
                if q and (q in fn or q in st.entity_id.lower()
                          or (qwords and any(w in hay for w in qwords))):
                    seen.add(st.entity_id)
                    out.append({"entity_id": st.entity_id,
                                "friendly_name": st.attributes.get("friendly_name", "")})
                    if len(out) >= limit:
                        return out
        except Exception:
            continue
    return out


def _draft_ok(preview: dict, args: dict) -> dict:
    return {"ok": True, "draft": {"alias": preview.get("alias"),
                                  "yaml": preview.get("yaml"), "args": args}}


def _draft_fail(last_error: dict | None, last_args: dict | None,
                prose: str | None = None) -> dict:
    err = (last_error or {}).get("error")
    # A capable model's clarifying question ("which light?") is more useful than
    # the generic fallback — surface it when we have nothing better.
    if prose and (not err or "name the trigger" in err):
        err = prose
    return {"ok": False,
            "error": err or "I couldn't compose that automation — try describing "
                            "it more specifically.",
            "invalid_entities": (last_error or {}).get("invalid_entities"),
            "invalid_services": (last_error or {}).get("invalid_services"),
            "draft": ({"args": last_args} if last_args else None)}


async def draft_automation(hass: HomeAssistant, text: str, *, client) -> dict[str, Any]:
    """Turn a plain-language request into a reviewed automation draft WITHOUT
    installing it. Tries a bounded agentic tool-calling loop first (search → compose
    → validate, retrying bad names); if the provider never emits a tool call — e.g.
    a local model without function-calling, like Gemma via Ollama — it falls back to
    a tools-free JSON-output draft that any instruction-following model can satisfy.
    Returns the draft + its YAML for review (install is a separate step), or the
    last validation failure with the closest real names. Never raises."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "Describe the automation you'd like."}
    if client is None:
        return {"ok": False, "error": ("No reasoning provider is configured, so I "
                "can't draft automations from the panel yet.")}
    res = await _draft_with_tools(hass, text, client)
    if res.get("ok") or res.get("saw_tool_call"):
        res.pop("saw_tool_call", None)
        res.pop("prose", None)
        return res
    # The provider produced no tool call (no function-calling support). Fall back
    # to a JSON-output draft, preserving any prose the model did return.
    return await _draft_with_json(hass, text, client, prior_prose=res.get("prose"))


async def _draft_with_tools(hass: HomeAssistant, text: str, client) -> dict:
    """Agentic tool-calling draft: search → compose → validate, retrying on a bad
    name. Sets ``saw_tool_call=False`` when the provider never emitted a tool call,
    so the caller can fall back to the tools-free path."""
    system = ("You compose ONE Home Assistant automation from the user's request. "
              "FIRST call search_entities to resolve the real entity_ids you need — "
              "never invent names — THEN call create_automation with those ids and "
              "the modern keys ('trigger', 'action'). If create_automation reports an "
              "unknown name, search for the correct one and try again. Some of the "
              "home's entities:\n" + _entity_hint(hass))
    working: list[dict] = [{"role": "system", "content": system},
                           {"role": "user", "content": text}]
    tools = [_SEARCH_TOOL, _COMPOSER_TOOL]
    last_error: dict | None = None
    last_args: dict | None = None
    saw_tool_call = False

    for _ in range(_DRAFT_MAX_STEPS):
        def _call():
            return client.chat(messages=working, tools=tools,
                               max_tokens=1024, temperature=0.2)
        try:
            result = await hass.async_add_executor_job(_call)
        except Exception as exc:
            return {"ok": False, "error": f"Drafting failed: {exc}",
                    "saw_tool_call": saw_tool_call}

        calls = (result or {}).get("tool_calls") or []
        if not calls:
            if saw_tool_call:
                return {**_draft_fail(last_error, last_args), "saw_tool_call": True}
            return {"ok": False, "saw_tool_call": False,
                    "prose": ((result or {}).get("text") or "").strip()}

        saw_tool_call = True
        working.append({
            "role": "assistant",
            "content": result.get("text") or "",
            "tool_calls": [{"id": c.get("id") or f"call_{i}", "type": "function",
                            "function": {"name": c.get("name"),
                                         "arguments": json.dumps(c.get("args") or {})}}
                           for i, c in enumerate(calls)],
        })
        for i, call in enumerate(calls):
            cid = call.get("id") or f"call_{i}"
            name = call.get("name")
            args = call.get("args") or {}
            if name == "search_entities":
                hits = _search_entities_for_draft(hass, args.get("query", ""),
                                                  args.get("domain"))
                working.append({"role": "tool", "tool_call_id": cid,
                                "content": json.dumps(hits)})
            elif name == "create_automation":
                last_args = {
                    "alias": args.get("alias") or "Automation",
                    "description": args.get("description", ""),
                    "trigger": args.get("trigger"), "condition": args.get("condition"),
                    "action": args.get("action"), "mode": args.get("mode", "single"),
                }
                preview = await create_automation(
                    hass, validate=True, install=False, **last_args)
                if preview.get("success"):
                    return {**_draft_ok(preview, last_args), "saw_tool_call": True}
                last_error = preview
                working.append({"role": "tool", "tool_call_id": cid, "content": json.dumps({
                    "error": preview.get("error"),
                    "invalid_entities": preview.get("invalid_entities"),
                    "invalid_services": preview.get("invalid_services")})})
            else:
                working.append({"role": "tool", "tool_call_id": cid,
                                "content": json.dumps({"error": f"unknown tool {name}"})})

    return {**_draft_fail(last_error, last_args), "saw_tool_call": True}


_DRAFT_STOP = {
    "the", "a", "an", "and", "or", "to", "from", "on", "off", "when", "once",
    "that", "this", "is", "are", "be", "will", "with", "in", "at", "by", "of",
    "it", "they", "them", "up", "down", "turn", "set", "for", "if", "then", "my",
    "you", "your", "space", "please", "detected", "leave", "person", "people",
}


def _extract_json(text: str):
    """Best-effort: pull the first JSON object out of a model reply — handles
    ```json fences and surrounding prose. Returns a dict/list, or None."""
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


def _relevant_entity_hint(hass: HomeAssistant, text: str, limit: int = 160) -> str:
    """Entity hint biased toward the request: entities whose name matches a word in
    the text lead, then the general list — so a tools-free model still picks the
    real ids for the devices the request actually names."""
    words = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
             if len(w) >= 3 and w not in _DRAFT_STOP]
    relevant: list[str] = []
    seen: set = set()
    for w in words:
        for hit in _search_entities_for_draft(hass, w, limit=8):
            if hit["entity_id"] not in seen:
                seen.add(hit["entity_id"])
                relevant.append(f"{hit['entity_id']} — {hit.get('friendly_name') or ''}")
    general = _entity_hint(hass, limit=limit)
    if relevant:
        return "Likely relevant:\n" + "\n".join(relevant[:40]) + "\n\nAll entities:\n" + general
    return general


def _bad_names(preview: dict) -> str:
    return ", ".join(list((preview.get("invalid_entities") or {}).keys())
                     + list((preview.get("invalid_services") or {}).keys()))


def _coerce_automation(data: dict) -> dict | None:
    """Pull an automation object out of a model's JSON action — either the wrapped
    ``{"tool":"create","automation":{...}}`` or a bare ``{alias,trigger,action}``."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("automation"), dict):
        data = data["automation"]
    if data.get("trigger") and data.get("action"):
        return data
    return None


async def _draft_with_json(hass: HomeAssistant, text: str, client,
                           prior_prose: str | None = None) -> dict:
    """Tools-free AGENTIC draft for models without function-calling (e.g. local
    Gemma via Ollama). The model drives a search → compose → validate loop by
    emitting ONE JSON *action* per turn — a ``search`` to resolve device ids, or a
    ``create`` with the automation — which JARVIS executes and feeds back. This
    gives a non-tool model the SAME capability as the native tool loop, over a
    plain-JSON protocol it can follow. Still never installs: the result is a
    validated draft for review."""
    system = (
        "You create ONE Home Assistant automation, step by step. Reply with EXACTLY "
        "ONE JSON object per message and NOTHING else. Two actions:\n"
        '  • find device ids:  {"tool": "search", "query": "chaise lamp"}\n'
        '  • build it:          {"tool": "create", "automation": {"alias": "...", '
        '"trigger": [ ... ], "condition": [ ... ], "action": [ ... ]}}\n'
        "Rules: use ONLY entity_ids that search returned — never invent them. Each "
        "trigger/action item uses Home Assistant's modern keys 'trigger'/'action', "
        'e.g. {"trigger":"state","entity_id":"binary_sensor.x","to":"on"} and '
        '{"action":"light.turn_on","target":{"entity_id":"light.y"}}. '
        "Search for each device the request names before building. Entities to start from:\n"
        + _relevant_entity_hint(hass, text))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]
    last_error: dict | None = None
    last_args: dict | None = None

    for _ in range(_DRAFT_MAX_STEPS):
        def _call():
            return client.chat(messages=messages, max_tokens=1024, temperature=0.1)
        try:
            result = await hass.async_add_executor_job(_call)
        except Exception as exc:
            return {"ok": False, "error": f"Drafting failed: {exc}"}
        raw = (result or {}).get("text") or ""
        messages.append({"role": "assistant", "content": raw[:1200]})
        data = _extract_json(raw)
        tool = data.get("tool") if isinstance(data, dict) else None

        if tool == "search":
            hits = _search_entities_for_draft(hass, (data.get("query") or ""))
            messages.append({"role": "user", "content":
                             "search results: " + json.dumps(hits)
                             + "\nReply with your next JSON action."})
            continue

        auto = _coerce_automation(data) if (tool in (None, "create")) else None
        if auto:
            last_args = {
                "alias": auto.get("alias") or "Automation",
                "description": auto.get("description", ""),
                "trigger": auto.get("trigger"), "condition": auto.get("condition"),
                "action": auto.get("action"), "mode": auto.get("mode", "single"),
            }
            preview = await create_automation(hass, validate=True, install=False, **last_args)
            if preview.get("success"):
                return _draft_ok(preview, last_args)
            last_error = preview
            messages.append({"role": "user", "content":
                             f"Those names are not in the home: {_bad_names(preview) or preview.get('error')}. "
                             'Use {"tool":"search",...} to find the correct ids, then try again.'})
            continue

        # Unparseable / unknown — nudge back to the protocol (don't clobber a real error).
        if last_error is None:
            last_error = {"error": "I couldn't turn that into an automation — name the "
                          "trigger (what starts it) and the action (what should happen)."}
        messages.append({"role": "user", "content":
                         'Reply with ONE JSON action only: {"tool":"search","query":"..."} '
                         'or {"tool":"create","automation":{...}}.'})

    return _draft_fail(last_error, last_args, prose=prior_prose)
