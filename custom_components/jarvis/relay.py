"""
JARVIS — relay a spoken message to a household member (#540).

JARVIS as the household intermediary: *"tell dad dinner's ready"* → JARVIS
announces it on the home's speakers, framed as a message for that person, and
notes whether they are currently home. Very MCU: passing word between people in
the house.

Deliberately **not** here (they are their own, larger increments — see the
issue): deferred delivery (*"tell them next time you see them"*), room-precise
routing to the one speaker nearest the recipient (household presence is
anonymous occupancy — a radar sensor knows a room is occupied, not *who* is in
it), and a two-way ESPHome intercom.

The text-shaping and recipient matching are pure (no Home Assistant), so they
are unit-testable directly; the single async entry point does the announce.
"""
from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Filler dropped when matching a spoken recipient to a known person.
_RECIP_STOP = {"the", "my", "to", "for", "please", "tell", "let", "know"}


def _norm_name(s: str) -> str:
    """Lowercase, collapse separators/whitespace. Pure."""
    s = (s or "").strip().lower()
    return " ".join(s.replace("_", " ").split())


def resolve_recipient(name: str, people: list) -> Optional[dict]:
    """Best-effort match of a spoken recipient — ``"dad"``, ``"Sam"``, ``"mum"``
    — to a known person from presence (``people`` is a list of
    ``{"name", "state", ...}`` as :func:`presence.get_presence_summary` returns).
    Returns the matched person dict, or ``None``.

    A relationship word like *dad* usually has no matching Home Assistant person
    entity, so ``None`` is common and completely fine — the relay still names the
    recipient aloud; the match only lets JARVIS add a "they're away" note. Pure;
    returns ``None`` on any bad input rather than raising.
    """
    want = _norm_name(name)
    if not want or not isinstance(people, list):
        return None
    toks = [t for t in want.split() if t not in _RECIP_STOP]
    if not toks:
        return None
    want_core = " ".join(toks)
    exact, contains = None, None
    for p in people:
        if not isinstance(p, dict):
            continue
        pname = _norm_name(str(p.get("name", "")))
        if not pname:
            continue
        if pname == want_core or pname == want:
            exact = p
            break
        # first-name / substring either direction ("sam" ↔ "sam smith")
        if contains is None and (
            want_core in pname or pname in want_core
            or any(t == pname.split()[0] for t in toks)
        ):
            contains = p
    return exact or contains


def compose_announcement(to_name: str, message: str,
                         from_name: Optional[str] = None) -> str:
    """The spoken relay line. Pure.

    - with a sender:  ``"Sam asked me to tell Dad: dinner's ready."``
    - without:        ``"Message for Dad: dinner's ready."``
    - no recipient:   ``"Announcement: dinner's ready."``
    """
    msg = (message or "").strip()
    to = (to_name or "").strip()
    frm = (from_name or "").strip()
    if not msg:
        return ""
    if not to:
        body = f"Announcement: {msg}"
    elif frm:
        body = f"{frm} asked me to tell {to}: {msg}"
    else:
        body = f"Message for {to}: {msg}"
    if body and body[-1] not in ".!?":
        body += "."
    return body


def presence_note(person: Optional[dict]) -> str:
    """A short status note for the tool's reply (not spoken). ``""`` when the
    recipient is unknown or home; a heads-up when they are away. Pure."""
    if not isinstance(person, dict):
        return ""
    state = str(person.get("state", "")).strip().lower()
    if state in ("not_home", "away"):
        nm = str(person.get("name", "they")).strip() or "they"
        return (f"{nm} appears to be away right now, so they may not hear this "
                f"until they're home.")
    return ""


async def async_relay(hass, to_name: str, message: str,
                      from_name: Optional[str] = None) -> dict:
    """Announce a message for ``to_name`` on the household speakers.

    Returns a result dict for the tool layer: ``{success, spoken, speakers,
    recipient_state, note}`` on success, or ``{success: False, error}``. Never
    raises — any failure becomes a clear error string.
    """
    msg = (message or "").strip()
    if not msg:
        return {"success": False, "error": "message is required"}
    text = compose_announcement(to_name, msg, from_name)

    # Resolve the recipient against presence (best-effort, only for the note).
    recipient = None
    try:
        from . import presence
        people = presence.get_presence_summary(hass).get("people", [])
        recipient = resolve_recipient(to_name, people)
    except Exception:
        recipient = None

    # House broadcast speakers through JARVIS's own routing.
    try:
        from . import audio_routing
        speakers = [s for s in audio_routing.broadcast_target(hass)
                    if not str(s).startswith("assist_satellite.")]
    except Exception as exc:
        return {"success": False, "error": f"could not resolve speakers: {exc}"}
    if not speakers:
        return {"success": False,
                "error": ("no announcement speakers are configured — set a "
                          "broadcast group in JARVIS Settings → Audio")}

    try:
        from .tts_helper import async_announce
        await async_announce(hass, text, None, speakers, context="announcement")
    except Exception as exc:
        return {"success": False, "error": f"announcement failed: {exc}"}

    try:
        from .websocket import jarvis_log
        # Record the operation, not the recipient — the debug log is readable by
        # non-admins, so keep the user-named recipient out of it (GHSA #574).
        jarvis_log("AGENT", f"relay ({'broadcast' if not to_name else 'targeted'}) "
                            f"on {len(speakers)} speaker(s)")
    except Exception:
        pass

    return {
        "success": True,
        "spoken": text,
        "speakers": speakers,
        "recipient_state": (recipient or {}).get("state"),
        "note": presence_note(recipient),
    }
