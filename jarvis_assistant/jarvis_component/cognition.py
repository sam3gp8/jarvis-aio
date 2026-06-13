"""
JARVIS local cognition layer.

A zero-cloud-cost reasoning stage that sees EVERY state change, maintains a
lightweight per-entity model of the home's normal rhythm, and decides which
events are salient enough to escalate to a cloud LLM.

It is strictly ADDITIVE to the observer's existing static pre-filter: it never
suppresses anything the static filter already escalates (no regression). It
only *adds* escalations for genuine anomalies the static filter would miss —
e.g. a numeric sensor spiking far outside its learned range, or a device
changing at an hour it normally never does. The user-configurable hourly cap
(`classifier_rate_limit`) remains the hard backstop on cloud cost.

This module is also the substrate for future ANTICIPATION: the per-entity model
(change frequency, value range, time-of-day histogram) is exactly what a
predictor will read to forecast "this usually happens by now" events. The
salience scorer is deliberately swappable behind decide() — today it is
heuristics; once a local Ollama model is available it can back the same call
with no change to the observer pipeline.
"""

from __future__ import annotations

import datetime
import logging
import math
import time
from collections import deque, namedtuple

_LOGGER = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────
MODEL_MAX_ENTITIES = 1500       # bound memory; evict least-recently-changed
TRANSITION_WINDOW = 50          # keep last N transition timestamps per entity
RARE_TRANSITIONS_PER_DAY = 3.0  # ≤ this many/day (avg) ⇒ a "rare" change
NUMERIC_MIN_SAMPLES = 30        # need this many readings before trusting stats
Z_SPIKE = 4.0                   # |z| ≥ this ⇒ strong anomaly
Z_DRIFT = 3.0                   # |z| ≥ this ⇒ mild anomaly
DEFAULT_THRESHOLD = 0.6         # salience ≥ this ⇒ escalate (anomaly path)

SAFETY_CLASSES = {
    "smoke", "gas", "carbon_monoxide", "moisture", "safety", "tamper",
}
# device_class "problem" is a DIAGNOSTIC, not life-safety — a stale backup, a
# failed update check, an addon complaint. It gets its own moderate tier below,
# and system-maintenance entities are damped to informational so housekeeping
# flags never masquerade as security events.
_MAINTENANCE_HINTS = (
    "backup", "stale", "update", "snapshot", "certificat", "reboot",
    "restart_required", "hacs", "supervisor", "firmware", "addon", "config_entry",
)
SECURITY_DOMAINS = {"lock", "alarm_control_panel"}
OPENING_CLASSES = {"door", "window", "garage_door", "opening"}
# A safety binary_sensor is an emergency only in its ACTIVE state ("on"); an
# alarm panel only when "triggered"/"pending". Going unavailable/unknown or
# returning to normal (off/dry/clear) is NEVER a trigger.
_ALARM_ACTIVE = {"triggered", "pending"}

Decision = namedtuple("Decision", ["escalate", "salience", "reason"])

# ── Anticipation: occupancy / expected-state-by-hour ─────────────────────────
OCCUPANCY_DOMAINS = {"lock", "cover", "alarm_control_panel"}
OCCUPANCY_BINARY_CLASSES = {"door", "window", "garage_door", "opening", "garage", "lock"}
OCC_SAMPLE_INTERVAL = 900       # seconds between occupancy samples (≈15 min)
OCC_MIN_SAMPLES_PER_HOUR = 20   # need ~5 days of samples before trusting an hour
OCC_UNUSUAL_SHARE = 0.20        # current state < this share at this hour ⇒ unusual
OCC_MIN_STILLNESS = 600         # entity must have held the state ≥ 10 min (not transient)
PREDICT_COOLDOWN = 3600         # re-announce the same entity at most hourly
_DEAD_STATES = {"unknown", "unavailable", "none", "", None}

