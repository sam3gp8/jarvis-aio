"""User-configurable exclusion of entities from JARVIS's awareness.

An entity can be excluded three ways, matching how people think about their
setups:

  * ``excluded_entities`` — specific entity_ids, one by one.
  * ``excluded_domains``  — whole domains (e.g. ``light``, ``switch``).
  * ``excluded_labels``   — every entity carrying a Home Assistant label
                            (the "as a group" case).

Excluded entities are dropped everywhere JARVIS enumerates entities on its own —
presence detection, room routing, the observer, and pattern/state learning — so
noise sources (a virtual occupancy sensor that other integrations expose, unused
light/switch entities) stop polluting those systems. Exclusion removes an entity
from JARVIS's *awareness*; Home Assistant still has the entity and JARVIS can
still act on it if you ask for it by name.

Config is read from the in-memory ``runtime_config`` (seeded from config.json at
setup, kept current by the panel), never via ``jarvis_config.get()``, because
``is_excluded()`` runs on the hot state-change path and a lazy config load there
both costs time and mutates shared module state.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

try:  # label registry is present on modern HA; degrade gracefully if not
    from homeassistant.helpers import label_registry as _lr
except Exception:  # pragma: no cover - very old HA
    _lr = None

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _as_list(value) -> list:
    """Coerce a config value (list, JSON string, or scalar) into a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, list) else [s]
        except Exception:
            return [s]
    return []


def _exclusion_config(hass: HomeAssistant):
    """Return (entities:set, domains:set, labels:set) from runtime_config."""
    ents: set[str] = set()
    doms: set[str] = set()
    labs: set[str] = set()
    try:
        for entry_data in (hass.data.get(DOMAIN) or {}).values():
            if not isinstance(entry_data, dict):
                continue
            rc = entry_data.get("runtime_config") or {}
            got = False
            for e in _as_list(rc.get("excluded_entities")):
                if e:
                    ents.add(str(e))
                    got = True
            for d in _as_list(rc.get("excluded_domains")):
                if d:
                    doms.add(str(d).strip().lower())
                    got = True
            for lab in _as_list(rc.get("excluded_labels")):
                if lab:
                    labs.add(str(lab))
                    got = True
            if got:
                break
    except Exception:
        pass
    return ents, doms, labs


def is_excluded(hass: HomeAssistant, entity_id: Optional[str]) -> bool:
    """True if the user has excluded this entity (by id, domain, or label).

    Cheap and safe to call on the hot state-change path: a tiny dict read plus,
    only when labels are configured, an in-memory registry lookup.
    """
    if not entity_id:
        return False
    ents, doms, labs = _exclusion_config(hass)
    if not ents and not doms and not labs:
        return False
    if entity_id in ents:
        return True
    domain = entity_id.split(".", 1)[0].lower()
    if domain in doms:
        return True
    if labs:
        try:
            ent = er.async_get(hass).async_get(entity_id)
            ent_labels = getattr(ent, "labels", None) if ent else None
            if ent_labels:
                # Match by label_id directly …
                if set(ent_labels) & labs:
                    return True
                # … and by human-readable label name, so a config that stored
                # names (or ids) both work.
                if _lr is not None:
                    reg = _lr.async_get(hass)
                    for lid in ent_labels:
                        lbl = reg.async_get_label(lid)
                        if lbl is not None and getattr(lbl, "name", None) in labs:
                            return True
        except Exception:
            pass
    return False


# ── Camera object-class sensors: person vs. not-a-person (#254) ───────────────
#
# Frigate (and ONVIF analytics) expose, per camera and per detected object
# *class*, a binary_sensor like ``binary_sensor.garage_car_occupancy`` /
# ``binary_sensor.driveway_dog_motion`` alongside the per-person
# ``*_person_occupancy``. Those non-person sensors turn ``on`` for a parked
# car, a passing animal or a delivered package — none of which means a *human*
# is present. Counting them as occupancy made JARVIS treat the garage as
# "occupied" whenever a car was parked there (discussion #221 → #254).
#
# ``is_nonperson_object_sensor`` recognises those non-person object-class
# sensors so the human-presence paths (presence.py, audio_routing.py) can skip
# them. It is intentionally conservative — it only fires when a known
# non-person object label sits in the Frigate/COCO sensor shape (the label
# token immediately before an occupancy/motion/presence suffix, or as the
# final token) — so an ordinary room sensor (``binary_sensor.kitchen_presence``)
# or any sensor that names ``person`` is never mistaken for one. The
# pattern-learning / automation-suggestion engine deliberately does NOT use
# this: learning "when the car is in the bay, …" is a legitimate use of a car
# sensor; only *human* presence must exclude it.

_NONPERSON_OBJECT_LABELS = frozenset({
    # vehicles
    "car", "truck", "bus", "van", "motorcycle", "motorbike", "bicycle", "bike",
    "boat", "train", "airplane", "vehicle", "trailer", "scooter",
    # animals
    "dog", "cat", "bird", "horse", "sheep", "cow", "bear", "deer", "fox",
    "squirrel", "rabbit", "raccoon", "possum", "animal", "pet",
    # delivered objects
    "package", "parcel", "delivery",
})

# Tokens that mark a sensor as a presence/motion-type signal. A non-person
# label counts as an object-class sensor only when it sits right before one of
# these (or is the final token), which is the Frigate/ONVIF naming shape.
_PRESENCE_SUFFIX_TOKENS = frozenset({
    "occupancy", "occupied", "motion", "presence", "present", "detected",
    "detection", "active", "moving", "contact", "all",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics (so '_', spaces and '-' all split)."""
    out: list[str] = []
    cur: list[str] = []
    for ch in str(text).lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def is_nonperson_object_sensor(entity_id: Optional[str],
                               friendly_name: Optional[str] = None) -> bool:
    """True when this looks like a camera object-class sensor for a NON-person
    object (a car/animal/package detector), which must not count as a human
    being present. Pure and cheap — no hass, no config. See the section note.
    """
    if not entity_id and not friendly_name:
        return False

    def _matches(tokens: list[str]) -> bool:
        if not tokens or "person" in tokens:
            return False  # a person sensor (or the word 'person') is never excluded
        last = len(tokens) - 1
        for i, tok in enumerate(tokens):
            if tok not in _NONPERSON_OBJECT_LABELS:
                continue
            if i == last or tokens[i + 1] in _PRESENCE_SUFFIX_TOKENS:
                return True
        return False

    # entity_id: classify on the object part only (drop the 'binary_sensor.' domain)
    if entity_id:
        obj = str(entity_id).split(".", 1)[-1]
        if _matches(_tokenize(obj)):
            return True
    if friendly_name and _matches(_tokenize(friendly_name)):
        return True
    return False


def excluded_entity_ids(hass: HomeAssistant) -> set:
    """The concrete set of currently-excluded entity_ids, expanding domains and
    labels across the live registry. For UI / bulk use — NOT the hot path, where
    ``is_excluded`` should be called per entity instead.
    """
    ents, doms, labs = _exclusion_config(hass)
    out = set(ents)
    if not doms and not labs:
        return out
    try:
        for st in hass.states.async_all():
            eid = st.entity_id
            if eid not in out and is_excluded(hass, eid):
                out.add(eid)
    except Exception:
        pass
    return out
