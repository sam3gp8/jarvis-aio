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
import time
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

# Per-source reliability for multi-source presence fusion (conflict adjudication).
# HA's own aggregate person.state is trusted, but an independent camera sighting
# is a strong "they are here" signal (a phone can die / be left behind).
_PRESENCE_SOURCE_RELIABILITY = {"person": 0.7, "camera": 0.8}
# A recent face sighting is treated as good for this long (presence fusion ttl).
_CAMERA_SIGHTING_TTL = 300.0


def _provenances_from_facts(rows) -> List[Any]:
    """Map curated fact dicts to kernel.provenance.Provenance records. Pure-ish
    (no I/O here); defensive — a bad row is skipped, never raised. The live
    shadow emission lives in the non-kernel ``knowledge`` module (#237); this is
    the reusable facade view exposed via :meth:`WorldModel.provenances`."""
    from . import provenance as P
    out: List[Any] = []
    for f in (rows or []):
        if not isinstance(f, dict):
            continue
        try:
            out.append(P.record(
                f.get("value"),
                source=str(f.get("source") or "knowledge"),
                confidence=float(f.get("confidence", 1.0) or 1.0),
                model=str(f.get("model") or ""),
            ))
        except Exception:   # pragma: no cover - defensive
            continue
    return out


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


def _who_is_where(hass) -> dict:
    from .. import recognition
    return recognition.who_is_where(hass) or {}


def _room_adjacency(config: dict) -> dict:
    from .. import residence_graph
    return residence_graph.room_adjacency(config or {})


