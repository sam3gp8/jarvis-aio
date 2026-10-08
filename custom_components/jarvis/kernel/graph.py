"""Typed, queryable knowledge graph (kernel primitive — roadmap Phase T).

A pure, immutable view over curated knowledge: **entities** (nodes) carrying
their attributes, and typed directed **relations** (edges) between them. It is
built from plain rows — the caller does the HA / DB extraction — so this stays
unit-testable and never touches I/O.

PURE (Phase T, first rung of the ladder): the structure and its query surface
exist and are unit-tested, but nothing in the live integration consumes it yet.
Later rungs wire a graph view onto ``kernel/world_model.py`` (``entities`` /
``relations`` / ``query``) in shadow, then parity against the current semantic
recall, then enforce behind ``KNOWLEDGE_GRAPH_ENFORCE`` with the present recall
as the fail-safe. Nothing here drives behaviour.

Row shapes mirror ``knowledge.py`` exactly, so a builder can hand rows straight
across:

  * fact:     ``{"subject","key","value","confidence"?,"source"?,"kind"?}``
  * relation: ``{"subject","predicate","object","confidence"?}``

Names are matched case-insensitively (an entity's display name is the first
spelling seen) so "Front Door" and "front door" are one node. Everything is a
frozen dataclass of plain values, hashable and cheap to pass around.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


def _norm(value) -> str:
    """Display form: stringified and stripped."""
    return str(value if value is not None else "").strip()


def _key(value) -> str:
    """Match form: normalized, lowercased — the identity used to merge nodes."""
    return _norm(value).lower()


def _conf(value, default: float = 1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Attribute:
    """One fact about an entity — a key/value with its confidence and source."""

    key: str
    value: str
    confidence: float = 1.0
    source: str = ""


@dataclass(frozen=True)
class Entity:
    """A node: a named subject carrying its attributes (sorted by key)."""

    name: str
    attributes: Tuple[Attribute, ...] = ()

    def get(self, key: str) -> Optional[str]:
        """The value of one attribute (case-insensitive key), or None."""
        k = _key(key)
        for a in self.attributes:
            if _key(a.key) == k:
                return a.value
        return None


@dataclass(frozen=True)
class Relation:
    """A typed directed edge: ``subject —predicate→ object``."""

    subject: str
    predicate: str
    object: str
    confidence: float = 1.0


@dataclass(frozen=True)
class KnowledgeGraph:
    """An immutable set of entities and typed relations, with a small query
    surface. Build it with :meth:`from_rows`; never mutate — derive a new one."""

    entities: Tuple[Entity, ...] = ()
    relations: Tuple[Relation, ...] = ()

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def from_rows(
        cls,
        facts: Iterable[Mapping] = (),
        relations: Iterable[Mapping] = (),
    ) -> "KnowledgeGraph":
        """Build a graph from plain fact and relation rows (knowledge.py shape).

        Facts attach as attributes to their ``subject`` node; relations add edges
        and implicitly create their endpoint nodes. Rows missing a required field
        are skipped. Pure and total — never raises on messy input.
        """
        # name_key -> display name (first spelling wins)
        names: Dict[str, str] = {}
        # name_key -> list[Attribute]
        attrs: Dict[str, List[Attribute]] = {}

        def _see(raw) -> str:
            disp = _norm(raw)
            k = disp.lower()
            if k and k not in names:
                names[k] = disp
            return k

        for f in facts or ():
            if not isinstance(f, Mapping):
                continue
            subj = _norm(f.get("subject"))
            key = _norm(f.get("key"))
            if not subj or not key:
                continue
            k = _see(subj)
            attrs.setdefault(k, []).append(Attribute(
                key=key,
                value=_norm(f.get("value")),
                confidence=_conf(f.get("confidence")),
                source=_norm(f.get("source")),
            ))

        rels: List[Relation] = []
        seen_edges = set()
        for r in relations or ():
            if not isinstance(r, Mapping):
                continue
            subj = _norm(r.get("subject"))
            pred = _norm(r.get("predicate"))
            obj = _norm(r.get("object"))
            if not subj or not pred or not obj:
                continue
            _see(subj)
            _see(obj)
            dedupe = (subj.lower(), pred.lower(), obj.lower())
            if dedupe in seen_edges:
                continue
            seen_edges.add(dedupe)
            rels.append(Relation(subject=subj, predicate=pred, object=obj,
                                  confidence=_conf(r.get("confidence"))))

        entities = tuple(
            Entity(name=names[k], attributes=tuple(
                sorted(attrs.get(k, []), key=lambda a: _key(a.key))))
            for k in sorted(names)
        )
        return cls(entities=entities, relations=tuple(rels))

    # ── queries (all pure, case-insensitive) ──────────────────────────────────
    def entity(self, name: str) -> Optional[Entity]:
        """The node with this name (case-insensitive), or None."""
        k = _key(name)
        for e in self.entities:
            if _key(e.name) == k:
                return e
        return None

    def relate(
        self,
        subject: Optional[str] = None,
        *,
        predicate: Optional[str] = None,
        object: Optional[str] = None,
    ) -> List[Relation]:
        """Relations matching any combination of subject / predicate / object
        (each case-insensitive; omit to leave that slot unconstrained)."""
        s, p, o = _key(subject) if subject else None, \
            _key(predicate) if predicate else None, \
            _key(object) if object else None
        out = []
        for r in self.relations:
            if s is not None and _key(r.subject) != s:
                continue
            if p is not None and _key(r.predicate) != p:
                continue
            if o is not None and _key(r.object) != o:
                continue
            out.append(r)
        return out

    def neighbors(self, name: str, *, predicate: Optional[str] = None) -> List[str]:
        """Names of entities directly reachable *out* of ``name`` (optionally
        only via ``predicate``), in first-seen order, de-duplicated."""
        out: List[str] = []
        seen = set()
        for r in self.relate(name, predicate=predicate):
            k = _key(r.object)
            if k not in seen:
                seen.add(k)
                out.append(r.object)
        return out

    def is_empty(self) -> bool:
        return not self.entities and not self.relations
