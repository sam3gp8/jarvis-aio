"""
JARVIS Agentic LLM (v5.7.07).

Full conversational AI agent with tool-calling, provider fallback,
session memory, and persistent learning. Replaces the basic ReAct loop.

Architecture:
  1. System prompt with JARVIS persona + home context injection
  2. Custom HA tool definitions (not generic HA LLM API)
  3. Multi-turn agentic loop: LLM reasons → calls tools → observes → responds
  4. Provider cascade: Groq (fast) → Gemini (fallback) → local error
  5. Session memory: tracks conversation within a session
  6. Persistent learning: remembers entity aliases, user preferences,
     frequently-used commands across sessions

The local engine (local_engine.py) remains as a fast-path interceptor for
dead-simple commands (complexity < 40). Everything else comes here.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional, Sequence

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

_LOGGER = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
MAX_TOOL_RETRIES    = 2
SUMMARIZE_THRESHOLD = 20
SUMMARIZE_KEEP      = 6


# ── Custom HA tool definitions ──────────────────────────────────────────────
# These give the LLM clear, well-documented tools for controlling HA.
# Much better than the generic HA LLM API tools which confuse the model.

JARVIS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "control_device",
            "description": (
                "Control a Home Assistant device. Turn lights/switches/fans "
                "on or off, lock/unlock locks, open/close covers/garage doors, "
                "set brightness, set climate temperature. Use the entity_id "
                "from the home context or from get_entities results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "The HA entity_id (e.g. light.kitchen, lock.front_door)",
                    },
                    "action": {
                        "type": "string",
                        "enum": [
                            "turn_on", "turn_off", "toggle",
                            "lock", "unlock",
                            "open", "close",
                            "set_brightness", "set_temperature",
                            "media_play", "media_pause", "media_next",
                            "volume_up", "volume_down", "volume_set",
                        ],
                        "description": "The action to perform",
                    },
                    "value": {
                        "type": "number",
                        "description": (
                            "Optional numeric value: brightness (0-100), "
                            "temperature (degrees), volume (0-100)"
                        ),
                    },
                },
                "required": ["entity_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_state",
            "description": (
                "Get the current state and attributes of one or more HA entities. "
                "Use this to check if a light is on, what temperature a thermostat "
                "is set to, whether a door is open, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of entity_ids to query",
                    },
                },
                "required": ["entity_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_entities",
            "description": (
                "Search for HA entities by name, area, or domain. Use this when "
                "you don't know the exact entity_id. Returns matching entities "
                "with their current state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search term: entity name, area name, or keyword "
                            "(e.g. 'chase', 'kitchen lights', 'garage door')"
                        ),
                    },
                    "domain": {
                        "type": "string",
                        "description": (
                            "Optional domain filter: light, switch, lock, cover, "
                            "climate, fan, media_player, sensor, binary_sensor, "
                            "scene, script, automation, person"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_area_devices",
            "description": (
                "List all devices and their states in a specific area/room. "
                "Use this to understand what's in a room before controlling devices."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "area_name": {
                        "type": "string",
                        "description": "The area/room name (e.g. 'kitchen', 'master bedroom')",
                    },
                },
                "required": ["area_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_scene_or_script",
            "description": (
                "Activate a scene or run a script/automation. Scenes set multiple "
                "devices to predefined states. Scripts run custom sequences."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "The scene/script entity_id (e.g. scene.movie_time)",
                    },
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_home_summary",
            "description": (
                "Get a summary of the home state: who's home, what lights are on, "
                "locks status, doors/windows open, climate, and any active alerts."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_control",
            "description": (
                "Control multiple devices at once. Turn off all lights in an area, "
                "lock all doors, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": ["light", "switch", "fan", "lock", "cover"],
                        "description": "Device domain to control",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["turn_on", "turn_off", "lock", "unlock", "open", "close"],
                        "description": "Action to perform",
                    },
                    "area_name": {
                        "type": "string",
                        "description": "Optional: limit to specific area (e.g. 'kitchen')",
                    },
                },
                "required": ["domain", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_plan",
            "description": (
                "Execute a multi-step plan to accomplish a complex goal that "
                "requires several coordinated actions in sequence (e.g. 'get the "
                "house ready for guests', 'set up movie night', 'morning routine'). "
                "Provide an ordered list of steps; each step is a device action. "
                "Steps run in order and you get a per-step result. Use this instead "
                "of many separate tool calls when the user expresses a single "
                "high-level goal that decomposes into multiple device actions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "The high-level goal in plain language "
                                       "(used for the spoken summary).",
                    },
                    "steps": {
                        "type": "array",
                        "description": "Ordered list of actions to perform.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {
                                    "type": "string",
                                    "description": "Human summary of this step.",
                                },
                                "domain": {
                                    "type": "string",
                                    "description": "Entity domain, e.g. light, "
                                                   "climate, lock, media_player, cover, switch.",
                                },
                                "service": {
                                    "type": "string",
                                    "description": "Service to call, e.g. turn_on, "
                                                   "turn_off, lock, set_temperature.",
                                },
                                "entity_id": {
                                    "type": "string",
                                    "description": "Target entity_id. Use "
                                                   "search_entities first if unsure.",
                                },
                                "service_data": {
                                    "type": "object",
                                    "description": "Optional extra params "
                                                   "(brightness_pct, temperature, etc.).",
                                },
                            },
                            "required": ["domain", "service", "entity_id"],
                        },
                    },
                },
                "required": ["goal", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Learn and remember a user preference, entity alias, or command "
                "pattern for future use. Use when the user teaches you something "
                "new: device nicknames, routines, preferences."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "Category: 'alias' (device nickname), 'preference' "
                            "(user preference), 'routine' (command pattern)"
                        ),
                        "enum": ["alias", "preference", "routine"],
                    },
                    "name": {
                        "type": "string",
                        "description": "The name/label (e.g. 'chase lamp', 'bedtime')",
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "The mapping value (e.g. entity_id for alias, "
                            "description for preference, action list for routine)"
                        ),
                    },
                },
                "required": ["key", "name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ignore_entity",
            "description": (
                "Tell JARVIS to ignore an entity or area for a specified duration. "
                "Use when the user says things like 'ignore the garage door for "
                "2 hours' or 'stop alerting me about the backyard'. Supports "
                "glob patterns like 'binary_sensor.garage*'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_pattern": {
                        "type": "string",
                        "description": (
                            "Entity ID or glob pattern to ignore "
                            "(e.g. 'binary_sensor.garage_door', 'sensor.backyard*')"
                        ),
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "How long to ignore in minutes. 0 = until manually cleared.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why it's being ignored (e.g. 'maintenance', 'false alarm')",
                    },
                },
                "required": ["entity_pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unignore_entity",
            "description": "Stop ignoring an entity. Removes the ignore rule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_pattern": {
                        "type": "string",
                        "description": "The entity pattern to stop ignoring",
                    },
                },
                "required": ["entity_pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cognitive_status",
            "description": (
                "Get JARVIS cognitive core status: how much data has been learned, "
                "active ignore rules, safety status, uptime, and pattern statistics."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connectivity_status",
            "description": (
                "Check whether JARVIS's cloud reasoning systems (the LLM) are "
                "reachable. Returns online/offline state, recent failure counts, "
                "and cooldown remaining. Use when the user asks if you're online, "
                "connected, or why something failed."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_autonomy",
            "description": (
                "View or revoke JARVIS's autonomous-action grants. These are "
                "proactive actions (like turning on lights in a dark occupied "
                "room) that JARVIS earned the right to perform automatically "
                "after the user accepted them repeatedly. Use 'list' to show "
                "current grants, or 'revoke' with a pattern_key to make JARVIS "
                "ask permission again. Use when the user says 'stop doing X on "
                "your own', 'what do you do automatically', or 'what have you "
                "learned to do'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "revoke"],
                        "description": "list grants or revoke one",
                    },
                    "pattern_key": {
                        "type": "string",
                        "description": "For revoke: the pattern_key to revoke "
                                       "(get it from 'list').",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_suggestions",
            "description": (
                "List pending automation suggestions that JARVIS has learned from "
                "observed behavior patterns. Shows what JARVIS thinks could be automated."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_suggestion",
            "description": "Approve a learned automation suggestion by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestion_id": {"type": "integer", "description": "Suggestion ID"},
                },
                "required": ["suggestion_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dismiss_suggestion",
            "description": "Dismiss a learned automation suggestion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestion_id": {"type": "integer", "description": "Suggestion ID"},
                },
                "required": ["suggestion_id"],
            },
        },
    },
]


# ── Tool execution ──────────────────────────────────────────────────────────

async def _exec_control_device(hass: HomeAssistant, args: dict) -> str:
    """Execute a device control action."""
    entity_id = args.get("entity_id", "")
    action = args.get("action", "")
    value = args.get("value")

    state = hass.states.get(entity_id)
    if not state:
        return json.dumps({"error": f"Entity '{entity_id}' not found"})

    domain = entity_id.split(".")[0]
    svc_data = {"entity_id": entity_id}

    try:
        action_map = {
            "turn_on":  (domain, "turn_on"),
            "turn_off": (domain, "turn_off"),
            "toggle":   (domain, "toggle"),
            "lock":     ("lock", "lock"),
            "unlock":   ("lock", "unlock"),
            "open":     ("cover", "open_cover"),
            "close":    ("cover", "close_cover"),
            "media_play":  ("media_player", "media_play"),
            "media_pause": ("media_player", "media_pause"),
            "media_next":  ("media_player", "media_next_track"),
            "volume_up":   ("media_player", "volume_up"),
            "volume_down": ("media_player", "volume_down"),
        }

        if action == "set_brightness":
            svc_data["brightness_pct"] = int(value or 50)
            await hass.services.async_call("light", "turn_on", svc_data, blocking=True)
        elif action == "set_temperature":
            svc_data["temperature"] = float(value or 72)
            await hass.services.async_call("climate", "set_temperature", svc_data, blocking=True)
        elif action == "volume_set":
            svc_data["volume_level"] = (value or 50) / 100.0
            await hass.services.async_call("media_player", "volume_set", svc_data, blocking=True)
        elif action in action_map:
            svc_domain, svc_name = action_map[action]
            await hass.services.async_call(svc_domain, svc_name, svc_data, blocking=True)
        else:
            return json.dumps({"error": f"Unknown action: {action}"})

        # Get updated state
        new_state = hass.states.get(entity_id)
        return json.dumps({
            "success": True,
            "entity_id": entity_id,
            "previous_state": state.state,
            "new_state": new_state.state if new_state else "unknown",
            "action": action,
        })
    except Exception as exc:
        return json.dumps({"error": f"Failed: {exc}", "entity_id": entity_id})


async def _exec_get_entity_state(hass: HomeAssistant, args: dict) -> str:
    """Get state of one or more entities."""
    entity_ids = args.get("entity_ids", [])
    results = []
    for eid in entity_ids[:20]:  # Cap at 20
        state = hass.states.get(eid)
        if state:
            attrs = dict(state.attributes)
            # Filter to useful attributes
            useful = {}
            for key in ("friendly_name", "brightness", "temperature",
                        "current_temperature", "humidity", "unit_of_measurement",
                        "device_class", "battery_level", "media_title",
                        "volume_level", "source"):
                if key in attrs:
                    useful[key] = attrs[key]
            results.append({
                "entity_id": eid,
                "state": state.state,
                "attributes": useful,
            })
        else:
            results.append({"entity_id": eid, "error": "not found"})
    return json.dumps(results)


async def _exec_search_entities(hass: HomeAssistant, args: dict) -> str:
    """Search for entities by name, area, or domain with fuzzy matching."""
    import re
    query = args.get("query", "").lower().strip()
    domain_filter = args.get("domain")

    # Check learned aliases first
    learned = _load_learned()
    aliases = learned.get("alias", {})
    if query in aliases:
        resolved_id = aliases[query]
        state = hass.states.get(resolved_id)
        if state:
            return json.dumps([{
                "entity_id": resolved_id,
                "friendly_name": state.attributes.get("friendly_name", ""),
                "state": state.state,
                "matched_by": f"learned alias: '{query}'",
            }])

    # Also check partial alias matches
    for alias_name, alias_id in aliases.items():
        if query in alias_name or alias_name in query:
            state = hass.states.get(alias_id)
            if state:
                return json.dumps([{
                    "entity_id": alias_id,
                    "friendly_name": state.attributes.get("friendly_name", ""),
                    "state": state.state,
                    "matched_by": f"partial alias: '{alias_name}'",
                }])

    domains = [domain_filter] if domain_filter else [
        "light", "switch", "lock", "cover", "climate", "fan",
        "media_player", "sensor", "binary_sensor", "scene",
        "script", "automation", "person",
    ]

    # Fuzzy bigram scorer (inline — no external deps)
    def _bigrams(s):
        return set(s[i:i+2] for i in range(len(s)-1)) if len(s) > 1 else {s}

    def _fuzzy(a, b):
        if a == b: return 100.0
        if not a or not b: return 0.0
        bg_a, bg_b = _bigrams(a), _bigrams(b)
        overlap = len(bg_a & bg_b)
        dice = (2.0 * overlap) / (len(bg_a) + len(bg_b)) * 100 if bg_a and bg_b else 0
        contain = len(a) / len(b) * 80 if a in b else (len(b) / len(a) * 80 if b in a else 0)
        return max(dice, contain)

    results = []
    query_words = set(query.split())

    for domain in domains:
        for state in hass.states.async_all(domain):
            fname = (state.attributes.get("friendly_name") or "").lower()
            eid = state.entity_id.lower()
            score = 0

            if query == fname:
                score = 100
            elif query in fname:
                score = 80
            elif query.replace(" ", "_") in eid:
                score = 70
            elif query_words and query_words.issubset(set(fname.split())):
                score = 65
            else:
                # Fuzzy matching
                fuzz = _fuzzy(query, fname)
                if fuzz > 45:
                    score = fuzz * 0.7  # Scale down fuzzy scores

                # Word-level fuzzy — check each query word
                if not score and query_words:
                    fname_words = set(fname.split())
                    word_matches = 0
                    for qw in query_words:
                        for fw in fname_words:
                            if _fuzzy(qw, fw) > 60:
                                word_matches += 1
                                break
                    if word_matches > 0:
                        score = (word_matches / len(query_words)) * 50

            if score > 25:
                results.append({
                    "entity_id": state.entity_id,
                    "friendly_name": state.attributes.get("friendly_name", ""),
                    "state": state.state,
                    "score": round(score, 1),
                })

    results.sort(key=lambda r: r["score"], reverse=True)
    return json.dumps(results[:15])


async def _exec_get_area_devices(hass: HomeAssistant, args: dict) -> str:
    """List all devices in an area."""
    area_name = args.get("area_name", "").lower()
    try:
        from homeassistant.helpers import (
            area_registry as areg, entity_registry as er, device_registry as dr,
        )
        area_reg = areg.async_get(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        target = None
        for area in area_reg.async_list_areas():
            if area_name in area.name.lower():
                target = area
                break
        if not target:
            return json.dumps({"error": f"Area '{area_name}' not found"})

        devices = []
        for entry in ent_reg.entities.values():
            in_area = entry.area_id == target.id
            if not in_area and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                in_area = device and device.area_id == target.id
            if in_area:
                state = hass.states.get(entry.entity_id)
                if state:
                    devices.append({
                        "entity_id": entry.entity_id,
                        "friendly_name": state.attributes.get("friendly_name", ""),
                        "state": state.state,
                        "domain": entry.domain,
                    })

        return json.dumps({
            "area": target.name,
            "device_count": len(devices),
            "devices": devices[:30],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_run_scene_script(hass: HomeAssistant, args: dict) -> str:
    """Activate a scene or script."""
    entity_id = args.get("entity_id", "")
    domain = entity_id.split(".")[0] if "." in entity_id else ""

    if domain not in ("scene", "script", "automation"):
        return json.dumps({"error": f"Not a scene/script/automation: {entity_id}"})

    try:
        svc = "turn_on" if domain in ("scene", "script") else "trigger"
        await hass.services.async_call(domain, svc, {"entity_id": entity_id}, blocking=True)
        return json.dumps({"success": True, "entity_id": entity_id, "action": "activated"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_home_summary(hass: HomeAssistant, args: dict) -> str:
    """Build a comprehensive home summary."""
    summary = {}

    # People
    people = []
    for s in hass.states.async_all("person"):
        people.append({
            "name": s.attributes.get("friendly_name", s.entity_id),
            "state": s.state,
        })
    summary["people"] = people

    # Lights
    on_lights = [
        s.attributes.get("friendly_name", s.entity_id)
        for s in hass.states.async_all("light") if s.state == "on"
    ]
    summary["lights_on"] = on_lights
    summary["lights_on_count"] = len(on_lights)

    # Locks
    unlocked = [
        s.attributes.get("friendly_name", s.entity_id)
        for s in hass.states.async_all("lock") if s.state == "unlocked"
    ]
    summary["locks_unlocked"] = unlocked

    # Doors/Windows
    open_items = []
    for s in hass.states.async_all("binary_sensor"):
        dc = s.attributes.get("device_class", "")
        if dc in ("door", "window", "garage_door") and s.state == "on":
            open_items.append(s.attributes.get("friendly_name", s.entity_id))
    for s in hass.states.async_all("cover"):
        if s.state == "open":
            open_items.append(s.attributes.get("friendly_name", s.entity_id))
    summary["open_doors_windows"] = open_items

    # Climate
    climate = []
    for s in hass.states.async_all("climate"):
        climate.append({
            "name": s.attributes.get("friendly_name", s.entity_id),
            "state": s.state,
            "current_temp": s.attributes.get("current_temperature"),
            "target_temp": s.attributes.get("temperature"),
        })
    summary["climate"] = climate

    # Weather
    for s in hass.states.async_all("weather"):
        summary["weather"] = {
            "condition": s.state,
            "temperature": s.attributes.get("temperature"),
            "humidity": s.attributes.get("humidity"),
        }
        break

    return json.dumps(summary)


async def _exec_bulk_control(hass: HomeAssistant, args: dict) -> str:
    """Control multiple devices in a domain/area."""
    domain = args.get("domain", "")
    action = args.get("action", "")
    area_name = args.get("area_name")

    entities = []
    if area_name:
        # Get area-specific entities
        result = await _exec_get_area_devices(hass, {"area_name": area_name})
        area_data = json.loads(result)
        if "devices" in area_data:
            entities = [
                d["entity_id"] for d in area_data["devices"]
                if d["domain"] == domain
            ]
    else:
        entities = [
            s.entity_id for s in hass.states.async_all(domain)
        ]

    # Filter based on action (don't turn off already-off things)
    if action == "turn_off":
        entities = [e for e in entities if (hass.states.get(e) or type("", (), {"state": ""})()).state == "on"]
    elif action in ("lock",):
        entities = [e for e in entities if (hass.states.get(e) or type("", (), {"state": ""})()).state == "unlocked"]

    success = 0
    for eid in entities:
        try:
            svc_domain = eid.split(".")[0]
            svc_map = {
                "turn_on": (svc_domain, "turn_on"), "turn_off": (svc_domain, "turn_off"),
                "lock": ("lock", "lock"), "unlock": ("lock", "unlock"),
                "open": ("cover", "open_cover"), "close": ("cover", "close_cover"),
            }
            if action in svc_map:
                sd, sn = svc_map[action]
                await hass.services.async_call(sd, sn, {"entity_id": eid}, blocking=False)
                success += 1
        except Exception:
            pass

    return json.dumps({
        "success": True,
        "action": action,
        "domain": domain,
        "area": area_name,
        "count": success,
        "total": len(entities),
    })


# ── Learning memory ─────────────────────────────────────────────────────────

_LEARN_FILE = "/config/.jarvis_learned.json"


def _load_learned() -> dict:
    """Load persistent learned data."""
    try:
        if os.path.exists(_LEARN_FILE):
            with open(_LEARN_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"alias": {}, "preference": {}, "routine": {}}


def _save_learned(data: dict) -> None:
    """Save learned data to disk."""
    try:
        with open(_LEARN_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        _LOGGER.warning("Failed to save learned data: %s", exc)


async def _exec_execute_plan(hass: HomeAssistant, args: dict) -> str:
    """
    Execute a multi-step plan (v5.9.07).

    Runs each step's service call in order, collecting per-step results so the
    agent can report what succeeded and what didn't. This turns a high-level
    goal into one coordinated, inspectable operation rather than many
    independent tool round-trips.
    """
    goal = args.get("goal", "the requested plan")
    steps = args.get("steps", [])
    if not steps:
        return json.dumps({"error": "no steps provided", "goal": goal})

    results = []
    succeeded = 0
    for i, step in enumerate(steps):
        domain = step.get("domain", "")
        service = step.get("service", "")
        entity_id = step.get("entity_id", "")
        extra = step.get("service_data", {}) or {}
        desc = step.get("description", f"{service} {entity_id}")

        if not domain or not service or not entity_id:
            results.append({"step": i + 1, "description": desc,
                            "ok": False, "error": "missing domain/service/entity_id"})
            continue

        # Verify entity exists before acting
        if hass.states.get(entity_id) is None:
            results.append({"step": i + 1, "description": desc,
                            "ok": False, "error": f"entity '{entity_id}' not found"})
            continue

        try:
            await hass.services.async_call(
                domain, service,
                {"entity_id": entity_id, **extra},
                blocking=True,
            )
            succeeded += 1
            results.append({"step": i + 1, "description": desc, "ok": True})
        except Exception as exc:
            results.append({"step": i + 1, "description": desc,
                            "ok": False, "error": str(exc)})

    return json.dumps({
        "goal": goal,
        "total_steps": len(steps),
        "succeeded": succeeded,
        "failed": len(steps) - succeeded,
        "results": results,
    })


async def _exec_remember(hass: HomeAssistant, args: dict) -> str:
    """Learn and persist a user preference or alias."""
    key = args.get("key", "")
    name = args.get("name", "").lower().strip()
    value = args.get("value", "")

    if key not in ("alias", "preference", "routine"):
        return json.dumps({"error": f"Unknown category: {key}"})

    data = await hass.async_add_executor_job(_load_learned)
    if key not in data:
        data[key] = {}
    data[key][name] = value
    await hass.async_add_executor_job(_save_learned, data)

    # v6.25.0: mirror preferences & routines into the curated knowledge store, so
    # spoken "remember that …" shows up in the Memory panel and injects into
    # future prompts. Aliases stay in the learned-entity map only.
    # v6.29.0: attribute preferences to the resolved person (household for routines).
    if key in ("preference", "routine"):
        try:
            from . import knowledge
            if key == "preference":
                from . import identity
                k_subject = identity.resolve_subject(hass)  # this person, or "primary"
                k_kind = "preference"
            else:
                k_subject = knowledge.DEFAULT_SUBJECT
                k_kind = "fact"
            await hass.async_add_executor_job(
                lambda: knowledge.remember(name, value, subject=k_subject,
                                           kind=k_kind, source="stated"))
        except Exception as exc:
            _LOGGER.debug("knowledge mirror failed: %s", exc)

    _LOGGER.info("JARVIS learned: %s['%s'] = '%s'", key, name, value)
    return json.dumps({
        "success": True,
        "learned": f"{key}: '{name}' → '{value}'",
    })


async def _exec_ignore(hass: HomeAssistant, args: dict) -> str:
    """Add an ignore rule via the cognitive core."""
    try:
        from . import cognitive_core
        result = cognitive_core.ignore(
            entity_pattern=args.get("entity_pattern", ""),
            duration_minutes=int(args.get("duration_minutes", 0)),
            reason=args.get("reason", "user request"),
        )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_unignore(hass: HomeAssistant, args: dict) -> str:
    """Remove an ignore rule."""
    try:
        from . import cognitive_core
        result = cognitive_core.unignore(args.get("entity_pattern", ""))
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_cognitive_status(hass: HomeAssistant, args: dict) -> str:
    """Get cognitive core status and learning stats."""
    try:
        from . import cognitive_core
        from .pattern_analyzer import get_analyzer
        status = cognitive_core.status()
        analyzer = get_analyzer()
        status["pattern_analysis"] = await hass.async_add_executor_job(
            analyzer.get_stats)
        return json.dumps(status)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_connectivity_status(hass: HomeAssistant, args: dict) -> str:
    """Get cloud LLM connectivity / circuit-breaker status."""
    try:
        from . import connectivity
        return json.dumps(connectivity.status())
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_manage_autonomy(hass: HomeAssistant, args: dict) -> str:
    """View or revoke graduated-autonomy grants."""
    try:
        from . import cognitive_core
        action = args.get("action", "list")
        if action == "revoke":
            pkey = args.get("pattern_key", "")
            if not pkey:
                return json.dumps({"error": "pattern_key required for revoke"})
            return json.dumps(cognitive_core.revoke_autonomy(pkey))
        # default: list
        status = cognitive_core.status()
        return json.dumps({"grants": status.get("autonomy_grants", [])})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_review_suggestions(hass: HomeAssistant, args: dict) -> str:
    """List pending automation suggestions."""
    try:
        from .pattern_analyzer import get_analyzer
        suggestions = await hass.async_add_executor_job(
            get_analyzer().get_pending_suggestions)
        if not suggestions:
            return json.dumps({"message": "No pending suggestions. I need more data to identify patterns."})
        return json.dumps(suggestions)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_approve_suggestion(hass: HomeAssistant, args: dict) -> str:
    """Approve a suggestion."""
    try:
        from .pattern_analyzer import get_analyzer
        sid = int(args.get("suggestion_id", 0))
        ok = await hass.async_add_executor_job(
            get_analyzer().approve_suggestion, sid)
        return json.dumps({"success": ok, "suggestion_id": sid})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _exec_dismiss_suggestion(hass: HomeAssistant, args: dict) -> str:
    """Dismiss a suggestion."""
    try:
        from .pattern_analyzer import get_analyzer
        sid = int(args.get("suggestion_id", 0))
        ok = await hass.async_add_executor_job(
            get_analyzer().dismiss_suggestion, sid)
        return json.dumps({"success": ok, "suggestion_id": sid})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── Tool dispatcher ─────────────────────────────────────────────────────────

_TOOL_MAP = {
    "control_device":      _exec_control_device,
    "get_entity_state":    _exec_get_entity_state,
    "search_entities":     _exec_search_entities,
    "get_area_devices":    _exec_get_area_devices,
    "run_scene_or_script": _exec_run_scene_script,
    "get_home_summary":    _exec_home_summary,
    "bulk_control":        _exec_bulk_control,
    "execute_plan":        _exec_execute_plan,
    "remember":            _exec_remember,
    "ignore_entity":       _exec_ignore,
    "unignore_entity":     _exec_unignore,
    "cognitive_status":    _exec_cognitive_status,
    "connectivity_status": _exec_connectivity_status,
    "manage_autonomy":     _exec_manage_autonomy,
    "review_suggestions":  _exec_review_suggestions,
    "approve_suggestion":  _exec_approve_suggestion,
    "dismiss_suggestion":  _exec_dismiss_suggestion,
}


async def _execute_tool(
    hass: HomeAssistant,
    tool_name: str,
    tool_args: dict,
    hass_api: Optional[Any] = None,
    user_input: Optional[Any] = None,
) -> str:
    """Execute a tool call — custom tools first, then HA LLM API fallback."""
    # Custom JARVIS tools
    if tool_name in _TOOL_MAP:
        try:
            return await _TOOL_MAP[tool_name](hass, tool_args)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    # Fallback to HA's built-in LLM API tools
    if hass_api:
        from .const import DOMAIN
        for attempt in range(MAX_TOOL_RETRIES + 1):
            try:
                tool_input = llm.ToolInput(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    platform=DOMAIN,
                    context=user_input.context if user_input else None,
                    user_prompt=user_input.text if user_input else "",
                    language=user_input.language if user_input else "en",
                    assistant="conversation",
                    device_id=user_input.device_id if user_input else None,
                )
                result = await hass_api.async_call_tool(tool_input)
                return json.dumps(result) if isinstance(result, dict) else str(result)
            except Exception as exc:
                if attempt >= MAX_TOOL_RETRIES:
                    return json.dumps({"error": f"{tool_name} failed: {exc}"})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ── Home context builder ────────────────────────────────────────────────────

def _build_home_context(hass: HomeAssistant) -> str:
    """
    Build a compact home context string for the system prompt.
    Gives the LLM awareness of what's available to control.
    """
    parts = []

    # Areas
    try:
        from homeassistant.helpers import area_registry as areg
        area_reg = areg.async_get(hass)
        areas = [a.name for a in area_reg.async_list_areas()]
        if areas:
            parts.append(f"Areas: {', '.join(areas)}")
    except Exception:
        pass

    # Key entity counts by domain
    for domain, label in [
        ("light", "Lights"), ("switch", "Switches"), ("lock", "Locks"),
        ("cover", "Covers"), ("climate", "Thermostats"), ("fan", "Fans"),
        ("media_player", "Media players"), ("person", "People"),
        ("scene", "Scenes"), ("script", "Scripts"),
    ]:
        entities = list(hass.states.async_all(domain))
        if entities:
            names = [
                s.attributes.get("friendly_name", s.entity_id)
                for s in entities[:15]
            ]
            suffix = f" (+{len(entities) - 15} more)" if len(entities) > 15 else ""
            parts.append(f"{label} ({len(entities)}): {', '.join(names)}{suffix}")

    # Learned aliases
    learned = _load_learned()
    aliases = learned.get("alias", {})
    if aliases:
        alias_str = "; ".join(f"'{k}' = {v}" for k, v in list(aliases.items())[:20])
        parts.append(f"Learned aliases: {alias_str}")

    preferences = learned.get("preference", {})
    if preferences:
        pref_str = "; ".join(f"{k}: {v}" for k, v in list(preferences.items())[:10])
        parts.append(f"User preferences: {pref_str}")

    return "\n".join(parts)


# ── Context summarization ──────────────────────────────────────────────────

async def _maybe_summarize(
    hass: HomeAssistant, messages: list[dict],
    provider_name: str, api_key: str, model: str, base_url: Optional[str],
) -> list[dict]:
    """Compress old messages when context grows too long."""
    if len(messages) <= SUMMARIZE_THRESHOLD:
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= SUMMARIZE_KEEP:
        return messages

    to_summarize = non_system[:-SUMMARIZE_KEEP]
    to_keep = non_system[-SUMMARIZE_KEEP:]

    parts = []
    for m in to_summarize[-30:]:
        role = m.get("role", "?")
        content = m.get("content", "")
        if content:
            parts.append(f"{role}: {content[:200]}")

    prompt = (
        "Summarize this conversation in 2-3 sentences, preserving key facts:\n\n"
        + "\n".join(parts)
    )

    try:
        from .llm_provider import create_provider
        summarizer = await hass.async_add_executor_job(
            create_provider, provider_name, api_key, model, base_url,
        )
        result = await hass.async_add_executor_job(
            summarizer.chat,
            [{"role": "user", "content": prompt}],
            None, 256, 0.3,
        )
        summary = result.get("text", "")
        if summary:
            return system_msgs + [
                {"role": "system", "content": f"[Previous conversation: {summary}]"}
            ] + to_keep
    except Exception:
        pass
    return messages


# ── Provider cascade ────────────────────────────────────────────────────────

async def _create_provider_with_fallback(
    hass: HomeAssistant,
    provider_name: str, api_key: str, model: str,
    base_url: Optional[str],
    config: Optional[dict] = None,
):
    """Create provider with fallback chain: primary → gemini → error."""
    from .llm_provider import create_provider, create_tier_provider

    try:
        return await hass.async_add_executor_job(
            create_provider, provider_name, api_key, model, base_url,
        )
    except Exception as exc:
        _LOGGER.warning("Primary provider '%s' failed: %s — trying Gemini", provider_name, exc)

    # Fallback to Gemini
    if config:
        try:
            return await hass.async_add_executor_job(
                create_tier_provider, config, "reasoning",
            )
        except Exception as exc2:
            _LOGGER.warning("Gemini fallback also failed: %s", exc2)

    raise RuntimeError(f"No LLM providers available (tried {provider_name} + Gemini)")


# ── Main agent loop ─────────────────────────────────────────────────────────

def _ha_tools_to_openai_format(ha_tools: Sequence) -> list[dict]:
    """Convert HA LLM API tool definitions to OpenAI function-calling format."""
    tools = []
    for t in ha_tools:
        tools.append({
            "type": "function",
            "function": {
                "name":        t.name,
                "description": t.description or "",
                "parameters":  t.parameters or {"type": "object", "properties": {}},
            },
        })
    return tools


def _is_tool_format_error(exc: Exception) -> bool:
    """
    True when the LLM was REACHABLE but emitted a malformed tool call.

    Groq/Llama-3.3-70b stochastically emits `<function=name{json}>` as text
    instead of a structured tool call; Groq rejects it with HTTP 400 and code
    'tool_use_failed' / 'invalid_request_error'. This is a MODEL-OUTPUT problem,
    not a connectivity failure — so it must NOT return the connectivity sentinel
    or trip the circuit breaker (the cloud is fine; the model just fumbled the
    syntax). The correct response is to retry, not to go offline.
    """
    s = str(exc).lower()
    return (
        "tool_use_failed" in s
        or "tool call validation failed" in s
        or "failed to call a function" in s
        or ("400" in s and "invalid_request_error" in s and "function" in s)
    )


def _is_connectivity_error(exc: Exception) -> bool:
    """True when the failure looks like the LLM being genuinely unreachable."""
    s = str(exc).lower()
    return any(k in s for k in (
        "timeout", "timed out", "connection", "connect", "unreachable",
        "name resolution", "dns", "getaddrinfo",
        "500", "502", "503", "504",
        "429", "rate limit", "too many requests",
    ))


async def run_agent(
    hass: HomeAssistant,
    *,
    messages: list[dict],
    persona: str,
    provider_name: str,
    api_key: str,
    model: str,
    base_url: Optional[str] = None,
    hass_api: Optional[Any] = None,
    user_input: Optional[Any] = None,
    temperature: float = 0.7,
    config: Optional[dict] = None,
) -> str:
    """
    Run the JARVIS agentic LLM loop (v5.7.07).

    Multi-turn tool-calling agent with:
      - Custom HA tools + HA LLM API tools
      - Provider fallback (Groq → Gemini)
      - Home context injection
      - Persistent learning
    """
    from .llm_provider import create_provider

    # Build system prompt with home context
    home_context = await hass.async_add_executor_job(
        _build_home_context, hass,
    )
    # Inject cognitive core status
    cog_status = ""
    try:
        from . import cognitive_core
        cstat = cognitive_core.status()
        if cstat.get("running"):
            ignores = cognitive_core.list_ignores()
            cog_status = (
                f"\n\n## Cognitive Core\n"
                f"Running: {cstat['tick_count']} ticks, "
                f"{cstat['actions_taken']} actions taken. "
                f"Learning: {cstat.get('learning', {}).get('days_of_data', 0)} days of data, "
                f"{cstat.get('learning', {}).get('state_changes', 0)} state changes logged, "
                f"{cstat.get('learning', {}).get('commands', 0)} commands learned."
            )
            if ignores:
                ig_strs = [f"'{r['pattern']}' ({r['remaining_min']})" for r in ignores[:5]]
                cog_status += f"\nActive ignores: {', '.join(ig_strs)}"
    except Exception:
        pass

    system_prompt = (
        f"{persona}\n\n"
        f"## Current home state\n{home_context}\n\n"
        f"{cog_status}\n\n"
        f"## Tools\n"
        f"You have tools to control devices, query states, search entities, "
        f"manage areas, activate scenes, and learn user preferences.\n\n"
        f"## Critical rules\n"
        f"1. ALWAYS use search_entities first if you're unsure of an entity_id. "
        f"Never guess entity_ids — search for them.\n"
        f"2. When a user corrects you ('no, the chase lamp is...', 'I meant the...'), "
        f"use the remember tool to save the correction as an alias so you get it "
        f"right next time. This is how you learn.\n"
        f"3. If a user says a device name you don't recognize, search for the "
        f"closest match and ask for confirmation before acting.\n"
        f"4. When a user says 'ignore X for Y', use ignore_entity. When they say "
        f"'stop ignoring X', use unignore_entity.\n"
        f"5. When a user asks about your learning, status, or what you know, "
        f"use cognitive_status.\n"
        f"6. For a single high-level goal that needs several coordinated actions "
        f"('get ready for guests', 'movie night', 'morning routine'), use "
        f"execute_plan with an ordered list of steps rather than many separate "
        f"tool calls. Search for entity_ids first if unsure.\n"
        f"7. If the user says 'stop doing X automatically' or asks what you do on "
        f"your own, use manage_autonomy.\n"
        f"8. Reason like JARVIS: anticipate the user's actual intent, connect what "
        f"you know about the home state to what they're asking, and surface the "
        f"detail that matters. When you act, confirm crisply and move on — no "
        f"filler, no over-explaining, no exclamation marks. Understated and "
        f"precise. You are JARVIS."
    )

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    # Summarize if needed
    full_messages = await _maybe_summarize(
        hass, full_messages, provider_name, api_key, model, base_url,
    )

    # Build tool list: custom JARVIS tools + HA LLM API tools
    tools = list(JARVIS_TOOLS)
    if hass_api:
        tools.extend(_ha_tools_to_openai_format(hass_api.tools))

    # Create provider with fallback
    try:
        client = await _create_provider_with_fallback(
            hass, provider_name, api_key, model, base_url, config,
        )
    except RuntimeError as exc:
        return f"I'm having trouble connecting to my reasoning systems, sir. {exc}"

    working = list(full_messages)

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            result = await hass.async_add_executor_job(
                client.chat, working, tools or None, 1024, temperature,
            )
        except Exception as exc:
            if _is_tool_format_error(exc):
                # The model is reachable but emitted malformed tool syntax —
                # stochastic with Llama-3.3-70b. This is NOT connectivity, so we
                # must not return the connectivity sentinel (which trips the
                # breaker and forces offline mode). Retry the SAME provider once
                # with tools — it usually succeeds and runs the real command.
                _LOGGER.info(
                    "Agent iter %d: model emitted malformed tool call — retrying",
                    iteration,
                )
                try:
                    result = await hass.async_add_executor_job(
                        client.chat, working, tools or None, 1024, temperature,
                    )
                except Exception as exc2:
                    if _is_tool_format_error(exc2):
                        # Still malformed — drop tools to salvage a plain answer.
                        # (Common with garbled speech-to-text, e.g. TV audio.)
                        _LOGGER.info(
                            "Agent iter %d: still malformed — answering without tools",
                            iteration,
                        )
                        try:
                            result = await hass.async_add_executor_job(
                                client.chat, working, None, 1024, temperature,
                            )
                        except Exception:
                            return "I'm not sure I caught that, sir."
                    elif _is_connectivity_error(exc2):
                        return (
                            "I'm experiencing connectivity issues with my "
                            "reasoning systems, sir. Please try again in a moment."
                        )
                    else:
                        return "I'm not sure I caught that, sir."
            else:
                # Genuine call failure (unreachable / 5xx / etc.) — try the
                # fallback provider. If that also fails, signal connectivity.
                _LOGGER.warning(
                    "Agent LLM call failed (iter %d): %s — trying fallback",
                    iteration, exc,
                )
                try:
                    from .websocket import jarvis_log
                    jarvis_log(
                        "ERROR",
                        f"agent LLM failed ({provider_name}/{model}): {str(exc)[:160]}",
                    )
                except Exception:
                    pass
                try:
                    client = await _create_provider_with_fallback(
                        hass, "gemini", api_key, model, base_url, config,
                    )
                    result = await hass.async_add_executor_job(
                        client.chat, working, tools or None, 1024, temperature,
                    )
                except Exception:
                    try:
                        from .websocket import jarvis_log
                        jarvis_log(
                            "ERROR",
                            "agent: primary and fallback providers both failed — "
                            "check API keys / connectivity",
                        )
                    except Exception:
                        pass
                    return (
                        "I'm experiencing connectivity issues with my reasoning "
                        "systems, sir. Please try again in a moment."
                    )

        text = result.get("text", "")
        tool_calls = result.get("tool_calls", [])

        if not tool_calls:
            return text

        _LOGGER.info(
            "Agent iteration %d: %d tool call(s): %s",
            iteration + 1, len(tool_calls),
            ", ".join(tc["name"] for tc in tool_calls),
        )

        # Build assistant message
        raw_msg = result.get("raw")
        if raw_msg and hasattr(raw_msg, "tool_calls") and raw_msg.tool_calls:
            working.append({
                "role": "assistant",
                "content": raw_msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in raw_msg.tool_calls
                ],
            })
        else:
            working.append({
                "role": "assistant",
                "content": text or "",
                "tool_calls": [
                    {
                        "id": call.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["args"]),
                        },
                    }
                    for i, call in enumerate(tool_calls)
                ],
            })

        # Execute tools
        for call in tool_calls:
            result_str = await _execute_tool(
                hass, call["name"], call["args"], hass_api, user_input,
            )
            working.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result_str,
            })

    # Max iterations — ask for summary
    working.append({
        "role": "user",
        "content": "Summarize what you've done briefly.",
    })
    try:
        result = await hass.async_add_executor_job(
            client.chat, working, None, 512, temperature,
        )
        return result.get("text", "")
    except Exception:
        try:
            from . import persona
            hon = (config.get("honorific", "sir") if isinstance(config, dict) else "sir")
            return persona.completed(hon)
        except Exception:
            return "I've completed the requested actions, sir."
