"""
JARVIS — deferred "notify X next time you see or hear them" (#540, increment 2).

The headline of #540: *hold* a message for a household member and deliver it the
next time JARVIS actually detects that person — when they arrive home, or when a
camera recognises them. Increment 1 (9.133.0, :mod:`relay`) shipped the
*immediate* announce ("tell dad dinner's ready"); this is the deferred queue the
owner emphasised.

Two shapes, both from the issue's "notify him / me / mum next time you see …":

  • **A held message for the person** — "tell dad, next time you see him, his
    tool order is done." When dad next appears JARVIS announces it to him.
  • **A sighting alert to someone else** — "let me know next time you see mum."
    When mum next appears JARVIS pings the asker (the owner's phone, or another
    resident on the speakers).

The store is **persistent** (a held message must survive a restart), keyed by the
normalised *target* (who to watch for). Everything is defensive: a read returns
``[]`` / ``0``, a write returns ``False``, and the delivery path never raises into
the event loop. A process-local "anything pending?" flag lets the hot
recognition / presence hooks short-circuit with no DB hit in the common case
(nothing queued) — call :func:`warm_start` once at setup so the flag is correct
after a restart.

Delivery is triggered from the places that already *name* a person: the presence
listener (``person``/``device_tracker`` → ``home``) and camera recognition
(:func:`recognition.remember_recognition`). The literal "hear" (voice-ID) trigger
is a small follow-up — the same :func:`async_maybe_deliver` entry point will take
it.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Optional

from .paths import config_path_str
from .sqlite_utils import ClosingConnection

_LOGGER = logging.getLogger(__name__)

DB_PATH = config_path_str("jarvis", "patterns.db")

# Bounds — a confused model or a busy household must not grow the queue without
# limit. Oldest-first within a target; enqueue is rejected past these.
_MAX_PER_TARGET = 20
_MAX_TOTAL = 200
_MAX_MSG_CHARS = 300
_DEFAULT_TTL_DAYS = 14

# "notify" values that mean "tell the owner" (push to their phone) rather than a
# named household member.
_OWNER_WORDS = {"me", "myself", "i", "owner", "my phone", "the owner"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_person TEXT NOT NULL,
    target_display TEXT NOT NULL,
    notify_display TEXT,
    notify_owner INTEGER DEFAULT 0,
    message TEXT NOT NULL,
    from_person TEXT,
    created_ts REAL NOT NULL,
    expires_ts REAL,
    status TEXT DEFAULT 'pending',
    delivered_ts REAL,
    delivered_trigger TEXT
);
CREATE INDEX IF NOT EXISTS idx_nq_target ON notify_queue(target_person, status);
"""

# Process-local "is anything pending?" flag so the hooks cost nothing when the
# queue is empty (the overwhelming common case). Seeded from the DB by
# warm_start() at setup; kept in step by every write path here.
_has_pending = False


# ── helpers (pure) ───────────────────────────────────────────────────────────
def _normalize(person: str) -> str:
    try:
        from . import identity
        return identity.normalize(person)
    except Exception:
        return " ".join(str(person or "").strip().lower().replace("_", " ").split())


def is_owner_notify(notify: Optional[str]) -> bool:
    """True when a spoken ``notify`` target means 'the owner' ('me', 'myself', …).
    Pure; blank is not the owner (blank means 'the target themselves')."""
    return _normalize(notify) in _OWNER_WORDS if notify else False


def _trigger_phrase(trigger: str) -> str:
    """How JARVIS describes having detected the person, for the alert line."""
    t = str(trigger or "").strip().lower()
    if t == "home":
        return "has just arrived home"
    if t == "camera":
        return "has just been seen on camera"
    if t == "voice":
        return "was just heard"
    return "is here now"


def compose_delivery_line(target_display: str, message: str,
                          notify_display: Optional[str] = None,
                          from_person: Optional[str] = None,
                          notify_owner: bool = False,
                          trigger: str = "") -> str:
    """The line JARVIS speaks or pushes when the target appears. Pure.

    - held message for the target (``notify`` empty or == target):
        ``"Dad — a message for you: your tool order is done."``
        with a sender: ``"Dad — Sam asked me to pass on: …"``
    - sighting alert to the owner (``notify_owner``):
        ``"You asked to be told when Mum is around — she has just arrived home."``
        plus ``" Note: <message>"`` when a message was left.
    - sighting alert to another resident:
        ``"Sam — Mum has just arrived home. You wanted to know. Note: …"``
    """
    target = str(target_display or "").strip() or "they"
    msg = str(message or "").strip()
    frm = str(from_person or "").strip()
    notify = str(notify_display or "").strip()

    to_target = (not notify) or _normalize(notify) == _normalize(target)
    if to_target and not notify_owner:
        if frm:
            body = f"{target} — {frm} asked me to pass on: {msg}" if msg \
                else f"{target} — {frm} wanted a word with you."
        else:
            body = f"{target} — a message for you: {msg}" if msg \
                else f"{target} — I was asked to catch you."
        return _end(body)

    phrase = _trigger_phrase(trigger)
    note = f" Note: {msg}" if msg else ""
    if notify_owner:
        body = f"You asked to be told when {target} is around — {target} {phrase}.{note}"
    else:
        who = notify or "someone"
        body = f"{who} — {target} {phrase}. You wanted to know.{note}"
    return _end(body)


