"""World-model facade (kernel Phase 2, docs/KERNEL_PLAN.md).

A single **read-only** view over the facts JARVIS already has — Home Assistant
state, the knowledge graph, identity/presence and scene memory — answered in
canonical terms (people / rooms / devices / facts / relationships) rather than as
raw entity ids and ad-hoc dict shapes. Reasoning paths migrate onto it one caller
at a time; the raw sources stay underneath and authoritative.

Design notes:
  * **Read-only.** Nothing here writes state, fires events, or mutates a store.
  * **Best-effort.** Every method degrades to an empty/None result rather than
    raising, so a caller can lean on it without wrapping each call.
  * **Thin + delegating.** Each non-HA source is reached through a small
    module-level seam (``_presence_summary`` etc.) that lazy-imports the owning
    module. That keeps this file's import surface tiny and lets tests substitute
    a fake source without touching Home Assistant.
  * **Sync.** The knowledge/scene sources are SQLite-backed and synchronous;
    callers on the event loop should invoke those methods via an executor, as
    they already do for the underlying modules.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


# ── source seams (lazy, patchable) ──────────────────────────────────────────────
# Each wraps one underlying module so WorldModel never imports them at module load
# and tests can monkeypatch a single function.

def _entity_area(hass, entity_id: str) -> Optional[str]:
    from .. import audio_routing
    return audio_routing.entity_area(hass, entity_id)


def _presence_summary(hass) -> dict:
    from .. import presence
    return presence.get_presence_summary(hass)


def _quick_person(hass, area_id: Optional[str]) -> str:
    from .. import identity
    return identity.quick_person(hass, area_id)


def _all_facts(subject: Optional[str]) -> List[dict]:
    from .. import knowledge
    return knowledge.all_facts(subject=subject)


def _related(subject: Optional[str], obj: Optional[str], predicate: Optional[str]) -> List[dict]:
    from .. import knowledge
    return knowledge.related(subject=subject, obj=obj, predicate=predicate)


def _where_last_seen(term: str) -> Optional[dict]:
    from ..vision import scene_memory
    return scene_memory.where_last_seen(term)


# ── area resolution ─────────────────────────────────────────────────────────────

def _area_of(hass, entity_id: str, state: Any = None) -> Optional[str]:
    """Best-effort area for an entity: the codebase's canonical helper first,
    then the state's own area attribute as a fallback."""
    try:
        area = _entity_area(hass, entity_id)
        if area:
            return area
    except Exception:
        pass
    try:
        st = state if state is not None else hass.states.get(entity_id)
        if st is not None:
            return st.attributes.get("area_id") or st.attributes.get("area")
    except Exception:
        pass
    return None


class WorldModel:
    """Canonical read facade over HA state + knowledge + identity + scene memory."""

    def __init__(self, hass, config: Optional[dict] = None) -> None:
        self._hass = hass
        self._config = config or {}

    # ── devices / rooms (Home Assistant state) ─────────────────────────────────
    def _canonical(self, state) -> Dict[str, Any]:
        eid = state.entity_id
        return {
            "entity_id": eid,
            "domain": eid.split(".", 1)[0] if "." in eid else eid,
            "name": state.attributes.get("friendly_name") or eid,
            "state": state.state,
            "area": _area_of(self._hass, eid, state),
            "attributes": dict(state.attributes),
        }

    def device(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Canonical snapshot of one entity, or None if it doesn't exist."""
        try:
            st = self._hass.states.get(entity_id)
        except Exception:
            return None
        return self._canonical(st) if st is not None else None

    def devices(
        self,
        *,
        domain: Optional[str] = None,
        area: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Canonical snapshots of entities, optionally filtered by domain and area."""
        try:
            states = self._hass.states.async_all(domain)
        except Exception:
            return []
        out = []
        for st in states:
            dev = self._canonical(st)
            if area is not None and dev["area"] != area:
                continue
            out.append(dev)
        return out

    def rooms(self) -> List[str]:
        """Distinct areas currently in use by known entities, sorted."""
        seen = set()
        for dev in self.devices():
            if dev["area"]:
                seen.add(dev["area"])
        return sorted(seen)

    # ── people / presence / identity ────────────────────────────────────────────
    def people(self) -> List[Dict[str, Any]]:
        """Canonical people roster with presence. Best-effort → [] on failure."""
        try:
            summary = _presence_summary(self._hass) or {}
            people = summary.get("people", []) or []
        except Exception as exc:
            _LOGGER.debug("world_model.people failed: %s", exc)
            return []
        out = []
        for p in people:
            if not isinstance(p, dict):
                continue
            state = p.get("state")
            out.append({
                "name": p.get("name"),
                "state": state,
                "home": state == "home",
                "area": p.get("area") or p.get("area_id"),
            })
        return out

    def person_in(self, area: Optional[str] = None) -> Optional[str]:
        """Best guess of who is in an area (or home), or None."""
        try:
            name = _quick_person(self._hass, area)
        except Exception as exc:
            _LOGGER.debug("world_model.person_in failed: %s", exc)
            return None
        return name or None

    # ── knowledge graph ─────────────────────────────────────────────────────────
    def facts(self, subject: Optional[str] = None) -> List[dict]:
        """Live curated facts, optionally about one subject. SYNC (DB-backed)."""
        try:
            return _all_facts(subject) or []
        except Exception as exc:
            _LOGGER.debug("world_model.facts failed: %s", exc)
            return []

    def beliefs(self, subject: Optional[str] = None) -> List[Any]:
        """Curated knowledge facts as kernel ``Belief`` values (MCU Phase E/E1).

        Generalises the flat per-fact ``confidence`` numbers into the kernel's
        probabilistic belief model (``kernel.beliefs``) — each fact becomes a
        Belief seeded from its confidence, prefixed by JARVIS's minimal identity
        self-assertion. SHADOW: this view is available and unit-tested, but no
        live decision consumes it yet, and the knowledge store stays authoritative.
        Best-effort → the identity belief alone (never empty) on any failure."""
        from . import beliefs as B
        out: List[Any] = [B.identity_assertion()]
        try:
            for f in (_all_facts(subject) or []):
                if not isinstance(f, dict):
                    continue
                subj = f.get("subject") or ""
                key = f.get("key") or ""
                val = f.get("value")
                prop = f"{subj}.{key}={val}" if subj else f"{key}={val}"
                out.append(B.seed_from_confidence(
                    prop, float(f.get("confidence", 1.0) or 1.0),
                    source=f.get("source") or "knowledge"))
        except Exception as exc:
            _LOGGER.debug("world_model.beliefs failed: %s", exc)
        return out

    def relationships(
        self,
        subject: Optional[str] = None,
        *,
        obj: Optional[str] = None,
        predicate: Optional[str] = None,
    ) -> List[dict]:
        """Typed relationship edges (knowledge graph). SYNC (DB-backed)."""
        try:
            return _related(subject, obj, predicate) or []
        except Exception as exc:
            _LOGGER.debug("world_model.relationships failed: %s", exc)
            return []

    # ── scene memory ────────────────────────────────────────────────────────────
    def last_seen(self, term: str) -> Optional[dict]:
        """Where an object/person was last seen by the cameras, or None. SYNC."""
        try:
            return _where_last_seen(term)
        except Exception as exc:
            _LOGGER.debug("world_model.last_seen failed: %s", exc)
            return None