# Recurring daily-event ("usually happened by now") detection
RECUR_DOMAINS = {"binary_sensor", "lock", "cover", "alarm_control_panel"}
RECUR_MIN_DAYS = 7              # need at least a week of daily occurrences
RECUR_MAX_STD = 2700            # ≤45-min spread ⇒ a consistent daily routine
RECUR_TOL_MIN = 1800            # overdue grace floor (30 min past usual time)
RECUR_TOL_MAX = 3600            # overdue grace cap (60 min)

# Presence routines (person / device_tracker)
PRESENCE_DOMAINS = {"person", "device_tracker"}
PRESENCE_DEBOUNCE = 300         # router/bluetooth trackers must hold ≥5 min
                                # (debounces Wi-Fi flap that plagues LAN trackers)
PRESENCE_DEBOUNCE_GPS = 60      # GPS/location trackers report reliably off-network,
                                # so they're trusted far faster than network trackers
APPROACH_OUTER_KM = 5.0         # within this of home AND closing ⇒ "heading home"
APPROACH_MIN_CLOSE_KM = 0.05    # must be ≥50 m closer than last reading to count

_PREDICT_COOLDOWNS: dict = {}
_RECUR_ALERTED: dict = {}       # entity_id -> local-day-ordinal last alerted
_LAST_DIST: dict = {}           # entity_id -> last distance-to-home (km)
_APPROACH_ALERTED: dict = {}    # entity_id -> already flagged this approach


class _Entry:
    """Lightweight per-entity model. Bounded memory."""
    __slots__ = (
        "last_state", "last_changed", "first_seen", "transitions",
        "n", "mean", "m2", "hours", "occ", "daily_first", "last_first_day",
        "depart_first", "return_first", "pres_depart_day", "pres_return_day",
    )

    def __init__(self, now: float):
        self.last_state = ""
        self.last_changed = now
        self.first_seen = now
        self.transitions: deque = deque(maxlen=TRANSITION_WINDOW)
        # Welford online numeric stats
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        # change count per hour-of-day (0..23)
        self.hours = [0] * 24
        # occupancy: per-hour {state: count} — "what state is normal at hour H"
        self.occ: dict = {}
        # daily routine: seconds-since-local-midnight of the FIRST change each
        # day (one per day), for "this usually happens by now" detection.
        self.daily_first: deque = deque(maxlen=45)
        self.last_first_day = 0
        # presence routines (person/device_tracker): first DEBOUNCED departure
        # and latest DEBOUNCED arrival each day, for "usually out/home by now".
        self.depart_first: deque = deque(maxlen=45)
        self.return_first: deque = deque(maxlen=45)
        self.pres_depart_day = 0
        self.pres_return_day = 0


# ── Module state ─────────────────────────────────────────────────────────────
_MODEL: dict[str, _Entry] = {}
_EVENTS_SEEN = 0
_ANOMALIES_ESCALATED = 0


def reset() -> None:
    """Clear the learned model (called on observer restart)."""
    global _EVENTS_SEEN, _ANOMALIES_ESCALATED
    _MODEL.clear()
    _PREDICT_COOLDOWNS.clear()
    _RECUR_ALERTED.clear()
    _LAST_DIST.clear()
    _APPROACH_ALERTED.clear()
    _EVENTS_SEEN = 0
    _ANOMALIES_ESCALATED = 0


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _local_day(now: float) -> int:
    # Proleptic Gregorian ordinal in LOCAL time: monotonic (good for spans) and
    # unique per calendar day (good for equality).
    return datetime.datetime.fromtimestamp(now).toordinal()


def _secs_since_midnight(now: float) -> int:
    lt = time.localtime(now)
    return lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec


def _evict_if_full() -> None:
    if len(_MODEL) < MODEL_MAX_ENTITIES:
        return
    # Evict the least-recently-changed entity (cheap, rare).
    oldest_key = min(_MODEL, key=lambda k: _MODEL[k].last_changed)
    _MODEL.pop(oldest_key, None)


