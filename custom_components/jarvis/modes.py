"""
JARVIS Directive Layer — operational modes (v6.61.0).

The generalization of the Lockdown state machine. Instead of a single formal
state, JARVIS supports named operational modes that each shift its whole
behavior profile at once — what it announces, how much it auto-does, how chatty
the persona is, and which outdoor/indoor events are worth surfacing.

Design, mirroring the proven lockdown state pattern:
  - One active mode at a time (default: "normal"), persisted atomically to
    /config/jarvis/mode_state.json so it survives reboots/reloads.
  - Modes are declared in a registry as behavior *overrides* — a mode only
    states what it changes; everything else falls back to normal config. This
    keeps modes decoupled: subsystems CONSULT the active mode (active_mode(),
    mode_overrides()) rather than modes reaching into subsystems.
  - Lockdown is deliberately NOT one of these: it's a safety state that owns
    securing the house and must not be overridden by a casual mode switch. A
    mode never suppresses safety; mode_allows_proactive() only affects the
    proactive/convenience layer, never SafetyManager.

Built-in modes ship as sensible defaults; users can override any field or add
their own via the `custom_modes` config key. Nothing here raises to the caller.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

# Guards WRITERS only (set_mode()/auto_evaluate(), run on executor threads from
# agent.py, websocket.py, and the cognitive tick). Reentrant so auto_evaluate()
# can hold it across its read-decide-write instead of releasing it between the
# decision and the set_mode() call that acts on it — otherwise a manual
# set_mode() could land in that gap and get clobbered by a stale auto decision.
# Readers never take this lock (see _state below) so a synchronous read on the
# event loop can never block on the executor thread's file I/O in _persist().
_state_lock = threading.RLock()

MODE_STATE_PATH = config_path_str("jarvis", "mode_state.json")
DEFAULT_MODE = "normal"

# Behavior fields a mode may override. A mode dict need only include the fields
# it changes; missing fields fall through to normal behavior / live config.
#   proactive:     bool  — allow the proactive/convenience layer to speak/act
#   banter:        int   — persona banter level 0/1/2 for this mode (None = keep)
#   announce_scope:str   — which events surface: "all" | "notable" | "critical"
#   auto_actions:  bool  — allow autonomy graduations to auto-execute
#   quiet:         bool   — treat as quiet-hours-like (suppress non-critical)
#   description:   str    — human summary
_BUILTIN_MODES: dict[str, dict] = {
    "normal": {
        "description": "Default operation — balanced proactivity and voice.",
        "proactive": True, "auto_actions": True,
        "announce_scope": "notable", "quiet": False,
    },
    "party": {
        "description": "Guests over — relax nagging, keep it light, only "
                       "surface genuinely notable things.",
        "proactive": False, "banter": 2, "announce_scope": "critical",
        "auto_actions": False, "quiet": False,
    },
    "lab": {
        "description": "Focused workshop/lab work — minimal interruptions, dry "
                       "tone, safety still fully active.",
        "proactive": False, "banter": 1, "announce_scope": "critical",
        "auto_actions": False, "quiet": True,
    },
    "movie": {
        "description": "Movie/theater mode — near-silent, only critical alerts, "
                       "no convenience chatter.",
        "proactive": False, "banter": 0, "announce_scope": "critical",
        "auto_actions": False, "quiet": True,
    },
    "guest": {
        "description": "Houseguest staying — softer autonomy, plainer voice, "
                       "avoid acting on unfamiliar patterns.",
        "proactive": True, "banter": 1, "announce_scope": "notable",
        "auto_actions": False, "quiet": False,
    },
    "away": {
        "description": "Household away — proactive convenience off, security "
                       "posture emphasized, only notable/critical events.",
        "proactive": False, "announce_scope": "notable",
        "auto_actions": True, "quiet": False,
    },
    "focus": {
        "description": "Deep-focus/work — hold non-critical interrupts, terse "
                       "tone.",
        "proactive": False, "banter": 1, "announce_scope": "critical",
        "auto_actions": True, "quiet": True,
    },
}

# In-memory state (source of truth at runtime; mirrored to disk). Treated as
# immutable: writers always build a whole new dict and swap the module-level
# reference under _state_lock rather than mutating keys in place, so readers
# can take a lock-free snapshot (dict reference reads/writes are atomic under
# the GIL) and never block behind a writer's file I/O.
_state = {"mode": DEFAULT_MODE, "since": 0.0, "reason": ""}
_loaded = False


def _cfg(key: str, default=None):
    try:
        from . import jarvis_config
        v = jarvis_config.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


def _all_modes() -> dict[str, dict]:
    """Built-in modes merged with any user-defined custom_modes (custom wins)."""
    modes = dict(_BUILTIN_MODES)
    custom = _cfg("custom_modes", {})
    if isinstance(custom, dict):
        for name, spec in custom.items():
            if isinstance(spec, dict):
                key = str(name).strip().lower()
                if key:
                    modes[key] = {**modes.get(key, {}), **spec}
    return modes


# ── persistence (atomic, reboot-safe — mirrors lockdown) ─────────────────────

def _load() -> None:
    # Double-checked lock: the common case (already loaded) is a lock-free
    # boolean read so callers on the event loop never wait on a writer.
    global _loaded, _state
    if _loaded:
        return
    with _state_lock:
        if _loaded:
            return
        try:
            if os.path.exists(MODE_STATE_PATH):
                with open(MODE_STATE_PATH) as f:
                    d = json.load(f)
                mode = str(d.get("mode", DEFAULT_MODE)).lower()
                if mode in _all_modes():
                    _state = {
                        "mode": mode,
                        "since": float(d.get("since", time.time())),
                        "reason": str(d.get("reason", "restored")),
                    }
                    if mode != DEFAULT_MODE:
                        _LOGGER.info("JARVIS mode RESTORED: %s", mode)
        except Exception as exc:
            _LOGGER.warning("mode state restore failed: %s", exc)
        _loaded = True


def _persist(snapshot: dict) -> None:
    # Called with _state_lock held by the writer; takes an explicit snapshot
    # rather than reading the global so it never races a subsequent swap.
    try:
        os.makedirs(os.path.dirname(MODE_STATE_PATH), exist_ok=True)
        tmp = MODE_STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snapshot, f)
        os.replace(tmp, MODE_STATE_PATH)
    except Exception as exc:
        _LOGGER.debug("mode state persist failed: %s", exc)


# ── public API (consulted by subsystems) ─────────────────────────────────────

def active_mode() -> str:
    """The currently active mode name (always valid; 'normal' if unset)."""
    _load()
    return _state.get("mode", DEFAULT_MODE)  # lock-free: single atomic dict read


def mode_info() -> dict:
    """Full status for the panel/agent: active mode, since, reason, and the
    resolved override profile."""
    _load()
    snapshot = _state  # local ref: immune to a concurrent writer's swap
    name = snapshot.get("mode", DEFAULT_MODE)
    since = snapshot.get("since", 0.0)
    reason = snapshot.get("reason", "")
    modes = _all_modes()
    spec = modes.get(name, modes[DEFAULT_MODE])
    return {
        "active": name,
        "since": since,
        "reason": reason,
        "description": spec.get("description", ""),
        "overrides": _resolve(name),
        "available": [
            {"name": n, "description": s.get("description", "")}
            for n, s in sorted(modes.items())
        ],
    }


def _resolve(name: str) -> dict:
    """Resolve a mode's effective behavior, falling back to normal for any
    field the mode doesn't override."""
    modes = _all_modes()
    base = dict(modes.get(DEFAULT_MODE, {}))
    spec = modes.get(name, {})
    base.update(spec)
    return {
        "proactive": bool(base.get("proactive", True)),
        "banter": base.get("banter", None),
        "announce_scope": base.get("announce_scope", "notable"),
        "auto_actions": bool(base.get("auto_actions", True)),
        "quiet": bool(base.get("quiet", False)),
    }


