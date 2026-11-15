"""Vision-based open/closed watch for sensor-less doors & windows (#530).

Many placed floor-plan openings (a shed window, an interior door) have **no
contact sensor** mapped to them, so JARVIS is blind to whether they are open or
closed. When such an opening sits in a camera's sightline, a camera already
*sees* its state — this module reads it with a throttled, one-at-a-time vision
classify and remembers the answer per opening.

Design constraints (owner-safe):

* **Ships OFF.** Nothing runs until the owner sets the ``opening_watch_enabled``
  config key true. With it off, the scheduler tick returns immediately, so
  merging this costs nothing.
* **Bounded cost.** At most ``max_per_tick`` (default 1) vision calls per tick,
  and each opening is re-checked no more often than ``opening_watch_interval_min``
  (default 30 min). "Credits aren't free" — one opening, least-recently-checked,
  per tick.
* **Fail-safe.** A missing camera, an unreadable frame, an unparseable verdict,
  or any error leaves the opening's prior state untouched and never raises.

The pure helpers (:func:`sensorless_openings`, :func:`parse_open_closed`,
:func:`due`, :func:`pick_next`) carry the logic and are unit-tested without Home
Assistant. :func:`check_openings` is the thin async driver that wires them to the
real camera vision path. The remembered states (:func:`get_states`) are what the
Home-view readout (increment 2) renders.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Ship OFF. The owner turns the watch on with the `opening_watch_enabled` config
# key; this constant is the default when the key is unset, and doubles as the
# kill-switch (a code revert to False disables the feature regardless of config).
OPENING_WATCH_ENABLED = False

# Default re-check throttle and per-tick cap (both overridable by config).
DEFAULT_INTERVAL_MIN = 30
DEFAULT_MAX_PER_TICK = 1

_OPENING_TYPES = ("door", "window")

# Remembered verdicts, keyed by opening id → record. Process-local cache; a
# reload keeps it (harmless — it is only a cache of last-seen state).
_STATES: dict = {}


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ── Pure helpers (no Home Assistant) ────────────────────────────────────────
def sensorless_openings(elements_by_floor) -> list:
    """Flatten the floor-plan elements to the door/window openings that have **no
    contact sensor** mapped (``entity`` missing/blank). Each result is
    ``{"id", "type", "room", "floor", "label"}``. Never raises."""
    out: list = []
    if not isinstance(elements_by_floor, dict):
        return out
    for floor, elems in elements_by_floor.items():
        if not isinstance(elems, list):
            continue
        for el in elems:
            if not isinstance(el, dict):
                continue
            etype = str(el.get("type") or "").strip().lower()
            if etype not in _OPENING_TYPES:
                continue
            if str(el.get("entity") or "").strip():
                continue  # has a sensor — JARVIS already knows its state
            room = str(el.get("room") or "").strip()
            eid = str(el.get("id") or "").strip()
            if not eid:
                continue
            label = (str(el.get("label") or "").strip()
                     or (f"{room} {etype}".strip() if room else etype))
            out.append({"id": eid, "type": etype, "room": room,
                        "floor": str(floor), "label": label})
    return out


def parse_open_closed(text) -> str:
    """Map a vision model's reply to ``"open"``, ``"closed"`` or ``"unknown"``.

    Keyword-based and defensive: handles a bare ``OPEN``/``CLOSED`` keyword, a
    sentence that contains one, and the negations ``not open`` / ``not closed``.
    An ambiguous or empty reply is ``"unknown"`` so a bad frame never flips a
    remembered state to a confident wrong value."""
    t = (text or "").strip().lower()
    if not t:
        return "unknown"
    # Explicit negations first ("the window is not open" → closed).
    if "not open" in t or "isn't open" in t or "is not open" in t:
        return "closed"
    if "not closed" in t or "isn't closed" in t or "is not closed" in t:
        return "open"
    closed = any(w in t for w in ("closed", "shut", "secured"))
    opened = ("ajar" in t or "wide open" in t
              or _has_word(t, "open") or _has_word(t, "opened"))
    if closed and not opened:
        return "closed"
    if opened and not closed:
        return "open"
    return "unknown"


def _has_word(text: str, word: str) -> bool:
    """Whole-word match so 'opening'/'closet' don't masquerade as open/closed."""
    import re
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


