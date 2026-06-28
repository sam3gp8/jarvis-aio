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

import logging
import yaml
from typing import Any, Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def create_automation(
    hass: HomeAssistant,
    *,
    alias: str,
    description: str = "",
    trigger: list[dict] | dict = None,
    condition: list[dict] | dict | None = None,
    action: list[dict] | dict = None,
    mode: str = "single",
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
