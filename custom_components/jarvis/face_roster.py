"""
JARVIS — household face roster (resident whitelist).

A small, JARVIS-native list of people who are household *residents*. JARVIS does
not run its own face engine — identities come from the vision backend (Frigate
face recognition, DoubleTake, CompreFace; see ``recognition.py``). This module
layers a user-curated "who is a resident" flag on top of those recognized names,
so JARVIS can tell a known resident from an unknown face and use that to refine
intrusion escalation and presence (issue #140).

Stored at ``<config>/jarvis/face_roster.json`` and matched by the same
normalized-name key ``identity.normalize`` uses, so "Sam", "sam", and "  Sam  "
are the same resident. The file is a plain JSON list by default
(``["Sam", "Alex"]``); when aliases are registered it becomes
``{"residents": [...], "aliases": {"sam_g": "Sam"}}`` so backends that spell a
resident differently (Frigate ``"sam_g"`` vs DoubleTake ``"Sam"``) resolve to one
identity (#41 §4). Legacy ``{normalized: display}`` maps still load.
Dependency-light stdlib only, atomic writes, never raises to the caller.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

ROSTER_PATH = config_path_str("jarvis", "face_roster.json")

_lock = threading.RLock()
# {normalized_name: display_name}
_roster: dict[str, str] = {}
# {normalized_alias: canonical_normalized_name} — an alias always resolves to a
# name present in _roster (dangling aliases are dropped on load/save).
_aliases: dict[str, str] = {}
_loaded = False


def _normalize(name: str) -> str:
    try:
        from .identity import normalize
        return normalize(name)
    except Exception:
        return "_".join((name or "").strip().lower().split())


def _load() -> None:
    global _loaded, _roster, _aliases
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        data: dict[str, str] = {}
        aliases: dict[str, str] = {}

        def _add_entry(display, alias_list=None) -> None:
            key = _normalize(str(display or ""))
            if not key:
                return
            data[key] = str(display).strip()
            for a in (alias_list or []):
                ak = _normalize(str(a))
                if ak and ak != key:
                    aliases[ak] = key

        try:
            if os.path.exists(ROSTER_PATH):
                with open(ROSTER_PATH) as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    # Bare list of names (preferred) or list of {name, aliases}.
                    for n in raw:
                        if isinstance(n, dict):
                            _add_entry(n.get("name"), n.get("aliases"))
                        else:
                            _add_entry(n)
                elif isinstance(raw, dict) and isinstance(raw.get("residents"), list):
                    # Rich format: {"residents": [...], "aliases": {alias: name}}.
                    for n in raw["residents"]:
                        if isinstance(n, dict):
                            _add_entry(n.get("name"), n.get("aliases"))
                        else:
                            _add_entry(n)
                    side = raw.get("aliases")
                    if isinstance(side, dict):
                        for a, canon in side.items():
                            ak = _normalize(str(a))
                            ck = _normalize(str(canon))
                            if ak and ck and ck in data and ak != ck:
                                aliases[ak] = ck
                elif isinstance(raw, dict):
                    # Legacy {normalized: display} map.
                    for k, v in raw.items():
                        key = _normalize(str(k))
                        if key:
                            data[key] = str(v or k).strip()
        except Exception as exc:
            _LOGGER.warning("face roster load failed: %s", exc)
            data, aliases = {}, {}
        _roster = data
        # Never keep an alias whose canonical resident isn't present.
        _aliases = {a: c for a, c in aliases.items() if c in data}
        _loaded = True


def _save() -> None:
    # Called with _lock held. Persists a plain list when there are no aliases
    # (unchanged on-disk shape, backward-compatible with plain-list readers), or
    # the rich {"residents", "aliases"} object when aliases exist.
    try:
        os.makedirs(os.path.dirname(ROSTER_PATH), exist_ok=True)
        tmp = ROSTER_PATH + ".tmp"
        if _aliases:
            payload: object = {
                "residents": sorted(_roster.values(), key=str.casefold),
                "aliases": {a: _roster[c] for a, c in sorted(_aliases.items())
                            if c in _roster},
            }
        else:
            payload = sorted(_roster.values(), key=str.casefold)
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, ROSTER_PATH)
    except Exception as exc:
        _LOGGER.debug("face roster persist failed: %s", exc)


def residents() -> list[str]:
    """Household resident display names, sorted (case-insensitive). Never raises."""
    _load()
    return sorted(_roster.values(), key=str.casefold)


def is_resident(name: str) -> bool:
    """Whether ``name`` (any casing/spacing, or a registered backend alias) is a
    flagged household resident. Never raises."""
    if not name:
        return False
    _load()
    norm = _normalize(name)
    return norm in _roster or _aliases.get(norm) in _roster


def canonical_name(name: str) -> str:
    """The unified resident display name for ``name`` — resolving a backend alias
    (Frigate's ``"sam_g"``, DoubleTake's ``"Sam"``) to one identity. Returns the
    roster's display spelling when ``name`` is a resident or a registered alias;
    otherwise the input name, stripped (it never fabricates an identity for an
    unknown person or an unlisted guest). Never raises."""
    if not name:
        return ""
    _load()
    norm = _normalize(name)
    if norm in _roster:
        return _roster[norm]
    canon = _aliases.get(norm)
    if canon and canon in _roster:
        return _roster[canon]
    return str(name).strip()


# Names the vision backend uses for "no identity" — these are strangers, not
# guests. Kept in step with ``recognition._is_unknown`` (which also matches the
# ``unrecognis(z)ed`` spellings some backends emit).
_UNKNOWN_NAMES = frozenset({
    "", "unknown", "unknown person", "unknown_face", "unrecognized", "unrecognised",
})


def is_guest(name: str) -> bool:
    """Whether ``name`` is a recognized *guest* — a person the vision backend
    identified by name who is NOT a flagged household resident (a friend,
    relative or other visitor). Blank/unknown names are strangers, not guests,
    so they return False (issue #388). Never raises."""
    if (name or "").strip().lower() in _UNKNOWN_NAMES:
        return False
    return not is_resident(name)


def relationship(name: str) -> str:
    """Classify a recognized person as ``"resident"``, ``"guest"`` or
    ``"unknown"``. This is the single source of truth for the
    household/guest/stranger distinction surfaced to Home Assistant automations
    (the ``jarvis_face_recognized`` event) and the Faces panel (issue #388):
    residents are flagged household members, guests are recognized people who
    aren't, and unknown is an unidentified face. Never raises."""
    if (name or "").strip().lower() in _UNKNOWN_NAMES:
        return "unknown"
    return "resident" if is_resident(name) else "guest"


def add_resident(name: str) -> bool:
    """Flag ``name`` a resident. Returns True if it was newly added. Never raises."""
    key = _normalize(name)
    if not key:
        return False
    _load()
    with _lock:
        if key in _roster:
            # Keep the latest display spelling, but report "not new".
            _roster[key] = str(name).strip()
            _save()
            return False
        _roster[key] = str(name).strip()
        _save()
        return True


def add_alias(alias: str, name: str) -> bool:
    """Register ``alias`` as another backend label for resident ``name`` (adding
    ``name`` to the roster if it isn't already). Returns True if the alias was
    newly mapped. A no-op when alias and name normalize to the same key. Never
    raises."""
    akey = _normalize(alias)
    nkey = _normalize(name)
    if not akey or not nkey or akey == nkey:
        return False
    _load()
    with _lock:
        if nkey not in _roster:
            _roster[nkey] = str(name).strip()
        if _aliases.get(akey) == nkey:
            _save()
            return False
        _aliases[akey] = nkey
        _save()
        return True


def remove_resident(name: str) -> bool:
    """Un-flag ``name`` and drop any aliases pointing at it. Returns True if it
    was present. Never raises."""
    key = _normalize(name)
    if not key:
        return False
    _load()
    with _lock:
        if key not in _roster:
            return False
        _roster.pop(key, None)
        for a in [a for a, c in _aliases.items() if c == key]:
            _aliases.pop(a, None)
        _save()
        return True


def unflagged_ha_persons(hass) -> list[str]:
    """HA ``person.*`` friendly names that are NOT flagged residents (nor a known
    alias), so the Faces HUD can gently surface household members who haven't been
    whitelisted yet (#41 §4). Read-only; respects excluded entities; sorted and
    de-duplicated. Never raises."""
    try:
        try:
            from .entity_filter import is_excluded as _excl
        except Exception:
            _excl = lambda _h, _e: False  # noqa: E731
        _load()
        out: list[str] = []
        seen: set[str] = set()
        for st in hass.states.async_all("person"):
            try:
                if _excl(hass, st.entity_id):
                    continue
            except Exception:
                pass
            nm = (st.attributes.get("friendly_name")
                  or st.entity_id.split(".", 1)[-1]).strip()
            low = nm.lower()
            if not nm or low in seen:
                continue
            seen.add(low)
            if not is_resident(nm):
                out.append(nm)
        return sorted(out, key=str.casefold)
    except Exception:
        return []
