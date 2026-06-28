"""
JARVIS — Config and schema migrations.

When the addon updates and config fields change — fields renamed, removed,
or new required fields added — migrations transform old configs into new
ones so existing installations upgrade smoothly instead of breaking.

How it works:
  - Every config entry carries a 'schema_version' integer
  - On setup, we check the entry's version against CURRENT_SCHEMA_VERSION
  - If the entry is older, run each migration in sequence
  - Save the new version back to the entry

To add a new migration:
  1. Bump CURRENT_SCHEMA_VERSION
  2. Add a function migrate_N_to_N1(data, options) -> (data, options)
  3. Append (N, migrate_N_to_N1) to MIGRATIONS

Never delete an old migration — someone three versions behind needs all of
them to walk forward to current.
"""
from __future__ import annotations

import logging
from typing import Callable

_LOGGER = logging.getLogger(__name__)


# ─── Current schema version ──────────────────────────────────────────────────
#
# Bump this whenever you change what's in entry.data or entry.options.
# v1 = initial release (Groq-only, hardcoded tts_engine)
# v2 = added tts_premium_engine + tts_premium_contexts
# v3 = added directive_preset + directive
# v4 = added llm_provider + llm_base_url (multi-provider support)
# v5 = added three-tier audio (voice_satellites/reply_speakers/broadcast_speakers)
# v6 = added observer mode (proactive reasoning, Gemini tier providers, sleep detection)
# v7 = area-registry-driven routing; flat entity lists removed in favor of
#      HA area registry + bedroom_areas toggle + broadcast_group entity

CURRENT_SCHEMA_VERSION = 7


# ─── Migrations ──────────────────────────────────────────────────────────────

def migrate_1_to_2(data: dict, options: dict) -> tuple[dict, dict]:
    """v1 → v2: introduce tts_premium_engine, tts_premium_contexts."""
    options.setdefault("tts_premium_engine", "")
    options.setdefault(
        "tts_premium_contexts",
        ["briefing", "camera", "doorbell", "recognition"],
    )
    _LOGGER.info("JARVIS migration v1→v2: added premium TTS options")
    return data, options


def migrate_2_to_3(data: dict, options: dict) -> tuple[dict, dict]:
    """v2 → v3: introduce directive system."""
    options.setdefault("directive_preset", "guardian_steward")
    options.setdefault("directive", "")
    _LOGGER.info("JARVIS migration v2→v3: added prime directive")
    return data, options


def migrate_3_to_4(data: dict, options: dict) -> tuple[dict, dict]:
    """v3 → v4: introduce multi-provider LLM support.

    Existing installs are Groq-only. We preserve that by defaulting to
    the same provider they already had.
    """
    options.setdefault("llm_provider", "groq")
    options.setdefault("llm_base_url", "")
    _LOGGER.info("JARVIS migration v3→v4: added multi-provider LLM support")
    return data, options


def migrate_4_to_5(data: dict, options: dict) -> tuple[dict, dict]:
    """v4 → v5: introduce three-tier audio architecture.

    Existing installs have a single 'cast_speakers' list. We keep that for
    backward compat but ALSO promote it to 'broadcast_speakers' so proactive
    announcements (briefings, sentinel) keep working as before. The user can
    then split listening devices out of it via the addon UI.
    """
    options.setdefault("voice_satellites", [])
    options.setdefault("reply_speakers", [])
    # Promote legacy cast_speakers to broadcast_speakers if not already set
    legacy = options.get("cast_speakers", [])
    if legacy and "broadcast_speakers" not in options:
        options["broadcast_speakers"] = list(legacy)
    else:
        options.setdefault("broadcast_speakers", [])
    options.setdefault("room_routing", True)
    _LOGGER.info(
        "JARVIS migration v4→v5: three-tier audio (promoted %d legacy speakers "
        "to broadcast_speakers; voice_satellites/reply_speakers default empty)",
        len(legacy),
    )
    return data, options