def observe(entity_id: str, old_state, new_value, now: float) -> _Entry:
    """
    Update the per-entity model from a state change. Called for EVERY event,
    including telemetry the static filter drops — this is the learning surface.
    Returns the entry (post-update).
    """
    entry = _MODEL.get(entity_id)
    if entry is None:
        _evict_if_full()
        entry = _Entry(now)
        _MODEL[entity_id] = entry

    # Transition bookkeeping (only count genuine value changes)
    if new_value != entry.last_state:
        entry.transitions.append(now)
        lt = time.localtime(now)
        entry.hours[lt.tm_hour] += 1
        entry.last_changed = now
        # First change of the day → record (day, time-of-day) for routine detection
        day_ord = _local_day(now)
        if entry.last_first_day != day_ord:
            entry.last_first_day = day_ord
            entry.daily_first.append((day_ord, _secs_since_midnight(now)))
    entry.last_state = new_value if new_value is not None else entry.last_state

    # Numeric running stats (Welford) — only for parseable numbers
    val = _to_float(new_value)
    if val is not None:
        entry.n += 1
        delta = val - entry.mean
        entry.mean += delta / entry.n
        entry.m2 += delta * (val - entry.mean)

    return entry


def _is_rare(entry: _Entry, now: float) -> bool:
    """True if this entity changes infrequently (avg ≤ RARE/day over its life)."""
    if len(entry.transitions) < 2:
        return False
    span = max(now - entry.first_seen, 1.0)
    per_day = len(entry.transitions) / (span / 86400.0)
    return per_day <= RARE_TRANSITIONS_PER_DAY


def _is_unusual_hour(entry: _Entry, now: float) -> bool:
    """True if this hour is one the entity rarely changes in (and we have history)."""
    total = sum(entry.hours)
    if total < 20:  # not enough history to judge
        return False
    hour = time.localtime(now).tm_hour
    share = entry.hours[hour] / total
    return share < 0.02  # this hour accounts for <2% of all its changes


def _is_safety_trigger(domain, dclass, new_value) -> bool:
    """
    True only when a safety/security sensor ENTERS its active/emergency state.
    Unavailability, unknown, or a return to normal (off/dry/clear) is never a
    trigger — connectivity blips must not be read as the sensor going off.
    """
    v = str(new_value).lower() if new_value is not None else ""
    if v in ("", "unavailable", "unknown", "none"):
        return False
    if dclass in SAFETY_CLASSES:
        return v == "on"
    if domain == "alarm_control_panel":
        return v in _ALARM_ACTIVE
    return False


def _salience(domain, dclass, new_value, entry, now, entity_id="") -> tuple:
    """Local heuristic salience in [0, 1] plus a short human reason."""
    score = 0.0
    reasons = []

    eid = (entity_id or "").lower()
    maintenance = any(h in eid for h in _MAINTENANCE_HINTS)

    if _is_safety_trigger(domain, dclass, new_value):
        score = max(score, 0.9); reasons.append("safety/security trigger")
    elif dclass == "problem" and str(new_value).lower() == "on":
        if maintenance:
            score = max(score, 0.15); reasons.append("system maintenance — informational")
        else:
            score = max(score, 0.4); reasons.append("device problem reported")
    elif domain == "cover" or dclass in OPENING_CLASSES or domain in SECURITY_DOMAINS:
        score = max(score, 0.55); reasons.append("access-point")

    # Numeric anomaly — the core value-add (catches spikes the static filter drops)
    val = _to_float(new_value)
    if val is not None and entry.n >= NUMERIC_MIN_SAMPLES:
        variance = entry.m2 / entry.n if entry.n else 0.0
        std = variance ** 0.5
        if std > 1e-9:
            z = abs(val - entry.mean) / std
            if z >= Z_SPIKE:
                score = max(score, 0.7); reasons.append(f"value spike (z={z:.1f})")
            elif z >= Z_DRIFT:
                score = max(score, 0.55); reasons.append(f"value drift (z={z:.1f})")

    if _is_rare(entry, now):
        score = min(1.0, score + 0.15); reasons.append("rare transition")
    if _is_unusual_hour(entry, now):
        score = min(1.0, score + 0.10); reasons.append("unusual hour")

    return score, ", ".join(reasons) if reasons else "routine"