def mode_overrides() -> dict:
    """The active mode's resolved behavior profile."""
    return _resolve(active_mode())


def mode_allows_proactive() -> bool:
    """Whether the active mode permits the proactive/convenience layer to speak
    or act. NEVER gates safety — SafetyManager runs regardless of mode."""
    return mode_overrides().get("proactive", True)


def mode_allows_auto_actions() -> bool:
    """Whether autonomy graduations may auto-execute under the active mode."""
    return mode_overrides().get("auto_actions", True)


def mode_announce_scope() -> str:
    """'all' | 'notable' | 'critical' — how selective event surfacing is."""
    return mode_overrides().get("announce_scope", "notable")


def mode_banter_level() -> Optional[int]:
    """Persona banter level the mode wants (0/1/2), or None to keep the user's
    configured level."""
    return mode_overrides().get("banter", None)


def set_mode(name: str, reason: str = "") -> dict:
    """Activate a mode. Returns {ok, mode, error?}. Unknown names are rejected
    with the list of valid modes. Never raises."""
    _load()
    key = str(name or "").strip().lower()
    modes = _all_modes()
    if key not in modes:
        return {"ok": False, "error": f"unknown mode '{name}'",
                "available": sorted(modes.keys())}
    global _state
    with _state_lock:
        new_state = {"mode": key, "since": time.time(), "reason": str(reason or "")}
        _state = new_state  # atomic swap: concurrent readers see old or new, never partial
        _persist(new_state)
    _LOGGER.info("JARVIS mode → %s%s", key, f" ({reason})" if reason else "")
    return {"ok": True, "mode": key, "overrides": _resolve(key)}