def due(last_ts, now, interval_s) -> bool:
    """True when an opening last checked at ``last_ts`` is due again at ``now``.
    A never-checked opening (``last_ts`` falsy/0) is always due."""
    try:
        if not last_ts:
            return True
        return (float(now) - float(last_ts)) >= float(interval_s)
    except Exception:
        return True


def pick_next(openings, states, now, interval_s) -> Optional[dict]:
    """Choose the single opening most worth checking this tick: among those due
    (per :func:`due`), the one checked longest ago (never-checked first). Returns
    ``None`` when nothing is due, so a tick with everything fresh does no work."""
    best = None
    best_ts = None
    for op in openings or []:
        rec = (states or {}).get(op.get("id")) or {}
        last_ts = rec.get("ts") or 0
        if not due(last_ts, now, interval_s):
            continue
        if best is None or last_ts < best_ts:
            best, best_ts = op, last_ts
    return best


# ── State access ────────────────────────────────────────────────────────────
def get_states() -> dict:
    """A copy of the remembered per-opening verdicts, for diagnostics / the
    Home-view readout. Keyed by opening id."""
    return {k: dict(v) for k, v in _STATES.items()}


def clear() -> None:
    """Drop all remembered verdicts (test / reset helper)."""
    _STATES.clear()


def _record(op: dict, state: str, camera: str, now: float) -> bool:
    """Store a verdict for one opening. Returns True when the open/closed state
    changed from what was remembered (for change logging)."""
    eid = op.get("id")
    prev = (_STATES.get(eid) or {}).get("state")
    _STATES[eid] = {
        "id": eid, "type": op.get("type"), "room": op.get("room"),
        "floor": op.get("floor"), "label": op.get("label"),
        "state": state, "camera": camera, "ts": now,
    }
    return state in ("open", "closed") and prev not in (None, state)


# ── Config / gating ─────────────────────────────────────────────────────────
def _watch_on(hass) -> bool:
    """Owner gate: on only when the `opening_watch_enabled` config key is true.
    Defaults to the module constant (OFF). The constant staying False is the
    kill-switch — a code revert disables the feature whatever config says."""
    if not OPENING_WATCH_ENABLED:
        try:
            from . import jarvis_config
            return _as_bool(jarvis_config.get("opening_watch_enabled", False))
        except Exception:
            return False
    return True


def _interval_s(hass) -> float:
    try:
        from . import jarvis_config
        mins = jarvis_config.get("opening_watch_interval_min", DEFAULT_INTERVAL_MIN)
        return max(60.0, float(mins) * 60.0)
    except Exception:
        return DEFAULT_INTERVAL_MIN * 60.0


def _elements(hass) -> dict:
    try:
        from . import jarvis_config
        raw = jarvis_config.get("floor_plan_elements", {})
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