def process(event, threshold: float = DEFAULT_THRESHOLD) -> Decision:
    """
    Observe an event into the model and decide whether it is salient enough to
    escalate to the cloud LLM. Safe to call on every state change.

    The returned Decision.escalate is the *anomaly* signal — the observer ORs it
    with the static pre-filter, so escalate=True here only ever ADDS coverage.
    """
    global _EVENTS_SEEN, _ANOMALIES_ESCALATED
    _EVENTS_SEEN += 1

    try:
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        new_value = new_state.state if new_state else None
        dclass = new_state.attributes.get("device_class") if new_state else None
        domain = entity_id.split(".", 1)[0] if entity_id else ""
        now = time.time()

        entry = observe(entity_id, old_state, new_value, now)
        score, reason = _salience(domain, dclass, new_value, entry, now, entity_id)
        escalate = score >= threshold
        if escalate:
            _ANOMALIES_ESCALATED += 1
        return Decision(escalate=escalate, salience=round(score, 3), reason=reason)
    except Exception as exc:  # never let cognition break the observer
        _LOGGER.debug("cognition.process error: %s", exc)
        return Decision(escalate=False, salience=0.0, reason="error")


def stats() -> dict:
    """Snapshot for the panel / observability, incl. learning progress."""
    predictable = 0
    routines = 0
    presence_routines = 0
    for eid, e in _MODEL.items():
        if any(sum(b.values()) >= OCC_MIN_SAMPLES_PER_HOUR for b in e.occ.values()):
            predictable += 1
        if eid.split(".", 1)[0] in PRESENCE_DOMAINS:
            if _routine_of_pts(list(e.depart_first)):
                presence_routines += 1
            if _routine_of_pts(list(e.return_first)):
                presence_routines += 1
        elif _routine_of(e) is not None:
            routines += 1
    return {
        "entities_tracked": len(_MODEL),
        "events_seen": _EVENTS_SEEN,
        "anomalies_escalated": _ANOMALIES_ESCALATED,
        "predictable": predictable,
        "routines": routines,
        "presence_routines": presence_routines,
    }


# ── Anticipation ─────────────────────────────────────────────────────────────
def _humanize(state, dclass) -> str:
    """Render a state for speech (door 'on'→open, lock stays locked/unlocked)."""
    s = str(state)
    if dclass in ("door", "window", "garage_door", "opening", "garage"):
        return {"on": "open", "off": "closed"}.get(s, s)
    return s.replace("_", " ")


def _occupancy_candidates(hass):
    """Discrete-state entities whose 'normal state by hour' is meaningful."""
    out = []
    states = hass.states
    for dom in OCCUPANCY_DOMAINS:
        try:
            out.extend(states.async_all(dom))
        except Exception:
            pass
    try:
        for st in states.async_all("binary_sensor"):
            if st.attributes.get("device_class") in OCCUPANCY_BINARY_CLASSES:
                out.append(st)
    except Exception:
        pass
    return out


def sample_occupancy(hass, now: float = None) -> int:
    """
    Sample the current state of occupancy-relevant entities into the per-hour
    occupancy model. Called periodically (≈every 15 min) — over days this learns
    'the garage is closed at this hour 95% of the time'. Returns count sampled.
    """
    now = now or time.time()
    hour = time.localtime(now).tm_hour
    sampled = 0
    try:
        for st in _occupancy_candidates(hass):
            sval = st.state
            if sval in _DEAD_STATES:
                continue
            eid = st.entity_id
            entry = _MODEL.get(eid)
            if entry is None:
                _evict_if_full()
                entry = _Entry(now)
                entry.last_state = sval
                _MODEL[eid] = entry
            bucket = entry.occ.setdefault(hour, {})
            bucket[sval] = bucket.get(sval, 0) + 1
            if bucket[sval] > 50000:  # decay guard
                for k in list(bucket):
                    bucket[k] //= 2
            sampled += 1
    except Exception as exc:
        _LOGGER.debug("sample_occupancy error: %s", exc)
    return sampled


