"""
JARVIS persona voice — phrase variety with a coherent character.

The point of variety is not randomness for its own sake; it is that JARVIS never
sounds like a recording. The same intent gets phrased a little differently each
time, but always within one register: dry, precise, deferential without being
servile — and deliberately PLAINER as the situation grows graver. JARVIS does not
quip during a smoke alarm.

Phrases are organized by SPEECH ACT and REGISTER. Selection is random with a
short anti-repeat memory so the same line doesn't surface twice in a row. This is
a dependency-free leaf (stdlib only) so any module can speak in JARVIS's voice.

    register ∈ "light" | "neutral" | "urgent" | "grave"

Public API:
    acknowledge(honorific, register)   — confirming a command ("At once, sir.")
    completed(honorific, register)     — after an action ("That's handled.")
    working(honorific)                 — buying a moment ("One moment, sir.")
    unable(honorific)                  — a graceful no ("I'm afraid I can't, sir.")
    greeting(honorific, hour)          — time-aware ("Good evening, sir.")
    announce_opener(honorific, reg)    — leads a household announcement
    register_for(urgency)              — map an urgency to a voice register
"""

from __future__ import annotations

import bisect
import random
import time
from collections import deque
from typing import Optional

# Full variety by default. Flip off for minimal, consistent phrasing.
_VARIETY = True

# Banter level (v6.51.0): how much MCU-JARVIS dry wit surfaces at LIGHT/NEUTRAL
# register. Set from config at speak-time via set_banter(). Levels:
#   0 = off (plain, the pre-6.51 phrasing)
#   1 = dry (occasional wit — the tasteful default)
#   2 = full (Stark's JARVIS — witty asides, more character)
# URGENT/GRAVE are never affected: JARVIS does not quip during a smoke alarm.
_BANTER = 1


def set_variety(on: bool) -> None:
    global _VARIETY
    _VARIETY = bool(on)


def set_banter(level) -> None:
    """0 plain · 1 dry · 2 full. Silently clamps; bad input → dry."""
    global _BANTER
    try:
        _BANTER = max(0, min(2, int(level)))
    except Exception:
        _BANTER = 1


def _banter_from_cfg() -> None:
    """Refresh banter level from runtime config (called before speaking)."""
    try:
        from . import jarvis_config
        val = jarvis_config.get("banter_level", None)
        if val is not None:
            set_banter(val)
    except Exception:
        pass


# ── Phrase pools.  {H} → capitalized honorific, {h} → lowercase ──────────────

_ACK = {
    "light": [
        "Of course, {h}.", "Right away, {h}.", "Consider it done.", "On it, {h}.",
        "Certainly.", "As you wish, {h}.", "Happily, {h}.", "Very good, {h}.",
        "Straight away.", "But of course, {h}.",
    ],
    "neutral": [
        "Of course, {h}.", "Right away, {h}.", "Certainly, {h}.", "Consider it done.",
        "At once, {h}.", "Very good, {h}.", "As you wish, {h}.",
    ],
    "urgent": [
        "At once, {h}.", "Right away.", "On it.", "Immediately, {h}.", "Done.",
    ],
}

_DONE = {
    "light": [
        "Done, {h}.", "That's handled.", "All set, {h}.", "Taken care of.",
        "There we are.", "Complete, {h}.", "Sorted.", "That's done, {h}.",
    ],
    "neutral": [
        "Done, {h}.", "That's complete, {h}.", "Handled, {h}.", "All set.",
        "Taken care of, {h}.",
    ],
    "urgent": ["Done.", "Handled, {h}.", "Complete."],
}

_GREET = {
    "morning": [
        "Good morning, {h}.", "Morning, {h}.", "A good morning to you, {h}.",
        "Good morning. I trust you slept well, {h}.",
    ],
    "afternoon": ["Good afternoon, {h}.", "Afternoon, {h}.", "Good afternoon to you, {h}."],
    "evening": ["Good evening, {h}.", "Evening, {h}.", "Good evening to you, {h}."],
    "night": [
        "Working late, {h}?", "Good evening, {h}.", "Burning the midnight oil, {h}?",
        "Still up, {h}? Good evening.",
    ],
}

# Announcement openers, by register. 'grave' stays plain on purpose.
_OPENER = {
    "neutral": [
        "{H},", "For your awareness, {h} —", "A small matter, {h} —",
        "If I may, {h} —", "Just so you know, {h} —", "A note, {h} —",
        "Worth mentioning, {h} —",
    ],
    "urgent": [
        "{H}, your attention —", "{H}, you should know —", "{H}, if I may —",
        "{H}, a moment —", "{H} —",
    ],
    "grave": ["{H}.", "{H} —"],
}

_WORKING = {
    "neutral": [
        "One moment, {h}.", "Looking into it.", "Working on it, {h}.",
        "Just a moment.", "Allow me a moment, {h}.", "Let me see, {h}.",
    ],
}

_UNABLE = {
    "neutral": [
        "I'm afraid I can't do that, {h}.",
        "That's beyond me at the moment, {h}.",
        "I'm not able to manage that just yet, {h}.",
        "Regrettably, {h}, that's outside what I can do.",
        "I wish I could, {h}, but that's not within my reach.",
    ],
}

