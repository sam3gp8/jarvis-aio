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

from .tool_driver import Done, run_tool_loop, supports_tools
# Re-exported: the composer's JSON parsing lives in the shared driver now, but
# tests and any caller still reach it here.
from .tool_driver import _extract_json, _extract_json_actions  # noqa: F401

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
    installing it.

    Routes by model capability:
      • a tool-capable model (every cloud provider) runs the agentic loop —
        search → compose → validate, retrying a bad name;
      • a local model that can't drive that loop (Gemma via Ollama — it loops on
        search and never commits) runs a ONE-SHOT compose instead: JARVIS
        pre-searches the request and ranks the real devices, hands the model that
        focused list, and asks for the automation in a single reply (no search
        tool to get stuck on), with deterministic bad-name correction.

    Returns the draft + its YAML for review (install is a separate step), or the
    last validation failure with the closest real names. Never raises."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "Describe the automation you'd like."}
    if client is None:
        return {"ok": False, "error": ("No reasoning provider is configured, so I "
                "can't draft automations from the panel yet.")}
    try:
        cap = await supports_tools(hass, client)
    except Exception:
        cap = None
    if cap is True:
        return await _draft_agentic(hass, text, client)
    return await _compose_oneshot(hass, text, client)


async def _draft_agentic(hass: HomeAssistant, text: str, client) -> dict[str, Any]:
    """Agentic search → compose → validate loop for a tool-capable model."""
    state: dict[str, Any] = {"last_error": None, "last_args": None}

    def _do_search(args: dict):
        hits = _search_entities_for_draft(hass, args.get("query", ""), args.get("domain"))
        return "search results: " + json.dumps(hits)

    async def _do_create(args: dict):
        auto = _coerce_automation(args) or (args if isinstance(args, dict) else {})
        last_args = {
            "alias": auto.get("alias") or "Automation",
            "description": auto.get("description", ""),
            "trigger": auto.get("trigger"), "condition": auto.get("condition"),
            "action": auto.get("action"), "mode": auto.get("mode", "single"),
        }
        state["last_args"] = last_args
        preview = await create_automation(hass, validate=True, install=False, **last_args)
        if preview.get("success"):
            return Done(_draft_ok(preview, last_args))
        state["last_error"] = preview
        return (f"Those names are not in the home: {_bad_names(preview) or preview.get('error')}. "
                "Use search_entities to find the correct ids, then try again.")

    handlers = {
        "search": _do_search, "search_entities": _do_search,
        "create": _do_create, "create_automation": _do_create,
    }
    system_native = (
        "You compose ONE Home Assistant automation from the user's request. "
        "FIRST call search_entities to resolve the real entity_ids you need — "
        "never invent names — THEN call create_automation with those ids and the "
        "modern keys ('trigger', 'action'). If create_automation reports an unknown "
        "name, search for the correct one and try again. Some of the home's "
        "entities:\n" + _entity_hint(hass))
    json_hint = (
        "Each trigger/action item uses Home Assistant's modern keys "
        "'trigger'/'action', e.g. "
        '{"trigger":"state","entity_id":"binary_sensor.x","to":"on"} and '
        '{"action":"light.turn_on","target":{"entity_id":"light.y"}}. '
        'Build it with {"tool":"create_automation","args":{"alias":"...",'
        '"trigger":[...],"condition":[...],"action":[...]}}.')

    res = await run_tool_loop(
        hass, client, system=system_native, user=text,
        tools=[_SEARCH_TOOL, _COMPOSER_TOOL], handlers=handlers,
        json_hint=json_hint, default_tool="create",
        max_steps=_DRAFT_MAX_STEPS, temperature=0.2, native=True)

    if isinstance(res.get("done"), dict):
        return res["done"]
    _log_compose(hass, "agentic draft produced nothing — model last said: %s"
                 % ((res.get("last_raw") or res.get("text") or "")[:200]), [],
                 bad=_bad_names(state["last_error"]) if state.get("last_error") else None)
    return _draft_fail(state["last_error"], state["last_args"], prose=res.get("text"))


# How many one-shot attempts a local model gets (first try + bad-name corrections).
_ONESHOT_ATTEMPTS = 3