def _end(body: str) -> str:
    body = (body or "").strip()
    if body and body[-1] not in ".!?":
        body += "."
    return body


# ── store ────────────────────────────────────────────────────────────────────
def ensure_schema(db_path: str = DB_PATH) -> None:
    """Create the notify_queue table + index if missing. Idempotent. Never raises."""
    try:
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            conn.executescript(_SCHEMA)
    except Exception as exc:
        _LOGGER.debug("notify_queue ensure_schema failed: %s", exc)


def has_pending() -> bool:
    """Cheap process-local check used by the delivery hooks to avoid a DB hit when
    nothing is queued. Correct after :func:`warm_start`; conservative otherwise."""
    return _has_pending


def warm_start(db_path: str = DB_PATH) -> None:
    """Seed the process-local pending flag from the DB at integration setup, so a
    restart with queued messages still delivers. Never raises."""
    _refresh_flag(db_path)


def _refresh_flag(db_path: str = DB_PATH) -> None:
    global _has_pending
    try:
        ensure_schema(db_path)
        now = time.time()
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM notify_queue WHERE status = 'pending' "
                "AND (expires_ts IS NULL OR expires_ts > ?)", (now,),
            ).fetchone()
        _has_pending = bool(row and row[0])
    except Exception as exc:
        _LOGGER.debug("notify_queue refresh flag failed: %s", exc)
        # Fail open: assume something may be pending so a hook still checks.
        _has_pending = True


def enqueue(target_display: str, message: str, *,
            notify_display: Optional[str] = None,
            from_person: Optional[str] = None,
            ttl_days: int = _DEFAULT_TTL_DAYS,
            db_path: str = DB_PATH) -> Optional[int]:
    """Queue a message to deliver the next time ``target_display`` is detected.
    Returns the new row id, or ``None`` on bad input / when a cap is hit. Never
    raises. ``notify_display`` is who to tell when the target appears — omit it to
    deliver the message *to the target*; 'me'/'myself' pushes to the owner."""
    global _has_pending
    target = str(target_display or "").strip()
    msg = str(message or "").strip()
    if not target or not msg:
        return None
    target_norm = _normalize(target)
    if not target_norm:
        return None
    owner = is_owner_notify(notify_display)
    notify = "" if owner else str(notify_display or "").strip()
    now = time.time()
    try:
        days = int(ttl_days)
    except Exception:
        days = _DEFAULT_TTL_DAYS
    expires = now + days * 86400 if days > 0 else None
    try:
        ensure_schema(db_path)
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM notify_queue WHERE status = 'pending'"
            ).fetchone()[0]
            if total >= _MAX_TOTAL:
                _LOGGER.debug("notify_queue full (%d pending) — rejecting", total)
                return None
            per = conn.execute(
                "SELECT COUNT(*) FROM notify_queue "
                "WHERE status = 'pending' AND target_person = ?", (target_norm,),
            ).fetchone()[0]
            if per >= _MAX_PER_TARGET:
                _LOGGER.debug("notify_queue cap for %s (%d) — rejecting",
                              target_norm, per)
                return None
            cur = conn.execute(
                "INSERT INTO notify_queue (target_person, target_display, "
                "notify_display, notify_owner, message, from_person, created_ts, "
                "expires_ts, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (target_norm, target, notify or None, 1 if owner else 0,
                 msg[:_MAX_MSG_CHARS], (str(from_person).strip() or None)
                 if from_person else None, now, expires),
            )
            new_id = cur.lastrowid
        _has_pending = True
        return int(new_id) if new_id is not None else None
    except Exception as exc:
        _LOGGER.debug("notify_queue enqueue failed: %s", exc)
        return None


def pending_for(target: str, db_path: str = DB_PATH) -> list[dict]:
    """Pending, non-expired messages whose target matches ``target`` (normalised),
    oldest first. Never raises."""
    target_norm = _normalize(target)
    if not target_norm:
        return []
    try:
        now = time.time()
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM notify_queue WHERE status = 'pending' "
                "AND target_person = ? AND (expires_ts IS NULL OR expires_ts > ?) "
                "ORDER BY created_ts ASC", (target_norm, now),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.debug("notify_queue pending_for failed: %s", exc)
        return []