def _now():
    """Local wall-clock now (HA-aware when available), for the temporal frame."""
    try:
        from homeassistant.util import dt as dt_util
        return dt_util.now()
    except Exception:
        import datetime
        return datetime.datetime.now()


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

    def _person_home_state(self, name: str) -> Optional[str]:
        """Read one person entity's own aggregate state as ``home``/``away`` by
        friendly name (or entity-id tail), or ``None`` if not found. Reads HA
        state directly — not the presence summary — so it never re-enters the
        presence read that may call this during fusion."""
        want = str(name or "").strip().lower()
        if not want:
            return None
        try:
            for st in self._hass.states.async_all("person"):
                fn = st.attributes.get("friendly_name") or st.entity_id.split(".", 1)[-1]
                if str(fn).strip().lower() == want:
                    return "home" if str(st.state) == "home" else "away"
        except Exception:   # pragma: no cover - defensive
            return None
        return None

    def fuse_presence(self, name: str, *, now: Optional[float] = None) -> Any:
        """Fuse a person's whereabouts from *independent* sources — HA's own
        aggregate ``person.state`` and a recent camera recognition — into one
        conflict-resolved verdict (Epistemic Fabric — Conflict, multi-source).

        Returns a :class:`kernel.conflict.Resolution` over
        :class:`kernel.provenance` records (value ``home``/``away``), ``contested``
        when the independent sources disagree within the margin — e.g. a camera
        sees Sam but their phone says away. Unlike the per-``device_tracker``
        adjudication in ``presence``, this fuses whole *source types*: it is the
        multi-source view the Conflict enforce rung will read. Observe-only facade
        — nothing gates on it yet. Best-effort → an empty ``Resolution`` on any
        failure (so a consumer falls back to HA's own resolution)."""
        from . import conflict as C
        from . import provenance as P
        try:
            nm = str(name or "").strip()
            if not nm:
                return C.Resolution()
            clock = time.time() if now is None else float(now)
            records = []
            # 1) HA person.state — its own aggregate (no stated expiry).
            state = self._person_home_state(nm)
            if state:
                records.append(P.record(state, source="person", confidence=1.0,
                                        now=(lambda c=clock: c)))
            # 2) Camera recognition — a recent sighting is an independent
            #    "they are here" observation, good for a bounded window.
            try:
                seen = _who_is_where(self._hass) or {}
                if any(str(v).strip().lower() == nm.lower() for v in seen.values()):
                    records.append(P.record("home", source="camera", confidence=1.0,
                                            now=(lambda c=clock: c),
                                            ttl=_CAMERA_SIGHTING_TTL))
            except Exception:   # pragma: no cover - defensive
                pass
            # Only a genuine ≥2-source case is a "fusion"; otherwise defer to HA.
            if len(records) < 2:
                return C.Resolution()
            return C.resolve(records, reliabilities=_PRESENCE_SOURCE_RELIABILITY,
                             now=clock)
        except Exception as exc:   # pragma: no cover - defensive
            _LOGGER.debug("world_model.fuse_presence failed: %s", exc)
            return C.Resolution()

    # ── knowledge graph ─────────────────────────────────────────────────────────
    def facts(self, subject: Optional[str] = None) -> List[dict]:
        """Live curated facts, optionally about one subject. SYNC (DB-backed)."""
        try:
            return _all_facts(subject) or []
        except Exception as exc:
            _LOGGER.debug("world_model.facts failed: %s", exc)
            return []

    def provenances(self, subject: Optional[str] = None) -> List[Any]:
        """Curated facts as kernel ``Provenance`` records (Epistemic Fabric, #237).

        Each fact becomes a Provenance pairing its value with where it came from
        (``source``), how sure we are (``confidence``) and the producing model.
        SHADOW: built on demand (and logged alongside ``facts()``), but nothing
        consumes it yet — conflict resolution / authoritative reads attach once it
        earns parity. Best-effort → empty list on any failure."""
        try:
            rows = _all_facts(subject) or []
        except Exception as exc:
            _LOGGER.debug("world_model.provenances failed: %s", exc)
            return []
        return _provenances_from_facts(rows)

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

    def uncertainties(self, subject: Optional[str] = None) -> List[Any]:
        """Curated facts as kernel ``Uncertain`` values (Epistemic Fabric —
        Uncertainty). Each fact's flat ``confidence`` becomes a first-class
        ``Uncertain`` (value + band known/believed/guessed/unknown + basis), so a
        consumer can reason over *what JARVIS knows vs. believes vs. guesses*, not
        just a bare number. SHADOW: this view is available and unit-tested, but no
        live decision gates on the band yet — ``identity.resolve`` is the
        Uncertainty parity anchor and the knowledge store stays authoritative.
        Best-effort → ``[]`` on any failure."""
        from . import uncertainty as U
        out: List[Any] = []
        try:
            for f in (_all_facts(subject) or []):
                if not isinstance(f, dict):
                    continue
                subj = f.get("subject") or ""
                key = f.get("key") or ""
                src = f.get("source") or "knowledge"
                basis = f"{src}:{subj}.{key}" if subj else f"{src}:{key}"
                out.append(U.assess(
                    f.get("value"), float(f.get("confidence", 1.0) or 1.0),
                    basis=basis))
        except Exception as exc:
            _LOGGER.debug("world_model.uncertainties failed: %s", exc)
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

    # ── knowledge graph view (roadmap Phase T) ───────────────────────────────────
    def knowledge_graph(self, subject: Optional[str] = None) -> Any:
        """The curated knowledge as a typed ``kernel.graph.KnowledgeGraph`` — the
        same facts()/relationships() this facade already reads, folded into
        entities + typed relations (MCU Phase T).

        SHADOW: this view is available and unit-tested, but no live decision
        consumes it yet; the knowledge store stays authoritative. Shadow →
        parity (graph vs current semantic recall) → enforce (a context read goes
        graph-authoritative behind KNOWLEDGE_GRAPH_ENFORCE, fail-safe = present
        recall). Best-effort → an empty graph on any failure."""
        from . import graph as G
        try:
            return G.KnowledgeGraph.from_rows(
                self.facts(subject), self.relationships(subject))
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("world_model.knowledge_graph failed: %s", exc)
            return G.KnowledgeGraph()

    def entities(self, subject: Optional[str] = None) -> List[Any]:
        """Graph entities (nodes) for the curated knowledge. SHADOW (Phase T)."""
        return list(self.knowledge_graph(subject).entities)

    def relations(
        self,
        subject: Optional[str] = None,
        *,
        predicate: Optional[str] = None,
        obj: Optional[str] = None,
    ) -> List[Any]:
        """Typed graph relations (edges), optionally filtered by subject /
        predicate / object. SHADOW (Phase T)."""
        return self.knowledge_graph(subject).relate(
            subject, predicate=predicate, object=obj)

    def query(
        self,
        subject: Optional[str] = None,
        *,
        predicate: Optional[str] = None,
        obj: Optional[str] = None,
    ) -> List[Any]:
        """Answer a context query against the knowledge graph — the relations
        matching the given ends. SHADOW (Phase T): the context-query surface the
        shadow/parity/enforce ladder builds on; nothing reads it authoritatively
        yet."""
        return self.relations(subject, predicate=predicate, obj=obj)

    # ── space & time view (roadmap Phase Q, Embodied JARVIS) ─────────────────────
    def spatial_graph(self, config: Optional[dict] = None) -> Any:
        """The home's floor plan as a ``kernel.space_time.SpatialGraph`` — areas
        and their adjacency, built from the same floor-plan adjacency the
        intrusion investigator already derives (``residence_graph.room_adjacency``).

        SHADOW (Phase Q): this view is available and unit-tested, but no live
        decision consumes it yet; the current per-feature mapping stays
        authoritative. Shadow → parity (camera↔sensor mapping, cf. #140) →
        enforce (presence/coverage/routing read the model behind
        ``SPACE_TIME_ENFORCE``, fail-safe = present mapping). Uses ``config`` when
        given, else the config this facade was built with. Best-effort → an empty
        graph on any failure."""
        from . import space_time as ST
        try:
            adjacency = _room_adjacency(config if config is not None else self._config)
            return ST.SpatialGraph.from_adjacency(adjacency)
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("world_model.spatial_graph failed: %s", exc)
            return ST.SpatialGraph()

    def temporal_frame(self, now: Any = None) -> Any:
        """The current moment as a ``kernel.space_time.TemporalFrame`` — the hour
        and weekday resolved to a coarse daypart. SHADOW (Phase Q): available and
        unit-tested, nothing reads it authoritatively yet. ``now`` is a
        ``datetime`` (defaults to local wall-clock). Best-effort → an "unknown"
        frame on any failure."""
        from . import space_time as ST
        try:
            dt = now if now is not None else _now()
            return ST.TemporalFrame.at(dt.hour, getattr(dt, "weekday", lambda: None)())
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("world_model.temporal_frame failed: %s", exc)
            return ST.TemporalFrame()

    # ── scene memory ────────────────────────────────────────────────────────────
    def last_seen(self, term: str) -> Optional[dict]:
        """Where an object/person was last seen by the cameras, or None. SYNC."""
        try:
            return _where_last_seen(term)
        except Exception as exc:
            _LOGGER.debug("world_model.last_seen failed: %s", exc)
            return None