def predict(hass, now: float = None) -> list:
    """
    Flag entities currently in a state that's unusual for this hour, held long
    enough to not be a transient. Returns action dicts (same shape the proactive
    managers use) for the cognitive-core tick to announce through the gated path.
    """
    now = now or time.time()
    hour = time.localtime(now).tm_hour
    out = []
    try:
        for eid, entry in list(_MODEL.items()):
            bucket = entry.occ.get(hour)
            if not bucket:
                continue
            total = sum(bucket.values())
            if total < OCC_MIN_SAMPLES_PER_HOUR:
                continue
            st = hass.states.get(eid)
            if st is None or st.state in _DEAD_STATES:
                continue
            cur = st.state
            share = bucket.get(cur, 0) / total
            if share >= OCC_UNUSUAL_SHARE:
                continue  # normal for this hour
            if (now - entry.last_changed) < OCC_MIN_STILLNESS:
                continue  # transient — let it settle
            if (now - _PREDICT_COOLDOWNS.get(eid, 0.0)) < PREDICT_COOLDOWN:
                continue
            dominant = max(bucket, key=bucket.get)
            if dominant == cur:
                continue
            _PREDICT_COOLDOWNS[eid] = now
            dclass = st.attributes.get("device_class")
            name = st.attributes.get("friendly_name", eid)
            domain = eid.split(".", 1)[0]
            urgency = "medium" if domain in ("lock", "alarm_control_panel", "cover") else "low"
            msg = (
                f"{name} is {_humanize(cur, dclass)}. Around this time it's "
                f"usually {_humanize(dominant, dclass)}, so I thought it worth mentioning."
            )
            out.append({
                "type": "anticipation",
                "urgency": urgency,
                "message": msg,
                "pattern_key": f"anticipate:{eid}",
                "offer": False,
            })
    except Exception as exc:
        _LOGGER.debug("predict error: %s", exc)
    return out


def _routine_of_pts(pts):
    """
    Core routine test on a list of (day_ord, secs) points: enough days, low
    time-of-day spread, and occurring on most days in its span. Returns
    (mean_secs, std_secs) or None.
    """
    if len(pts) < RECUR_MIN_DAYS:
        return None
    secs = [s for (_d, s) in pts]
    m = sum(secs) / len(secs)
    std = (sum((x - m) ** 2 for x in secs) / len(secs)) ** 0.5
    if std > RECUR_MAX_STD:
        return None
    days = [d for (d, _s) in pts]
    span = (max(days) - min(days)) + 1
    if span <= 0 or (len(pts) / span) < 0.6:
        return None
    return m, std


def _routine_of(entry: _Entry):
    """
    Return (mean_secs, std_secs) if this entity has a CONSISTENT, near-daily
    routine — enough days, low time-of-day spread, and occurring on most days in
    its observed span. Otherwise None. This is what makes "usually by now" safe:
    it won't fire for events that merely happen at a consistent time but rarely.
    """
    return _routine_of_pts(list(entry.daily_first))


def _hhmm(secs: float) -> str:
    return f"{int(secs // 3600):02d}:{int((secs % 3600) // 60):02d}"


def _is_home(state) -> bool:
    return state == "home"


def _is_away(state) -> bool:
    return state not in _DEAD_STATES and state != "home"


def _last_changed_ts(st):
    try:
        return st.last_changed.timestamp()
    except Exception:
        return None


def _source_type(st) -> str:
    return (st.attributes.get("source_type") or "").lower()


def _entity_coords(st):
    lat = st.attributes.get("latitude")
    lon = st.attributes.get("longitude")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _is_gps(st) -> bool:
    # A tracker is location-aware (works off-network) if HA marks it gps OR it
    # exposes coordinates — covers person entities backed by a GPS tracker.
    return _source_type(st) == "gps" or _entity_coords(st) is not None


def _debounce_for(st) -> int:
    return PRESENCE_DEBOUNCE_GPS if _is_gps(st) else PRESENCE_DEBOUNCE