def clear_mode(reason: str = "") -> dict:
    """Return to normal mode."""
    return set_mode(DEFAULT_MODE, reason or "cleared")


# ── automatic mode (v7.14.0): track occupancy unless the user goes hands-on ──

def auto_enabled() -> bool:
    """Whether JARVIS may switch operational mode on its own. Users who prefer a
    hands-on approach (setting modes only via HMI/voice) turn this off; the
    discretionary modes stay fully manual either way."""
    v = _cfg("operational_mode_auto", True)
    return True if v is None else bool(v)


def auto_evaluate(occupied: bool) -> Optional[dict]:
    """When auto-mode is on, keep AWAY/NORMAL tracking real occupancy. This only
    ever toggles between 'away' and 'normal': a deliberately set discretionary
    mode (party/movie/lab/guest/focus) is left untouched while someone is home,
    and is superseded by 'away' only once the home goes empty; returning home
    from 'away' lands in 'normal'. Returns the set_mode result if it changed the
    mode, else None. Never raises — auto-mode must never break the tick."""
    try:
        if not auto_enabled():
            return None
        # Hold the writer lock across read-decide-write (set_mode reacquires it
        # reentrantly) so a concurrent manual set_mode() can't land in the gap
        # and get clobbered by a now-stale auto decision.
        with _state_lock:
            _load()
            cur = _state.get("mode", DEFAULT_MODE)
            if not occupied and cur != "away":
                return set_mode("away", "auto: home empty")
            if occupied and cur == "away":
                return set_mode(DEFAULT_MODE, "auto: someone home")
    except Exception as exc:
        _LOGGER.debug("auto_evaluate failed: %s", exc)
    return None


# ── room-scoped modes (v7.15.0): a mode can apply only in bound room(s) ───────

def mode_scoped_to_areas() -> list:
    """Areas the *active* mode is bound to, if any. Lab and Movie can be scoped
    to specific room(s) so their 'minimal interruptions' applies only there and
    the rest of the house stays normal. Empty list = whole-house (unconfigured,
    or a mode with no area binding)."""
    try:
        m = active_mode()
        if m == "lab":
            v = _cfg("lab_areas", []) or []
            return [str(a) for a in v if a] if isinstance(v, list) else []
        if m == "movie":
            a = _cfg("movie_area", "") or ""
            return [str(a)] if a else []
    except Exception:
        pass
    return []


def mode_active_here(area_id: str) -> bool:
    """Whether the active mode's behavior applies in this area. A room-scoped
    mode (Lab/Movie bound to rooms) applies only in its bound rooms; any other
    mode applies everywhere."""
    areas = mode_scoped_to_areas()
    return True if not areas else (str(area_id) in areas)