# ── Async driver ────────────────────────────────────────────────────────────
async def check_openings(hass, *, now=None, max_per_tick: int = DEFAULT_MAX_PER_TICK) -> dict:
    """One throttled sweep: pick the least-recently-checked sensor-less opening
    that sits in a camera's sightline and is due, read its open/closed state with
    one vision classify, and remember it. Returns a small summary dict. Never
    raises — every failure mode degrades to 'skipped' and leaves state intact."""
    if not _watch_on(hass):
        return {"enabled": False, "checked": 0}
    now = float(now if now is not None else time.time())
    interval = _interval_s(hass)

    openings = sensorless_openings(_elements(hass))
    if not openings:
        return {"enabled": True, "checked": 0, "reason": "no sensor-less openings"}

    # Only openings whose room resolves to a covering camera are checkable.
    from . import camera_coverage
    checkable = []
    for op in openings:
        room = op.get("room")
        if not room:
            continue  # exterior door with no HA area — nothing to aim a camera at
        cam = camera_coverage.camera_for_area(hass, room)
        if cam:
            op = dict(op, camera=cam)
            checkable.append(op)
    if not checkable:
        return {"enabled": True, "checked": 0, "reason": "no camera sightline"}

    checked = 0
    results = []
    for _ in range(max(1, int(max_per_tick))):
        op = pick_next(checkable, _STATES, now, interval)
        if op is None:
            break
        verdict = await _classify_opening(hass, op)
        if verdict is None:
            # Unreadable this tick — stamp ts so we don't hammer the same one,
            # but keep any prior open/closed state.
            prev = (_STATES.get(op["id"]) or {}).get("state", "unknown")
            _record(op, prev if prev in ("open", "closed") else "unknown",
                    op.get("camera", ""), now)
        else:
            changed = _record(op, verdict, op.get("camera", ""), now)
            if changed:
                _log_change(op, verdict)
        checked += 1
        results.append({"id": op["id"], "state": verdict or "unreadable"})
        # Exclude the one just handled so a multi-check tick moves on.
        checkable = [c for c in checkable if c["id"] != op["id"]]

    return {"enabled": True, "checked": checked, "results": results}


async def _classify_opening(hass, op: dict) -> Optional[str]:
    """Grab one frame from the opening's covering camera and classify open/closed.
    Returns ``"open"``/``"closed"``/``"unknown"`` from the model, or ``None`` when
    the frame or client was unavailable (caller keeps prior state)."""
    import base64
    try:
        from . import camera as cam_mod
    except Exception:
        return None
    camera_entity = op.get("camera")
    if not camera_entity:
        return None
    try:
        frame = await cam_mod._get_best_image(hass, camera_entity)
    except Exception as exc:
        _LOGGER.debug("opening_watch: snapshot failed for %s: %s", camera_entity, exc)
        return None
    if not frame:
        return None

    provider = cam_mod._cfg_opt(hass, "vision_provider", "groq") or "groq"
    model = cam_mod._cfg_opt(hass, "vision_model", getattr(cam_mod, "VISION_MODEL", "")) \
        or getattr(cam_mod, "VISION_MODEL", "")
    thinking = bool(cam_mod._cfg_opt(hass, "vision_thinking_enabled", False))
    client = await cam_mod.async_make_client(hass, provider, model, None)
    if client is None:
        _LOGGER.debug("opening_watch: no vision client (provider=%s) — skipping", provider)
        return None

    kind = op.get("type", "opening")
    room = op.get("room") or "this room"
    question = (
        f"Look only at the {kind} in the {room} that this camera can see. "
        f"Is that {kind} OPEN or CLOSED right now? Answer with exactly one word: "
        f"OPEN, CLOSED, or UNKNOWN if you genuinely cannot tell."
    )
    try:
        suffix = "" if thinking else cam_mod._no_think_suffix(model)
    except Exception:
        suffix = ""
    b64 = base64.b64encode(frame).decode()
    messages = [
        {"role": "system",
         "content": "You are a precise visual inspector. Report only what is "
                    "clearly visible; if unsure, say UNKNOWN."},
        {"role": "user",
         "content": [
             {"type": "image_url",
              "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
             {"type": "text", "text": question + suffix},
         ]},
    ]
    try:
        text = await cam_mod._vision_describe(
            hass, client, messages, model, thinking,
            camera_name=camera_entity)
    except Exception as exc:
        _LOGGER.debug("opening_watch: vision classify failed for %s: %s",
                      op.get("id"), exc)
        return None
    return parse_open_closed(text)


def _log_change(op: dict, state: str) -> None:
    try:
        from .websocket import jarvis_log
        where = op.get("room") or op.get("floor") or ""
        jarvis_log("OPENINGS",
                   f"{op.get('label') or op.get('type')}"
                   f"{(' (' + where + ')') if where else ''} is now {state} "
                   f"(seen via {op.get('camera', 'camera')})")
    except Exception:
        pass
