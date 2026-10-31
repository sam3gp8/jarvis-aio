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


async def draft_automation(hass: HomeAssistant, text: str, *, client) -> dict[str, Any]:
    """Turn a plain-language request into a reviewed automation draft WITHOUT
    installing it. The model emits a structured trigger/action referencing real
    entities; every name is validated against the live home, and the draft + its
    YAML (plus any bad names) are handed back for review. Never raises."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "Describe the automation you'd like."}
    if client is None:
        return {"ok": False, "error": ("No reasoning provider is configured, so I "
                "can't draft automations from the panel yet.")}
    system = ("You convert ONE natural-language request into a single Home "
              "Assistant automation by calling create_automation. Use ONLY real "
              "entity_ids and services from this home — never invent names — and "
              "prefer the modern keys ('trigger', 'action'). Home entities:\n"
              + _entity_hint(hass))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]

    def _call():
        return client.chat(messages=messages, tools=[_COMPOSER_TOOL],
                           max_tokens=1024, temperature=0.2)

    try:
        result = await hass.async_add_executor_job(_call)
    except Exception as exc:
        return {"ok": False, "error": f"Drafting failed: {exc}"}

    calls = (result or {}).get("tool_calls") or []
    if not calls:
        return {"ok": False,
                "error": ((result or {}).get("text") or "").strip()
                or "I couldn't turn that into an automation — name the trigger "
                   "and what should happen."}
    args = calls[0].get("args") or {}
    preview = await create_automation(
        hass, alias=(args.get("alias") or "Automation"),
        description=args.get("description", ""),
        trigger=args.get("trigger"), condition=args.get("condition"),
        action=args.get("action"), mode=args.get("mode", "single"),
        validate=True, install=False)
    norm_args = {
        "alias": args.get("alias") or "Automation",
        "description": args.get("description", ""),
        "trigger": args.get("trigger"), "condition": args.get("condition"),
        "action": args.get("action"), "mode": args.get("mode", "single"),
    }
    if not preview.get("success"):
        # drafted, but references a name the home doesn't have — show why.
        return {"ok": False, "error": preview.get("error"),
                "invalid_entities": preview.get("invalid_entities"),
                "invalid_services": preview.get("invalid_services"),
                "draft": {"args": norm_args}}
    return {"ok": True, "draft": {
        "alias": preview.get("alias"),
        "yaml": preview.get("yaml"),
        "args": norm_args,
    }}