def _log_compose(hass, msg: str, cands: list, *, bad: str | None = None) -> None:
    """Record a composer step in the JARVIS log so a failure is diagnosable — the
    candidates we offered (did we even find the right devices?) and what the model
    did with them. Never raises."""
    try:
        from .websocket import jarvis_log
        cand_s = (", ".join(c["entity_id"] for c in cands[:14]) or "none") if cands else "—"
        jarvis_log("AGENT", "composer: %s | candidates[%d]: %s%s"
                   % (msg, len(cands or []), cand_s, (f" | bad: {bad}" if bad else "")))
    except Exception:
        pass


def _first_automation(raw: str) -> dict | None:
    """Pull the first object that looks like an automation (has trigger+action)
    out of a reply, skipping any stray search/other JSON the model emitted."""
    for obj in _extract_json_actions(raw):
        auto = _coerce_automation(obj)
        if auto:
            return auto
    return _coerce_automation(_extract_json(raw))


def _corrections_for(hass: HomeAssistant, preview: dict) -> str:
    """For each bad entity name, offer the closest real ids from the home, so the
    retry can fix it deterministically rather than hoping the model guesses."""
    lines: list[str] = []
    for bad in list((preview.get("invalid_entities") or {}).keys()):
        key = bad.split(".")[-1].replace("_", " ")
        hits = _search_entities_for_draft(hass, key, limit=5)
        opts = [h["entity_id"] for h in hits]
        near = (preview.get("invalid_entities") or {}).get(bad)
        if not opts and near:
            opts = [near]
        if opts:
            lines.append(f"  instead of '{bad}': " + ", ".join(opts))
    return "\n".join(lines)


async def _compose_oneshot(hass: HomeAssistant, text: str, client) -> dict[str, Any]:
    """One-shot compose for a model that can't drive the agentic loop. JARVIS
    pre-searches + ranks the real devices the request names, hands the model that
    focused list, and asks for the whole automation in a SINGLE JSON object — no
    search tool to loop on. Validates, and on a bad name retries with the closest
    real ids. Logs the candidates + outcome so a failure is diagnosable."""
    cands = _relevant_entities(hass, text, limit=40)
    cand_lines = ("\n".join(f"- {c['entity_id']} ({c['friendly']})" for c in cands)
                  or "(no obvious device matches were found for this request)")
    system = (
        "You write ONE Home Assistant automation for the user's request, as a "
        "SINGLE JSON object and NOTHING else — no prose, no search, no extra "
        "objects.\n\n"
        "Use ONLY these real devices (copy the entity_id EXACTLY; never invent "
        "one):\n" + cand_lines + "\n\n"
        "Reply with exactly this shape (Home Assistant modern keys):\n"
        '{"alias":"<short name>","trigger":[ ... ],"condition":[ ... ],"action":[ ... ]}\n'
        "Item examples:\n"
        '  trigger:   {"trigger":"state","entity_id":"binary_sensor.x","to":"on"}\n'
        '             {"trigger":"sun","event":"sunset"}\n'
        '  condition: {"condition":"sun","after":"sunset","before":"sunrise"}  '
        "(use this for a dusk-to-dawn / night-only window — no entity needed)\n"
        '  action:    {"action":"light.turn_on","target":{"entity_id":"light.y"}}\n'
        "Cover both directions if the request implies on AND off (e.g. a presence "
        "sensor turning to 'on' and to 'off'). Output the JSON object now.")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]
    last_error: dict | None = None
    last_args: dict | None = None
    last_raw = ""

    for attempt in range(_ONESHOT_ATTEMPTS):
        def _call():
            return client.chat(messages=messages, max_tokens=1024, temperature=0.1)
        try:
            res = await hass.async_add_executor_job(_call)
        except Exception as exc:
            _log_compose(hass, f"one-shot call failed: {exc}", cands)
            return _draft_fail({"error": f"Drafting failed: {exc}"}, last_args)
        raw = (res or {}).get("text") or ""
        last_raw = raw or last_raw
        auto = _first_automation(raw)
        if not auto:
            messages.append({"role": "assistant", "content": raw[:1000]})
            messages.append({"role": "user", "content":
                "Output ONLY the automation as one JSON object: "
                '{"alias":"...","trigger":[...],"condition":[...],"action":[...]}. '
                "No other text, no search."})
            continue
        last_args = {
            "alias": auto.get("alias") or "Automation",
            "description": auto.get("description", ""),
            "trigger": auto.get("trigger"), "condition": auto.get("condition"),
            "action": auto.get("action"), "mode": auto.get("mode", "single"),
        }
        preview = await create_automation(hass, validate=True, install=False, **last_args)
        if preview.get("success"):
            _log_compose(hass, "one-shot OK (attempt %d): %s"
                         % (attempt + 1, last_args.get("alias")), cands)
            return _draft_ok(preview, last_args)
        last_error = preview
        corrections = _corrections_for(hass, preview)
        messages.append({"role": "assistant", "content": raw[:1000]})
        messages.append({"role": "user", "content":
            f"These are NOT real devices and must be replaced: "
            f"{_bad_names(preview) or preview.get('error')}.\n"
            + (("Use one of these real ids instead:\n" + corrections + "\n")
               if corrections else "")
            + "Re-output the FULL automation as one JSON object using only real "
              "entity_ids from the list."})

    _log_compose(hass, "one-shot gave up after %d attempts — model last said: %s"
                 % (_ONESHOT_ATTEMPTS, last_raw[:200]), cands,
                 bad=(_bad_names(last_error) if last_error else "no valid JSON object"))
    # No valid automation ever parsed → surface the model's own words (e.g. a
    # clarifying question); a validation failure → surface the specific bad names.
    prose = last_raw.strip() if last_error is None else None
    return _draft_fail(last_error, last_args, prose=prose)