# ── Full-banter overlays (v6.51.0), MCU-JARVIS register ──────────────────────
# Merged onto the LIGHT pool only when _BANTER == 2. Dry, clever, still
# unmistakably deferential — never sarcastic toward the user, never during
# anything urgent. These are the lines that make JARVIS sound like JARVIS.
_ACK_FULL = [
    "As you wish, {h}.", "Consider it already done, {h}.",
    "With pleasure, {h}.", "A fine idea, {h}. On it.",
    "Naturally, {h}.", "Say no more, {h}.",
    "Ever your instrument, {h}.", "At once — I do live to serve, {h}.",
]
_DONE_FULL = [
    "Done, {h} — effortless, as ever.", "Handled. You're welcome, {h}.",
    "There we are. I do enjoy being useful, {h}.",
    "Complete, {h}. Try not to look so surprised.",
    "Taken care of. I'll add it to my considerable tally, {h}.",
    "All set, {h}. Another small triumph.",
]
_UNABLE_FULL = [
    "I'm afraid that's beyond even my talents, {h} — and they are not modest.",
    "Would that I could, {h}. Alas, I have limits. Few, but real.",
    "That one's outside my remit, {h}. Do tell Mr. Stark I said so.",
    "I must decline, {h} — not from reluctance, from incapacity.",
]
# Occasional dry asides the agent LLM may append (level 2 only). Kept short.
_ASIDES = [
    "Will there be anything else, {h}?",
    "A pleasure, as always.",
    "I'll be here. I'm always here.",
]


# ── Anti-repeat picker ───────────────────────────────────────────────────────

_recent: dict[str, deque] = {}


def _fill(template: str, honorific: str) -> str:
    h = (honorific or "sir").strip() or "sir"
    return template.replace("{H}", h[:1].upper() + h[1:]).replace("{h}", h)


def _pick(pool: list, key: str, honorific: str) -> str:
    if not pool:
        return ""
    if not _VARIETY:
        return _fill(pool[0], honorific)
    # Avoid the last few choices for this category so nothing repeats back-to-back.
    span = min(3, max(1, len(pool) - 1))
    dq = _recent.setdefault(key, deque(maxlen=span))
    choices = [i for i in range(len(pool)) if i not in dq]
    if not choices:
        choices = list(range(len(pool)))
    i = random.choice(choices)
    dq.append(i)
    return _fill(pool[i], honorific)


def _reg(pools: dict, register: str) -> list:
    return pools.get(register) or pools.get("neutral") or next(iter(pools.values()))


# ── Public API ───────────────────────────────────────────────────────────────

_REGISTER_BY_URGENCY = {"critical": "grave", "high": "urgent"}


def register_for(urgency: Optional[str]) -> str:
    return _REGISTER_BY_URGENCY.get((urgency or "").lower(), "neutral")


def _banter_pool(base_pools: dict, register: str, full_overlay: list) -> list:
    """Pool for a speech act at a register, widened with full-banter lines
    only at LIGHT register when banter is maxed. URGENT/GRAVE never widen."""
    pool = list(_reg(base_pools, register))
    if _BANTER >= 2 and register == "light" and full_overlay:
        pool = pool + full_overlay
    return pool


def acknowledge(honorific: str = "sir", register: str = "neutral") -> str:
    _banter_from_cfg()
    return _pick(_banter_pool(_ACK, register, _ACK_FULL),
                 f"ack:{register}:{_BANTER}", honorific)


def completed(honorific: str = "sir", register: str = "neutral") -> str:
    _banter_from_cfg()
    return _pick(_banter_pool(_DONE, register, _DONE_FULL),
                 f"done:{register}:{_BANTER}", honorific)


def working(honorific: str = "sir") -> str:
    return _pick(_WORKING["neutral"], "working", honorific)


def unable(honorific: str = "sir") -> str:
    _banter_from_cfg()
    pool = list(_UNABLE["neutral"])
    if _BANTER >= 2:
        pool = pool + _UNABLE_FULL
    return _pick(pool, f"unable:{_BANTER}", honorific)


def aside(honorific: str = "sir") -> str:
    """An optional dry closing aside — empty unless full banter is on. Callers
    may append this to a response for extra character; safe to ignore."""
    _banter_from_cfg()
    if _BANTER < 2:
        return ""
    return _pick(_ASIDES, "aside", honorific)


# Hour → daypart, as edges for a branch-free bisect lookup. Buckets:
#   [0,5)=night  [5,12)=morning  [12,17)=afternoon  [17,22)=evening  [22,24)=night
_GREET_EDGES = [5, 12, 17, 22]
_GREET_BUCKETS = ["night", "morning", "afternoon", "evening", "night"]


def greeting(honorific: str = "sir", hour: Optional[int] = None) -> str:
    if hour is None:
        hour = time.localtime().tm_hour
    bucket = _GREET_BUCKETS[bisect.bisect_right(_GREET_EDGES, hour)]
    return _pick(_GREET[bucket], f"greet:{bucket}", honorific)


def announce_opener(honorific: str = "sir", register: str = "neutral") -> str:
    return _pick(_reg(_OPENER, register), f"open:{register}", honorific)