def _home_coords(hass):
    z = hass.states.get("zone.home")
    if z is not None:
        c = _entity_coords(z)
        if c:
            return c
    try:
        return float(hass.config.latitude), float(hass.config.longitude)
    except Exception:
        return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def distance_home_km(hass, st):
    """Great-circle distance from an entity's GPS coords to home, or None."""
    hc = _home_coords(hass)
    ec = _entity_coords(st)
    if not hc or not ec:
        return None
    return _haversine_km(ec[0], ec[1], hc[0], hc[1])


def predict_overdue(hass, now: float = None) -> list:
    """
    Flag recurring daily events that haven't happened yet today by their usual
    time ('the front door usually has activity by 07:45 — none yet today'). One
    alert per entity per day. Returns action dicts for the gated announce path.
    """
    now = now or time.time()
    out = []
    today = _local_day(now)
    now_secs = _secs_since_midnight(now)
    try:
        for eid, entry in list(_MODEL.items()):
            if eid.split(".", 1)[0] not in RECUR_DOMAINS:
                continue
            routine = _routine_of(entry)
            if routine is None:
                continue
            mean_s, std_s = routine
            tol = min(max(2 * std_s, RECUR_TOL_MIN), RECUR_TOL_MAX)
            if now_secs <= mean_s + tol:
                continue                       # not past the usual time + grace yet
            if entry.last_first_day == today:
                continue                       # already happened today — fine
            if _RECUR_ALERTED.get(eid) == today:
                continue                       # already alerted today
            _RECUR_ALERTED[eid] = today
            st = hass.states.get(eid)
            name = st.attributes.get("friendly_name", eid) if st else eid
            usual = f"{int(mean_s // 3600):02d}:{int((mean_s % 3600) // 60):02d}"
            out.append({
                "type": "anticipation_overdue",
                "urgency": "low",
                "message": (
                    f"{name} usually has activity by around {usual}, but there's "
                    f"been none yet today — thought it worth flagging."
                ),
                "pattern_key": f"overdue:{eid}",
                "offer": False,
            })
    except Exception as exc:
        _LOGGER.debug("predict_overdue error: %s", exc)
    return out


def _presence_entities(hass):
    out = []
    for dom in PRESENCE_DOMAINS:
        try:
            out.extend(hass.states.async_all(dom))
        except Exception:
            pass
    return out


def sample_presence(hass, now: float = None) -> int:
    """
    Record debounced departure/arrival times for person/device_tracker entities.
    A state must have held ≥PRESENCE_DEBOUNCE to count, so brief Wi-Fi/GPS flaps
    don't pollute the routine. Departure = first stable 'away' of the day;
    arrival = the LATEST stable 'home' of the day (i.e. home-for-the-evening),
    only counted on days the person actually left. Called periodically.
    """
    now = now or time.time()
    today = _local_day(now)
    seen = 0
    try:
        for st in _presence_entities(hass):
            cur = st.state
            if cur in _DEAD_STATES:
                continue
            eid = st.entity_id
            entry = _MODEL.get(eid)
            if entry is None:
                _evict_if_full()
                entry = _Entry(now)
                entry.last_state = cur
                _MODEL[eid] = entry
            ts = _last_changed_ts(st)
            if ts is None or (now - ts) < _debounce_for(st):
                continue  # unknown age, or not held long enough for THIS tracker type
            tsecs = _secs_since_midnight(ts)
            seen += 1
            if _is_away(cur):
                if entry.pres_depart_day != today:        # first stable away today
                    entry.pres_depart_day = today
                    entry.depart_first.append((today, tsecs))
            elif _is_home(cur):
                if entry.pres_depart_day == today:         # returned after leaving
                    if entry.pres_return_day == today and entry.return_first \
                            and entry.return_first[-1][0] == today:
                        entry.return_first[-1] = (today, tsecs)  # keep LATEST arrival
                    else:
                        entry.pres_return_day = today
                        entry.return_first.append((today, tsecs))
    except Exception as exc:
        _LOGGER.debug("sample_presence error: %s", exc)
    return seen