_DRAFT_STOP = {
    "the", "a", "an", "and", "or", "to", "from", "on", "off", "when", "once",
    "that", "this", "is", "are", "be", "will", "with", "in", "at", "by", "of",
    "it", "they", "them", "up", "down", "turn", "set", "for", "if", "then", "my",
    "you", "your", "space", "please", "detected", "leave", "person", "people",
}


def _relevant_entities(hass: HomeAssistant, text: str, limit: int = 40) -> list[dict]:
    """Rank the home's devices by how well they match the request, so a one-shot
    composer hands the model a SMALL, FOCUSED, correctly-ordered candidate list
    (the living-room occupancy sensor that matches 'living'+'room'+'occupancy'
    outranks a bedroom sensor that matches only 'room'). Essential in a big home
    where a flat dump of hundreds of entities drowns the few that matter.

    Scored by the number of distinct request keywords found in the entity's
    friendly name + id; actuatable domains (for actions) are scanned before
    triggerable ones (for triggers). Best-effort; never raises."""
    words = {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
             if len(w) >= 3 and w not in _DRAFT_STOP}
    if not words:
        return []
    scored: list[dict] = []
    seen: set = set()
    for pri, dom in enumerate(_ACTUATABLE + _TRIGGERABLE):
        try:
            for st in hass.states.async_all(dom):
                if st.entity_id in seen:
                    continue
                fn = str(st.attributes.get("friendly_name") or "")
                hay = (fn + " " + st.entity_id.replace(".", " ").replace("_", " ")).lower()
                score = sum(1 for w in words if w in hay)
                if score > 0:
                    seen.add(st.entity_id)
                    scored.append({"entity_id": st.entity_id,
                                   "friendly": fn or st.entity_id,
                                   "domain": dom, "score": score, "pri": pri})
        except Exception:
            continue
    # highest keyword overlap first; ties broken by domain priority (actuatable
    # before triggerable), then entity_id for stability.
    scored.sort(key=lambda c: (-c["score"], c["pri"], c["entity_id"]))
    return scored[:limit]


def _bad_names(preview: dict) -> str:
    return ", ".join(list((preview.get("invalid_entities") or {}).keys())
                     + list((preview.get("invalid_services") or {}).keys()))


def _coerce_automation(data: dict) -> dict | None:
    """Pull an automation object out of a model's JSON action — either a wrapped
    ``{"automation":{...}}`` or a bare ``{alias,trigger,action}``. Returns None
    when it carries no usable trigger+action."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("automation"), dict):
        data = data["automation"]
    if data.get("trigger") and data.get("action"):
        return data
    return None