def migrate_5_to_6(data: dict, options: dict) -> tuple[dict, dict]:
    """
    v5 → v6: Observer Mode (proactive reasoning).

    Adds all observer-related config with safe defaults:
      - observer_enabled: False (opt-in)
      - Gemini tier providers default to the standard allocation
      - Presence/sleep entity lists default to empty
      - Quiet hours default to 22:00–07:00
    """
    options.setdefault("observer_enabled", False)
    options.setdefault("gemini_api_key", "")
    options.setdefault("classifier_provider", "gemini")
    options.setdefault("classifier_model", "gemini-2.5-flash-lite")
    options.setdefault("reasoning_provider", "gemini")
    options.setdefault("reasoning_model", "gemini-2.5-flash")
    options.setdefault("review_provider", "gemini")
    options.setdefault("review_model", "gemini-2.5-pro")
    options.setdefault("observer_presence_entities", [])
    options.setdefault("observer_sleep_entities", [])
    options.setdefault("observer_ignore", [])
    options.setdefault(
        "observer_categories",
        ["appliances", "doors_windows", "presence", "security"],
    )
    options.setdefault("observer_quiet_start", "22:00")
    options.setdefault("observer_quiet_end", "07:00")
    options.setdefault("notify_service", "")
    _LOGGER.info(
        "JARVIS migration v5→v6: Observer Mode config added (disabled by default)"
    )
    return data, options


def migrate_6_to_7(data: dict, options: dict) -> tuple[dict, dict]:
    """
    v6 → v7: Area-registry-driven routing.

    Removes the flat entity lists (voice_satellites, reply_speakers,
    broadcast_speakers, observer_presence_entities, observer_sleep_entities,
    observer_ignore, observer_categories, cast_speakers, room_routing).
    JARVIS now reads HA's area registry directly.

    Adds:
      - bedroom_areas (empty list — user must toggle areas in options flow)
      - broadcast_group (promoted from legacy broadcast_speakers[0] if any)

    Keeps legacy values intact under their old keys (ignored by new code,
    but harmless). This means a user rolling back to v6 won't find themselves
    with an empty config — the old data is still there.
    """
    # Promote the first broadcast_speakers entry (if any) to broadcast_group
    legacy_broadcast = options.get("broadcast_speakers", []) or []
    if legacy_broadcast and not options.get("broadcast_group"):
        options["broadcast_group"] = legacy_broadcast[0]
        _LOGGER.info(
            "JARVIS migration v6→v7: promoted broadcast_speakers[0]=%s to broadcast_group",
            legacy_broadcast[0],
        )

    # User must pick bedrooms post-migration; empty default is safe (no sleep
    # detection until they toggle areas in the options flow)
    options.setdefault("bedroom_areas", [])

    _LOGGER.info(
        "JARVIS migration v6→v7: area-registry-driven routing. User should "
        "review Settings → Devices & Services → JARVIS → Configure to set "
        "bedroom_areas toggle."
    )
    return data, options


# (source_version, migration_function)
MIGRATIONS: list[tuple[int, Callable]] = [
    (1, migrate_1_to_2),
    (2, migrate_2_to_3),
    (3, migrate_3_to_4),
    (4, migrate_4_to_5),
    (5, migrate_5_to_6),
    (6, migrate_6_to_7),
]


# ─── Runner ──────────────────────────────────────────────────────────────────

def migrate_config(
    data: dict,
    options: dict,
    current_version: int = 1,
) -> tuple[dict, dict, int]:
    """
    Walk migrations forward from current_version to CURRENT_SCHEMA_VERSION.
    Returns (new_data, new_options, new_version).
    """
    if current_version >= CURRENT_SCHEMA_VERSION:
        return data, options, current_version

    _LOGGER.info(
        "JARVIS: migrating config from v%d to v%d",
        current_version, CURRENT_SCHEMA_VERSION,
    )

    for source_version, migration_fn in MIGRATIONS:
        if current_version == source_version:
            try:
                data, options = migration_fn(data, options)
                current_version = source_version + 1
            except Exception as exc:
                _LOGGER.error(
                    "JARVIS migration v%d→v%d failed: %s",
                    source_version, source_version + 1, exc,
                )
                break

    return data, options, current_version