def predict_presence(hass, now: float = None) -> list:
    """
    Presence-routine overdue checks: 'usually out by HH:MM but still home' and
    'usually home by HH:MM but not back yet'. One alert per direction per entity
    per day. Returns action dicts for the gated announce path.
    """
    now = now or time.time()
    out = []
    today = _local_day(now)
    now_secs = _secs_since_midnight(now)
    try:
        for eid, entry in list(_MODEL.items()):
            if eid.split(".", 1)[0] not in PRESENCE_DOMAINS:
                continue
            st = hass.states.get(eid)
            if st is None or st.state in _DEAD_STATES:
                continue
            cur = st.state
            name = st.attributes.get("friendly_name", eid)

            # Departure overdue — usually gone by now, still home, hasn't left today
            dep = _routine_of_pts(list(entry.depart_first))
            if dep and _is_home(cur) and entry.pres_depart_day != today:
                m, sd = dep
                tol = min(max(2 * sd, RECUR_TOL_MIN), RECUR_TOL_MAX)
                key = "dep:" + eid
                if now_secs > m + tol and _RECUR_ALERTED.get(key) != today:
                    _RECUR_ALERTED[key] = today
                    out.append({
                        "type": "anticipation_presence", "urgency": "low",
                        "message": (f"{name} is usually out by around {_hhmm(m)}, "
                                    f"but is still home — thought you'd want to know."),
                        "pattern_key": f"presence_depart:{eid}", "offer": False,
                    })

            # Arrival overdue — usually home by now, not home, did leave today
            ret = _routine_of_pts(list(entry.return_first))
            if ret and _is_away(cur) and entry.pres_depart_day == today \
                    and entry.pres_return_day != today:
                m, sd = ret
                tol = min(max(2 * sd, RECUR_TOL_MIN), RECUR_TOL_MAX)
                key = "arr:" + eid
                if now_secs > m + tol and _RECUR_ALERTED.get(key) != today:
                    _RECUR_ALERTED[key] = today
                    out.append({
                        "type": "anticipation_presence", "urgency": "low",
                        "message": (f"{name} is usually home by around {_hhmm(m)}, "
                                    f"but isn't back yet."),
                        "pattern_key": f"presence_arrive:{eid}", "offer": False,
                    })
    except Exception as exc:
        _LOGGER.debug("predict_presence error: %s", exc)
    return out


def presence_status(hass) -> list:
    """
    Current whereabouts of each tracked person — zone, tracker type, and distance
    from home for location-aware trackers. Prefers person.* entities (they
    aggregate trackers); falls back to device_tracker.*. For panel / context.
    """
    out = []
    try:
        ents = []
        try:
            ents = list(hass.states.async_all("person"))
        except Exception:
            ents = []
        if not ents:
            try:
                ents = list(hass.states.async_all("device_tracker"))
            except Exception:
                ents = []
        for st in ents:
            if st.state in _DEAD_STATES:
                continue
            d = distance_home_km(hass, st)
            zone = "Home" if _is_home(st.state) else (
                "Away" if st.state == "not_home" else st.state)
            out.append({
                "entity_id": st.entity_id,
                "name": st.attributes.get("friendly_name", st.entity_id),
                "zone": zone,
                "gps": _is_gps(st),
                "distance_km": round(d, 1) if d is not None else None,
            })
    except Exception as exc:
        _LOGGER.debug("presence_status error: %s", exc)
    return out


def predict_proximity(hass, now: float = None) -> list:
    """
    Detect a location-aware (GPS) tracker heading home and flag it ONCE per trip
    when it crosses inside APPROACH_OUTER_KM while closing. Works entirely off
    the local network. State resets when the person reaches home. Returns action
    dicts for the gated announce path.
    """
    out = []
    try:
        for st in _presence_entities(hass):
            eid = st.entity_id
            if _is_home(st.state):
                _LAST_DIST.pop(eid, None)
                _APPROACH_ALERTED.pop(eid, None)
                continue
            ec = _entity_coords(st)
            if ec is None:
                continue  # no location → can't do proximity (network-only tracker)
            d = distance_home_km(hass, st)
            if d is None:
                continue
            prev = _LAST_DIST.get(eid)
            _LAST_DIST[eid] = d
            if prev is None:
                continue  # need a previous reading to know direction
            closing = d < (prev - APPROACH_MIN_CLOSE_KM)
            if closing and d <= APPROACH_OUTER_KM and not _APPROACH_ALERTED.get(eid):
                _APPROACH_ALERTED[eid] = True
                name = st.attributes.get("friendly_name", eid)
                out.append({
                    "type": "anticipation_arriving", "urgency": "low",
                    "message": f"{name} is heading home — about {d:.1f} km out.",
                    "pattern_key": f"arriving:{eid}", "offer": False,
                })
    except Exception as exc:
        _LOGGER.debug("predict_proximity error: %s", exc)
    return out


