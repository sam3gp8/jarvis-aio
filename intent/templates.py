"""Air-gapped informational fallback templates for JARVIS.

When every model link is unreachable (cloud providers AND the local mind), JARVIS
can still voice common system updates from these hardcoded templates — no
inference required. This is a curated starting set grounded in this property's
entities, not an exhaustive 50; extend ``STATUS_TEMPLATES`` with the phrasings you
find yourself reaching for. Matching is keyword-based and entirely local (stdlib).

Usage:
    template_for("network_restored", honorific="sir")
    match_status("is the sump pump ok", honorific="sir")  # free-text → best template
"""
from __future__ import annotations

# key → (trigger keywords, spoken template). {honorific} is filled at lookup.
STATUS_TEMPLATES: dict[str, tuple[tuple[str, ...], str]] = {
    "all_clear": (
        ("all good", "all clear", "everything ok", "systems nominal", "status report", "sitrep"),
        "All systems are nominal, {honorific}. No active faults to report.",
    ),
    "offline_mode": (
        ("offline", "no internet", "internet down", "cloud down", "lost connection"),
        "We're offline, {honorific}. I'm running in local-only mode; core automations remain active.",
    ),
    "network_restored": (
        ("network back", "internet back", "connection restored", "back online"),
        "Network connectivity has been restored, {honorific}.",
    ),
    "core_switch_down": (
        ("core switch", "network switch", "switch offline"),
        "The core network switch is offline, {honorific}. I'd check its upstream power first.",
    ),
    "storage_high": (
        ("storage full", "disk full", "storage high", "root storage"),
        "Server storage is running high, {honorific}. Some cleanup is advisable soon.",
    ),
    "ram_high": (
        ("memory high", "ram high", "out of memory"),
        "System memory pressure is elevated, {honorific}.",
    ),
    "freeze_warning": (
        ("freezing", "freeze", "pipes", "cold"),
        "Temperatures are approaching freezing, {honorific}. The basement freeze sensor is being watched.",
    ),
    "freeze_sensor_down": (
        ("freeze sensor", "basement sensor offline"),
        "The basement freeze sensor has dropped offline, {honorific}; I can't confirm pipe temperatures.",
    ),
    "sump_pump": (
        ("sump pump", "basement water", "flooding"),
        "The sump pump status is the thing to check, {honorific}, especially after heavy rain.",
    ),
    "garage_open": (
        ("garage open", "garage door", "is the garage"),
        "I'd verify the garage door, {honorific}; I can secure it on your word.",
    ),
    "door_unlocked": (
        ("door unlocked", "is it locked", "lock status"),
        "Let me note the locks may need attention, {honorific}. Say the word to secure the house.",
    ),
    "reboot_complete": (
        ("rebooted", "restarted", "back up", "reboot complete"),
        "Systems are back up and I'm fully operational again, {honorific}.",
    ),
    "backup_done": (
        ("backup", "backed up", "snapshot"),
        "The latest backup completed successfully, {honorific}.",
    ),
    "update_done": (
        ("update complete", "updated", "new version"),
        "The update has been applied, {honorific}. I'm running the latest build.",
    ),
    "low_battery": (
        ("low battery", "battery low", "batteries"),
        "One or more sensors are reporting low battery, {honorific}.",
    ),
}


def template_for(key: str, *, honorific: str = "sir", **fmt: str) -> str | None:
    """Return the canned phrase for a known event key (formatted), or None."""
    entry = STATUS_TEMPLATES.get(key)
    if entry is None:
        return None
    return entry[1].format(honorific=honorific.title(), **fmt)


def match_status(phrase: str, *, honorific: str = "sir") -> str | None:
    """Best-effort free-text match to a template, scored by keyword hits. Returns
    the formatted phrase or None if nothing matches."""
    if not phrase:
        return None
    text = phrase.lower()
    best_key, best_score = None, 0
    for key, (keywords, _template) in STATUS_TEMPLATES.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_key, best_score = key, score
    if best_key is None or best_score == 0:
        return None
    return template_for(best_key, honorific=honorific)
