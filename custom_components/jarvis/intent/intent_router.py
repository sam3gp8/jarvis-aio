"""Local intent routing for JARVIS.

``LocalIntentRouter`` matches a spoken/typed phrase against a small table of
local command patterns (no cloud round-trip), resolves pronoun context ("turn it
off") to the active entity in a room, executes the change, and supports a short
interactive feedback window so an actionable announcement can be confirmed by
voice without a fresh wake word.

Module-level code is stdlib-only so the pure matching helpers
(``match_intent`` / ``is_affirmative``) load and test without Home Assistant.
Anything touching hass (entity-area lookup, service calls, the feedback timer)
is imported lazily inside the methods that need it.
"""
from __future__ import annotations

import logging
import re

_LOGGER = logging.getLogger(__name__)

# Fired when an actionable announcement opens a confirmation window; the voice
# satellite layer listens for this to start a short, wake-word-free capture.
EVENT_FEEDBACK_WINDOW = "jarvis_feedback_window"
FEEDBACK_TIMEOUT_S = 10.0

# Intent table — ordered most-specific first so "turn off the lights" matches
# the light intent rather than the pronoun ("it") intent.
_INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("secure_area", (
        r"\bsecure\b",
        r"\bclose\b.*\bgarage\b",
        r"\block\s+(up|down|the|it)\b",
        r"\barm\b.*\b(garage|house|home|alarm)\b",
    )),
    ("lights_off", (
        r"\b(turn|switch|shut)\s+off\b.*\blight",
        r"\blights?\s+off\b",
        r"\bkill\s+the\s+lights?\b",
    )),
    ("lights_on", (
        r"\b(turn|switch)\s+on\b.*\blight",
        r"\blights?\s+on\b",
    )),
    ("context_off", (
        r"\bturn\s+it\s+off\b",
        r"\bswitch\s+it\s+off\b",
        r"\bshut\s+it\s+off\b",
        r"\bturn\s+that\s+off\b",
        r"\bpause\s+it\b",
    )),
    ("context_close", (
        r"\bclose\s+it\b",
        r"\bshut\s+it\b",
    )),
)

_AFFIRMATIVE_PATTERNS: tuple[str, ...] = (
    r"\b(yes|yeah|yep|yup|sure|ok|okay|affirmative|confirm(ed)?|proceed|go ahead|do it)\b",
    r"\b(close|shut|secure)\s+it\b",
)

_ACTIVE_MEDIA_STATES = {"playing"}
_ACTIVE_LIGHT_STATES = {"on"}
_ACTIVE_GENERIC_STATES = {"on", "open"}


def match_intent(phrase: str) -> dict | None:
    """Return {"intent": name, "raw": phrase} for the first matching pattern, or
    None if the phrase doesn't map to a known local command. Pure function."""
    if not phrase:
        return None
    text = phrase.lower().strip()
    for name, patterns in _INTENT_PATTERNS:
        if any(re.search(pat, text) for pat in patterns):
            return {"intent": name, "raw": phrase}
    return None


def is_affirmative(phrase: str) -> bool:
    """True if the phrase reads as a yes/confirmation. Pure function."""
    if not phrase:
        return False
    text = phrase.lower().strip()
    return any(re.search(pat, text) for pat in _AFFIRMATIVE_PATTERNS)