# ── Persistence (patterns.db) ────────────────────────────────────────────────
def _entry_to_dict(e: _Entry) -> dict:
    return {
        "last_state": e.last_state, "last_changed": e.last_changed,
        "first_seen": e.first_seen, "transitions": list(e.transitions),
        "n": e.n, "mean": e.mean, "m2": e.m2, "hours": e.hours,
        "occ": {str(h): d for h, d in e.occ.items()},
        "daily_first": list(e.daily_first), "last_first_day": e.last_first_day,
        "depart_first": list(e.depart_first), "return_first": list(e.return_first),
        "pres_depart_day": e.pres_depart_day, "pres_return_day": e.pres_return_day,
    }


def _entry_from_dict(d: dict) -> _Entry:
    e = _Entry(float(d.get("first_seen", time.time())))
    e.last_state = d.get("last_state", "")
    e.last_changed = float(d.get("last_changed", e.first_seen))
    e.transitions = deque(d.get("transitions", []), maxlen=TRANSITION_WINDOW)
    e.n = int(d.get("n", 0)); e.mean = float(d.get("mean", 0.0)); e.m2 = float(d.get("m2", 0.0))
    h = d.get("hours") or [0] * 24
    e.hours = h if len(h) == 24 else [0] * 24
    e.occ = {int(k): {s: int(c) for s, c in v.items()} for k, v in (d.get("occ") or {}).items()}
    e.daily_first = deque([tuple(x) for x in (d.get("daily_first") or [])], maxlen=45)
    e.last_first_day = int(d.get("last_first_day", 0))
    e.depart_first = deque([tuple(x) for x in (d.get("depart_first") or [])], maxlen=45)
    e.return_first = deque([tuple(x) for x in (d.get("return_first") or [])], maxlen=45)
    e.pres_depart_day = int(d.get("pres_depart_day", 0))
    e.pres_return_day = int(d.get("pres_return_day", 0))
    return e


def save_to_db(db_path: str) -> int:
    """Persist the model to patterns.db. SYNC — call via executor."""
    import json
    import sqlite3
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cognition_model "
                "(entity_id TEXT PRIMARY KEY, data TEXT, updated REAL)"
            )
            rows = [(eid, json.dumps(_entry_to_dict(e)), time.time())
                    for eid, e in list(_MODEL.items())]
            conn.executemany(
                "INSERT OR REPLACE INTO cognition_model (entity_id, data, updated) "
                "VALUES (?, ?, ?)", rows,
            )
            conn.commit()
        return len(rows)
    except Exception as exc:
        _LOGGER.debug("cognition save_to_db failed: %s", exc)
        return 0


def load_from_db(db_path: str) -> int:
    """Load the model from patterns.db. SYNC — call via executor."""
    import json
    import sqlite3
    n = 0
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cognition_model "
                "(entity_id TEXT PRIMARY KEY, data TEXT, updated REAL)"
            )
            cur = conn.execute("SELECT entity_id, data FROM cognition_model")
            for eid, data in cur.fetchall():
                try:
                    _MODEL[eid] = _entry_from_dict(json.loads(data))
                    n += 1
                except Exception:
                    pass
        _LOGGER.info("cognition: loaded %d entity models from patterns.db", n)
    except Exception as exc:
        _LOGGER.debug("cognition load_from_db failed: %s", exc)
    return n
