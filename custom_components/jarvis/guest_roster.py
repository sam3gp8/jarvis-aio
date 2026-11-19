"""
JARVIS — known guests & friends roster (per-person notes).

A user-curated list of recurring *non-resident* visitors — friends, relatives,
regular guests — each with a short free-text note of context JARVIS should keep
in mind whenever that person is around: e.g. "best friend, fine to be here
alone", "don't disturb him", "let me know when he arrives". Household residents
live in ``face_roster.py``; this is the parallel store for *known guests*
(issue #549).

JARVIS does not run its own face engine — identities come from the vision
backend (Frigate / Double Take / CompreFace; see ``recognition.py``). This layer
only attaches the user's notes to a recognized name, matched by the same
normalized-name key ``identity.normalize`` uses, so "Bob", "bob" and "  Bob  "
are the same guest.

Stored as JSON at ``<config>/jarvis/guest_roster.json`` as
``{normalized_name: {"name": display, "note": text}}``. Dependency-light stdlib
only, atomic writes, never raises to the caller.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

ROSTER_PATH = config_path_str("jarvis", "guest_roster.json")

_lock = threading.RLock()
# {normalized_name: {"name": display_name, "note": note_text}}
_roster: dict[str, dict] = {}
_loaded = False


def _normalize(name: str) -> str:
    try:
        from .identity import normalize
        return normalize(name)
    except Exception:
        return "_".join((name or "").strip().lower().split())


def _entry(name: str, note: str = "") -> dict:
    return {"name": str(name or "").strip(), "note": str(note or "").strip()}


def _load() -> None:
    global _loaded, _roster
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        data: dict[str, dict] = {}
        try:
            if os.path.exists(ROSTER_PATH):
                with open(ROSTER_PATH) as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        key = _normalize(str(k))
                        if not key:
                            continue
                        if isinstance(v, dict):
                            disp = str(v.get("name") or k).strip()
                            note = str(v.get("note") or "").strip()
                        else:
                            # Tolerate a legacy {name: note_string} shape.
                            disp = str(k).strip()
                            note = str(v or "").strip()
                        data[key] = _entry(disp, note)
        except Exception as exc:
            _LOGGER.warning("guest roster load failed: %s", exc)
            data = {}
        _roster = data
        _loaded = True


def _save() -> None:
    # Called with _lock held. Persists the {norm: {name, note}} map.
    try:
        os.makedirs(os.path.dirname(ROSTER_PATH), exist_ok=True)
        tmp = ROSTER_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_roster, f)
        os.replace(tmp, ROSTER_PATH)
    except Exception as exc:
        _LOGGER.debug("guest roster persist failed: %s", exc)


def guests() -> list[dict]:
    """Known guests as ``[{"name", "note"}, …]``, sorted by name
    (case-insensitive). Never raises."""
    _load()
    return sorted(
        ({"name": e["name"], "note": e.get("note", "")} for e in _roster.values()),
        key=lambda e: e["name"].casefold(),
    )


def is_known_guest(name: str) -> bool:
    """Whether ``name`` (any casing/spacing) is a curated known guest. Never raises."""
    if not name:
        return False
    _load()
    return _normalize(name) in _roster


def note_for(name: str) -> str:
    """The user's note for a known guest, or ``""`` if unknown / no note.
    Never raises."""
    if not name:
        return ""
    _load()
    e = _roster.get(_normalize(name))
    return str(e.get("note", "")) if e else ""


def set_guest(name: str, note: str = "") -> bool:
    """Add or update a known guest and their note. Returns True if the guest was
    newly added (False if an existing one was updated). Never raises."""
    key = _normalize(name)
    if not key:
        return False
    _load()
    with _lock:
        is_new = key not in _roster
        _roster[key] = _entry(name, note)
        _save()
        return is_new


def remove_guest(name: str) -> bool:
    """Remove a known guest. Returns True if one was present. Never raises."""
    key = _normalize(name)
    if not key:
        return False
    _load()
    with _lock:
        if key not in _roster:
            return False
        _roster.pop(key, None)
        _save()
        return True