def list_pending(db_path: str = DB_PATH) -> list[dict]:
    """All pending, non-expired messages (for the 'what are you holding?' query and
    the panel), oldest first. Never raises."""
    try:
        now = time.time()
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM notify_queue WHERE status = 'pending' "
                "AND (expires_ts IS NULL OR expires_ts > ?) "
                "ORDER BY created_ts ASC", (now,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.debug("notify_queue list_pending failed: %s", exc)
        return []


def mark_delivered(row_id: int, trigger: str = "", db_path: str = DB_PATH) -> bool:
    """Mark one queued message delivered. Never raises."""
    try:
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            conn.execute(
                "UPDATE notify_queue SET status = 'delivered', delivered_ts = ?, "
                "delivered_trigger = ? WHERE id = ? AND status = 'pending'",
                (time.time(), str(trigger or "")[:40], int(row_id)),
            )
        _refresh_flag(db_path)
        return True
    except Exception as exc:
        _LOGGER.debug("notify_queue mark_delivered failed: %s", exc)
        return False


def cancel_for(target: Optional[str] = None, db_path: str = DB_PATH) -> int:
    """Cancel pending messages for ``target`` (normalised), or ALL when ``target``
    is None/blank. Returns the number cancelled. Never raises."""
    try:
        with sqlite3.connect(db_path, factory=ClosingConnection) as conn:
            if target and _normalize(target):
                cur = conn.execute(
                    "UPDATE notify_queue SET status = 'cancelled' "
                    "WHERE status = 'pending' AND target_person = ?",
                    (_normalize(target),),
                )
            else:
                cur = conn.execute(
                    "UPDATE notify_queue SET status = 'cancelled' "
                    "WHERE status = 'pending'")
            n = cur.rowcount or 0
        _refresh_flag(db_path)
        return int(n)
    except Exception as exc:
        _LOGGER.debug("notify_queue cancel_for failed: %s", exc)
        return 0


def reset_for_tests() -> None:
    """Clear the process-local pending flag (tests)."""
    global _has_pending
    _has_pending = False


# ── delivery (async) ─────────────────────────────────────────────────────────
async def _announce(hass, text: str) -> bool:
    """Speak ``text`` on the household broadcast speakers (same routing as the
    relay). Returns True only when a speaker actually received it. Never raises.

    (#595: previously called broadcast_target(hass) with no args, announced with
    tts_entity None, and returned True unconditionally — so a silent non-delivery
    looked like a success.)"""
    try:
        from . import audio_routing
        speakers, tts_entity = audio_routing.announcement_targets(hass)
    except Exception:
        speakers, tts_entity = [], None
    if not speakers or not tts_entity:
        return False
    try:
        from .tts_helper import async_announce
        return bool(await async_announce(hass, text, tts_entity, speakers,
                                         context="announcement"))
    except Exception as exc:
        _LOGGER.debug("notify_queue announce failed: %s", exc)
        return False


async def _push_owner(hass, text: str) -> bool:
    """Push ``text`` to the owner via the configured notify service. Never raises."""
    try:
        from . import jarvis_config
        svc = str(jarvis_config.get("notify_service", "") or "").strip()
    except Exception:
        svc = ""
    if not svc or "." not in svc:
        return False
    domain, name = svc.split(".", 1)
    try:
        await hass.services.async_call(
            domain, name, {"message": text, "title": "JARVIS"}, blocking=True)
        return True
    except Exception as exc:
        _LOGGER.debug("notify_queue push failed: %s", exc)
        return False


async def async_deliver_for(hass, target_display: str, trigger: str = "") -> int:
    """Deliver every pending message for ``target_display`` (the person just
    detected) and mark each delivered. Announces to the household (or pushes to the
    owner for a sighting alert). Returns the count delivered. Never raises."""
    rows = pending_for(target_display)
    if not rows:
        return 0
    delivered = 0
    for r in rows:
        try:
            line = compose_delivery_line(
                r.get("target_display") or target_display,
                r.get("message") or "",
                notify_display=r.get("notify_display"),
                from_person=r.get("from_person"),
                notify_owner=bool(r.get("notify_owner")),
                trigger=trigger,
            )
            if not line:
                mark_delivered(r.get("id"), trigger)
                continue
            ok = await (_push_owner(hass, line) if r.get("notify_owner")
                        else _announce(hass, line))
            if ok:
                mark_delivered(r.get("id"), trigger)
                delivered += 1
            # If delivery failed (no speakers / no notify service), leave the row
            # pending so a later detection re-tries rather than losing the message.
        except Exception as exc:
            _LOGGER.debug("notify_queue deliver row failed: %s", exc)
    if delivered:
        try:
            from .websocket import jarvis_log
            jarvis_log("AGENT", f"deferred notify → {target_display} "
                                f"({delivered} delivered, {trigger})")
        except Exception:
            pass
    return delivered


async def async_maybe_deliver(hass, name: str, trigger: str = "") -> int:
    """Hook entry point: deliver anything held for ``name``, cheaply skipping the
    DB entirely when nothing is queued. Safe to call from any detection path."""
    if not _has_pending or not str(name or "").strip():
        return 0
    return await async_deliver_for(hass, name, trigger)


def schedule_delivery(hass, name: str, trigger: str = "") -> None:
    """Sync-safe fire-and-forget delivery for callers in the event loop that are
    not coroutines (e.g. the recognition cache write). No-op when nothing is
    queued. Never raises."""
    if not _has_pending or hass is None or not str(name or "").strip():
        return
    try:
        hass.async_create_task(async_maybe_deliver(hass, name, trigger))
    except Exception:
        try:
            hass.add_job(async_maybe_deliver(hass, name, trigger))
        except Exception as exc:
            _LOGGER.debug("notify_queue schedule_delivery failed: %s", exc)