class LocalIntentRouter:
    """Route phrases to local actions, with room-context pronoun resolution and
    a short voice-confirmation window."""

    def __init__(self, hass, *, ledger=None, mutex=None) -> None:
        self.hass = hass
        self.ledger = ledger  # optional StateLedger (duck-typed); write-ahead for high-stakes
        self.mutex = mutex    # optional EntityLockRegistry (duck-typed); concurrency control
        self._pending_feedback: dict | None = None
        self._feedback_cancel = None

    # ── Area helper (lazy import keeps the module HA-free at import time) ──
    def _area_of(self, entity_id: str):
        from .. import audio_routing  # lazy
        return audio_routing.entity_area(self.hass, entity_id)

    # ── Context resolution ────────────────────────────────────────────────
    def resolve_active_entity(
        self, area_id: str,
        domains: tuple[str, ...] = ("media_player", "light"),
        *, area_of=None,
    ) -> tuple[str | None, str | None]:
        """Find the entity a pronoun ("it") most likely refers to in an area:
        a playing media_player first, then an 'on' light, then any other 'on'/
        'open' entity in the requested domains. Returns (entity_id, domain)."""
        resolve_area = area_of or self._area_of
        for domain in domains:
            for st in self.hass.states.async_all(domain):
                try:
                    if resolve_area(st.entity_id) != area_id:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                state = str(st.state).lower()
                if domain == "media_player" and state in _ACTIVE_MEDIA_STATES:
                    return st.entity_id, domain
                if domain == "light" and state in _ACTIVE_LIGHT_STATES:
                    return st.entity_id, domain
                if domain not in ("media_player", "light") and state in _ACTIVE_GENERIC_STATES:
                    return st.entity_id, domain
        return None, None

    # ── Execution ─────────────────────────────────────────────────────────
    async def _call_domain_in_area(
        self, domain: str, service: str, area_id: str,
        *, high_stakes: bool = False, desired_state: str | None = None,
        priority: int | None = None,
    ) -> list[str]:
        """Call a service on the entities of `domain` in `area_id`. Acquires a
        per-entity concurrency lock first (skipping entities held at equal-or-
        higher priority), writes a recovery-ledger intent before high-stakes
        actions, and releases the locks after. Returns the entity_ids acted on."""
        candidates: list[str] = []
        for st in self.hass.states.async_all(domain):
            try:
                if self._area_of(st.entity_id) != area_id:
                    continue
            except Exception:  # noqa: BLE001
                continue
            candidates.append(st.entity_id)
        if not candidates:
            return []

        # Concurrency control: only act on entities we can lock at this priority.
        tokens: dict = {}
        if self.mutex is not None and priority is not None:
            targets: list[str] = []
            for eid in candidates:
                token = self.mutex.try_acquire(eid, priority)
                if token is not None:
                    tokens[eid] = token
                    targets.append(eid)
                else:
                    _LOGGER.info("intent: %s busy (higher-priority lock) — skipping", eid)
            if not targets:
                return []
        else:
            targets = candidates

        # Write-ahead: durably record intent BEFORE issuing a high-stakes call.
        txns: list[str] = []
        if high_stakes and self.ledger is not None and desired_state is not None:
            for eid in targets:
                try:
                    txns.append(
                        self.ledger.record_intent(
                            eid, desired_state, action=f"{domain}.{service}"
                        )
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("ledger record_intent failed for %s", eid)

        try:
            await self.hass.services.async_call(
                domain, service, {"entity_id": targets}, blocking=True
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("intent: %s.%s failed for %s", domain, service, targets)
            self._release_all(tokens)
            return []

        for txn in txns:
            try:
                self.ledger.mark_complete(txn)
            except Exception:  # noqa: BLE001
                pass
        self._release_all(tokens)
        return targets

    def _release_all(self, tokens: dict) -> None:
        if self.mutex is None:
            return
        for token in tokens.values():
            try:
                self.mutex.release(token)
            except Exception:  # noqa: BLE001
                pass

    async def execute(self, decision: dict, area_id: str, *, user_id: str | None = None) -> dict:
        """Carry out a matched intent in the given area."""
        intent = decision["intent"]

        priority = None
        if self.mutex is not None:
            from ..automation.mutex import Priority  # lazy — keeps module HA-free
            priority = Priority.INTENT

        if intent == "lights_off":
            acted = await self._call_domain_in_area(
                "light", "turn_off", area_id, priority=priority
            )
            return {"executed": bool(acted), "intent": intent, "entities": acted}

        if intent == "lights_on":
            acted = await self._call_domain_in_area(
                "light", "turn_on", area_id, priority=priority
            )
            return {"executed": bool(acted), "intent": intent, "entities": acted}

        if intent == "secure_area":
            covers = await self._call_domain_in_area(
                "cover", "close_cover", area_id,
                high_stakes=True, desired_state="closed", priority=priority,
            )
            locks = await self._call_domain_in_area(
                "lock", "lock", area_id,
                high_stakes=True, desired_state="locked", priority=priority,
            )
            return {
                "executed": bool(covers or locks),
                "intent": intent,
                "entities": covers + locks,
            }

        if intent in ("context_off", "context_close"):
            entity_id, domain = self.resolve_active_entity(area_id)
            if entity_id is None:
                return {"executed": False, "intent": intent,
                        "reason": "nothing active to act on in this area"}
            service = "close_cover" if domain == "cover" else "turn_off"
            token = None
            if self.mutex is not None and priority is not None:
                token = self.mutex.try_acquire(entity_id, priority)
                if token is None:
                    return {"executed": False, "intent": intent, "entity_id": entity_id,
                            "reason": "entity busy (higher-priority lock)"}
            try:
                await self.hass.services.async_call(
                    domain, service, {"entity_id": entity_id}, blocking=True
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("intent: context action failed for %s", entity_id)
                if token is not None:
                    self.mutex.release(token)
                return {"executed": False, "intent": intent, "entity_id": entity_id}
            if token is not None:
                self.mutex.release(token)
            return {"executed": True, "intent": intent, "entity_id": entity_id}

        return {"executed": False, "intent": intent, "reason": "unhandled intent"}

    async def route(self, phrase: str, target_area: str, *, user_id: str | None = None) -> dict:
        """Match a phrase and execute it. Returns a result dict; never raises."""
        decision = match_intent(phrase)
        if decision is None:
            _LOGGER.debug("intent: no local match for %r", phrase)
            return {"matched": False, "intent": None, "executed": False}
        result = await self.execute(decision, target_area, user_id=user_id)
        return {"matched": True, **result}

    # ── Interactive feedback window ───────────────────────────────────────
    async def open_feedback_window(
        self, pending_action: dict, *, timeout: float = FEEDBACK_TIMEOUT_S
    ) -> None:
        """Arm a short confirmation window. Fires EVENT_FEEDBACK_WINDOW for the
        voice satellite layer and auto-disarms after `timeout` seconds.

        `pending_action` should contain at least {"intent": ..., "area": ...}.
        """
        from homeassistant.helpers.event import async_call_later  # lazy

        self._cancel_feedback()
        self._pending_feedback = pending_action
        self.hass.bus.async_fire(
            EVENT_FEEDBACK_WINDOW, {"timeout": timeout, "action": pending_action}
        )

        def _expire(_now):
            self._pending_feedback = None
            self._feedback_cancel = None

        self._feedback_cancel = async_call_later(self.hass, timeout, _expire)

    async def handle_voice_response(self, phrase: str) -> dict:
        """Process a phrase captured during an open feedback window. If the
        window is open and the phrase is affirmative, execute the pending
        action."""
        if self._pending_feedback is None:
            return {"handled": False, "reason": "no open window"}
        if not is_affirmative(phrase):
            return {"handled": False, "affirmative": False}
        pending = self._pending_feedback
        self._cancel_feedback()
        result = await self.execute({"intent": pending["intent"]}, pending["area"])
        return {"handled": True, **result}

    def _cancel_feedback(self) -> None:
        if self._feedback_cancel is not None:
            try:
                self._feedback_cancel()
            except Exception:  # noqa: BLE001
                pass
        self._feedback_cancel = None
        self._pending_feedback = None
