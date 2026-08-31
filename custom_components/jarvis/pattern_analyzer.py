"""
JARVIS — Pattern Analyzer (v5.9.00).

Reads state_changes and commands from patterns.db, identifies repeating
behavioral patterns, and proposes automations. Runs periodically (every
6 hours) once enough data is accumulated (7+ days).

Pattern types detected:
  1. Time-based routines: "Lights turned off every night around 10:30 PM"
  2. Sequence patterns: "Front door locks 5 min after garage closes"
  3. Repeated commands: "Turn off kitchen lights" said 3x/day at similar times
  4. Temperature preferences: thermostat adjusted to same temp at same times
  5. Presence-triggered: lights on when arriving, off when leaving

Each detected pattern gets a confidence score (0-1). Patterns above 0.7
become suggestions stored in the suggestions table. The user approves
or dismisses via conversation or the panel.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DB_PATH = "/config/jarvis/patterns.db"
MIN_DAYS = 7           # Don't analyze until we have this much data
MIN_OCCURRENCES = 5    # Pattern must repeat this many times
CONFIDENCE_THRESHOLD = 0.65  # Minimum to create a suggestion
ANALYSIS_INTERVAL = 21600    # 6 hours between analyses
KNOWLEDGE_FACT_CONFIDENCE = 0.75  # routines/commands above this also become observed facts
PERSON_DOMINANCE_RATIO = 0.8      # a person must account for this share of a
                                   # pattern's occurrences to own it, vs. household


def set_thresholds(min_occurrences: int | None = None,
                   confidence: float | None = None) -> None:
    """Loosen/tighten the pattern engine at runtime (panel-configurable). The
    cognitive tick calls this with the user's settings before each analysis."""
    global MIN_OCCURRENCES, CONFIDENCE_THRESHOLD
    if min_occurrences is not None:
        try:
            MIN_OCCURRENCES = max(2, int(min_occurrences))
        except Exception:
            pass
    if confidence is not None:
        try:
            CONFIDENCE_THRESHOLD = min(0.95, max(0.3, float(confidence)))
        except Exception:
            pass


# Adaptive suggestion threshold (opt-in): when enabled, how welcome recent
# suggestions were nudges the confidence bar for creating new ones — mostly
# dismissed as unneeded → stricter, almost all acted on → slightly looser. The
# delta is bounded and the result is clamped, so it can never run away, and it
# is derived ONLY from "suggestion" outcomes — it never touches intrusion,
# lockdown, or any security/safety decision.
_ADAPT_CACHE = {"ts": 0.0, "delta": 0.0}
_ADAPT_MIN_JUDGED = 5           # need this many judged suggestions before moving
_ADAPT_WINDOW_S = 30 * 86400.0  # look back a month


def _learned_threshold_delta() -> float:
    """Bounded adjustment (in [-0.07, +0.15]) to the suggestion confidence bar,
    learned from how recent suggestions were received. Returns 0.0 when the
    opt-in is off, on any error, or with too little evidence. Cached 5 min."""
    try:
        from . import jarvis_config
        if not jarvis_config.get("adaptive_suggestion_threshold", False):
            return 0.0
    except Exception:
        return 0.0
    now = time.time()
    if now - _ADAPT_CACHE["ts"] < 300.0:
        return _ADAPT_CACHE["delta"]
    delta = 0.0
    try:
        from . import decision_record
        r = decision_record.outcome_rate("suggestion", window_s=_ADAPT_WINDOW_S)
        if int(r.get("judged", 0)) >= _ADAPT_MIN_JUDGED:
            uw = r.get("unwelcome_rate") or 0.0
            if uw >= 0.5:
                delta = 0.15        # mostly unwelcome → much more selective
            elif uw >= 0.3:
                delta = 0.07        # somewhat unwelcome → more selective
            elif uw <= 0.1:
                delta = -0.07       # almost all welcome → a little more generous
    except Exception:
        delta = 0.0
    _ADAPT_CACHE.update(ts=now, delta=delta)
    return delta


def _effective_threshold() -> float:
    """CONFIDENCE_THRESHOLD adjusted by the learned delta, clamped [0.3, 0.95]."""
    return min(0.95, max(0.3, CONFIDENCE_THRESHOLD + _learned_threshold_delta()))


@dataclass
class DetectedPattern:
    pattern_type: str      # time_routine, sequence, repeated_command, temp_pref, presence
    description: str
    entity_ids: list[str]
    confidence: float
    occurrences: int
    coverage: float = 0.0  # positive days / opportunity days (0 = not computed)
    details: dict = field(default_factory=dict)


def normalize_suggestion_automation(stored_yaml: str) -> dict:
    """
    Pure: turn a suggestion's stored automation JSON into structured args for
    automation_creator.create_automation, or explain why it can't (v6.52.0).

    Closes the pattern-engine loop: the analyzer generates these blobs, the
    user approves, and this converts the blob into an installable automation.
    Handles the legacy trigger/action shape the generator emits — HA modernized
    'platform'→'trigger' and 'service'→'action', so we translate both — and
    refuses the non-actionable 'manual_review' markers honestly instead of
    fabricating an automation from a vague note.

    Returns either:
        {"installable": True, "alias", "trigger": [...], "action": [...]}
        {"installable": False, "reason": "..."}
    """
    if not stored_yaml:
        return {"installable": False, "reason": "no automation payload"}
    try:
        data = json.loads(stored_yaml)
    except Exception:
        return {"installable": False, "reason": "payload is not valid JSON"}

    if not isinstance(data, dict):
        return {"installable": False, "reason": "payload is not an object"}
    if data.get("type") == "manual_review" or "note" in data and "trigger" not in data:
        return {"installable": False,
                "reason": "advisory only — needs a human to design the automation"}

    alias = data.get("alias")
    trigger = data.get("trigger")
    action = data.get("action")
    if not alias or not trigger or not action:
        return {"installable": False, "reason": "missing alias, trigger, or action"}

    def _modernize_trigger(t: dict) -> dict:
        t = dict(t)
        if "platform" in t and "trigger" not in t:
            t["trigger"] = t.pop("platform")
        return t

    def _modernize_action(a: dict) -> dict:
        a = dict(a)
        if "service" in a and "action" not in a:
            a["action"] = a.pop("service")
        return a

    triggers = [trigger] if isinstance(trigger, dict) else list(trigger)
    actions = [action] if isinstance(action, dict) else list(action)
    triggers = [_modernize_trigger(t) if isinstance(t, dict) else t for t in triggers]
    # action items can be delays or service calls; only modernize the dicts
    norm_actions = []
    for a in actions:
        if isinstance(a, dict):
            norm_actions.append(_modernize_action(a))
        else:
            norm_actions.append(a)

    return {"installable": True, "alias": alias,
            "trigger": triggers, "action": norm_actions}


def service_for(entity_id: str, state: str) -> Optional[dict]:
    """
    Map an entity + desired state to the correct HA service call (v6.52.1).
    The pattern generator used to build every action as `{domain}.turn_{state}`,
    which is only valid for on/off domains — it would emit `lock.turn_on` for a
    learned door-lock routine (the module's own flagship example) and write a
    broken automation. Now each domain gets its real service; anything without a
    clean mapping returns None so the caller can mark it advisory instead of
    installing garbage.

    Returns {"service": "domain.service", "entity_id": ...} or None.
    """
    if not entity_id or "." not in entity_id:
        return None
    domain = entity_id.split(".")[0]
    s = str(state).lower().strip()

    onoff = {"light", "switch", "fan", "input_boolean", "humidifier", "siren"}
    if domain in onoff and s in ("on", "off"):
        return {"service": f"{domain}.turn_{s}", "entity_id": entity_id}

    if domain == "lock" and s in ("locked", "unlocked"):
        return {"service": f"lock.{'lock' if s == 'locked' else 'unlock'}",
                "entity_id": entity_id}

    if domain == "cover" and s in ("open", "closed", "opening", "closing"):
        # settle transient states to the intended end state
        want_open = s in ("open", "opening")
        return {"service": f"cover.{'open' if want_open else 'close'}_cover",
                "entity_id": entity_id}

    if domain in ("switch", "input_boolean") and s in ("on", "off"):
        return {"service": f"{domain}.turn_{s}", "entity_id": entity_id}

    # climate, media_player, and everything else need parameters we don't infer
    # from a bare state — better to advise than to guess.
    return None


def explain_suggestion(pattern_type: str, details: dict, count: int) -> dict:
    """Turn a suggestion's evidence into a human 'why' for the review UI
    (v6.80.0). Returns {headline, evidence:[...]} — the observations that led to
    the proposal, so approving is an informed choice rather than a leap. Pure,
    never raises."""
    d = details or {}
    ev: list[str] = []
    headline = ""
    try:
        if pattern_type == "time_routine":
            hour = d.get("hour")
            state = d.get("state")
            consistency = d.get("coverage", d.get("consistency"))
            observed = d.get("observed_days")
            opportunity = d.get("opportunity_days")
            person = d.get("person")
            when = f"{int(hour):02d}:00" if hour is not None else "a regular time"
            headline = f"A daily routine around {when}"
            if state is not None:
                ev.append(f"Observed turning {state} near {when}")
            # Prefer the honest coverage framing — how many days it happened out
            # of how many it could have, so the negative evidence is visible too.
            if observed is not None and opportunity:
                missed = max(0, int(opportunity) - int(observed))
                line = f"Happened on {int(observed)} of {int(opportunity)} days"
                if missed:
                    line += f" (missed {missed})"
                ev.append(line)
            else:
                ev.append(f"Happened {count} times in the last 30 days")
            if consistency is not None:
                ev.append(f"Consistent on about {int(float(consistency) * 100)}% of days")
            if person:
                ev.append(f"Specifically when {person} is home")
        elif pattern_type == "sequence":
            headline = "One action reliably follows another"
            first = d.get("first") or d.get("trigger")
            then = d.get("then") or d.get("action")
            if first and then:
                ev.append(f"After {first}, {then} usually follows")
            ev.append(f"Seen {count} times in 30 days")
            if d.get("window_seconds"):
                ev.append(f"Usually within {int(d['window_seconds'])}s")
        elif pattern_type == "repeated_command":
            headline = "A command you give often"
            cmd = d.get("command") or d.get("text")
            if cmd:
                ev.append(f"You've asked '{cmd}' {count} times")
            if d.get("hour") is not None:
                ev.append(f"Most often around {int(d['hour']):02d}:00")
        elif pattern_type == "temp_pref":
            headline = "A temperature preference"
            if d.get("target") is not None:
                ev.append(f"Set to {d['target']}° repeatedly")
            ev.append(f"Observed {count} times")
        elif pattern_type == "presence":
            headline = "A presence-linked pattern"
            ev.append(f"Correlated {count} times over 30 days")
        else:
            headline = "A learned pattern"
            ev.append(f"Observed {count} times in 30 days")
    except Exception:
        headline = headline or "A learned pattern"
        if not ev:
            ev.append(f"Observed {count} times")
    return {"headline": headline, "evidence": ev}


class PatternAnalyzer:
    """Analyzes accumulated state change data for behavioral patterns."""

    def __init__(self):
        self._last_analysis: float = 0.0
        self._db = DB_PATH

    def _connect(self) -> Optional[sqlite3.Connection]:
        try:
            if not Path(self._db).exists():
                return None
            conn = sqlite3.connect(self._db)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:
            return None

    def should_analyze(self) -> bool:
        """Check if enough data and time has passed for analysis."""
        if (time.time() - self._last_analysis) < ANALYSIS_INTERVAL:
            return False
        conn = self._connect()
        if not conn:
            return False
        try:
            oldest = conn.execute(
                "SELECT MIN(timestamp) FROM state_changes"
            ).fetchone()[0]
            if not oldest:
                return False
            days = (datetime.now() - datetime.fromisoformat(oldest)).days
            count = conn.execute("SELECT COUNT(*) FROM state_changes").fetchone()[0]
            conn.close()
            return days >= MIN_DAYS and count >= 50
        except Exception:
            return False

    async def analyze(self, hass: HomeAssistant) -> list[DetectedPattern]:
        """Run full pattern analysis. Returns detected patterns."""
        self._last_analysis = time.time()
        patterns = []

        conn = self._connect()
        if not conn:
            return patterns

        try:
            patterns.extend(await hass.async_add_executor_job(
                self._find_time_routines, conn))
            patterns.extend(await hass.async_add_executor_job(
                self._find_repeated_commands, conn))
            patterns.extend(await hass.async_add_executor_job(
                self._find_sequence_patterns, conn))
            patterns.extend(await hass.async_add_executor_job(
                self._find_presence_patterns, conn))
        except Exception as exc:
            _LOGGER.warning("Pattern analysis error: %s", exc)
        finally:
            conn.close()

        # Store high-confidence patterns as suggestions
        new_suggestions = 0
        new_person_patterns = 0
        _eff_threshold = _effective_threshold()
        for p in patterns:
            if p.confidence >= _eff_threshold:
                stored = await hass.async_add_executor_job(
                    self._store_suggestion, p)
                if stored:
                    new_suggestions += 1
                # v6.41.0: patterns confidently owned by one person also land
                # in person_patterns — the dedicated per-person routine store
                # (independent of the household suggestions/automations flow).
                if p.details.get("person"):
                    if await hass.async_add_executor_job(
                            self._store_person_pattern, p):
                        new_person_patterns += 1

        # Promote the most reliable routines/commands into the curated knowledge
        # store as *observed* facts, so they surface in the Memory tab (marked ~)
        # and inject into conversation. Sequences/presence stay as automations only.
        # v6.41.0: a pattern confidently owned by one person is attributed to
        # that person's knowledge subject rather than "household".
        promoted = await hass.async_add_executor_job(
            self._promote_to_knowledge, patterns)

        if patterns:
            _LOGGER.info(
                "Pattern analysis: %d patterns found, %d new suggestions, "
                "%d facts learned, %d person routines (threshold=%.0f%%)",
                len(patterns), new_suggestions, promoted, new_person_patterns,
                _eff_threshold * 100,
            )

        return patterns

    def _find_time_routines(self, conn: sqlite3.Connection) -> list[DetectedPattern]:
        """Find entities that change state at similar times each day."""
        patterns = []

        # Group state changes by entity + action, look for time clustering
        rows = conn.execute("""
            SELECT entity_id, new_state, hour, day_of_week, COUNT(*) as cnt
            FROM state_changes
            WHERE timestamp > datetime('now', '-30 days')
            GROUP BY entity_id, new_state, hour
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, (MIN_OCCURRENCES,)).fetchall()

        for row in rows:
            entity = row["entity_id"]
            state = row["new_state"]
            hour = row["hour"]
            count = row["cnt"]

            # Opportunity days: distinct days we were observing at all.
            total_days = conn.execute("""
                SELECT COUNT(DISTINCT date(timestamp)) FROM state_changes
                WHERE timestamp > datetime('now', '-30 days')
            """).fetchone()[0] or 1

            # Positive days: distinct days this routine ACTUALLY happened. Using
            # distinct days (not raw event count) so several same-hour events on
            # one day count once — the honest "on N of M days" numerator.
            positive_days = conn.execute("""
                SELECT COUNT(DISTINCT date(timestamp)) FROM state_changes
                WHERE entity_id = ? AND new_state = ? AND hour = ?
                  AND timestamp > datetime('now', '-30 days')
            """, (entity, state, hour)).fetchone()[0] or 0

            # Coverage weighs the negative evidence: a routine on 42 of 45 days
            # (0.93) is far stronger than one on 42 of 120 days (0.35), even
            # though both were "seen 42 times".
            coverage = positive_days / total_days if total_days else 0.0
            negative_days = max(0, total_days - positive_days)
            if coverage < 0.3:
                continue

            # Confidence = coverage, discounted for a small sample so a 3-of-3
            # (1.0) can't outrank a 40-of-45 (0.89) on three data points.
            sample_factor = min(1.0, positive_days / MIN_OCCURRENCES)
            confidence = round(coverage * sample_factor, 3)

            time_str = f"{hour:02d}:00"
            details = {
                "hour": hour, "state": state,
                "coverage": round(coverage, 2),
                "consistency": round(coverage, 2),   # back-compat key
                "observed_days": positive_days,
                "opportunity_days": total_days,
                "skipped_days": negative_days,
            }

            # v6.41.0: a single sole-occupant person can own this routine
            # outright; otherwise it stays household-wide, unchanged.
            person = self._dominant_person(conn, "state_changes", entity=entity,
                                            state=state, hour=hour)
            days_str = f"on {positive_days} of {total_days} days"
            if person:
                details["person"] = person
                desc = (f"{entity} turns {state} around {time_str} {days_str} "
                        f"when {person} is home")
            elif state in ("on", "off"):
                desc = f"{entity} turns {state} around {time_str} {days_str}"
            else:
                desc = f"{entity} changes to '{state}' around {time_str} {days_str}"

            patterns.append(DetectedPattern(
                pattern_type="time_routine",
                description=desc,
                entity_ids=[entity],
                confidence=confidence,
                occurrences=count,
                coverage=round(coverage, 3),
                details=details,
            ))

        return patterns[:20]  # Cap at 20

    def _find_repeated_commands(self, conn: sqlite3.Connection) -> list[DetectedPattern]:
        """Find voice commands that repeat at similar times."""
        patterns = []

        try:
            rows = conn.execute("""
                SELECT text, hour, COUNT(*) as cnt
                FROM commands
                WHERE timestamp > datetime('now', '-30 days')
                GROUP BY text, hour
                HAVING cnt >= ?
                ORDER BY cnt DESC
                LIMIT 20
            """, (MIN_OCCURRENCES,)).fetchall()
        except Exception:
            return patterns

        for row in rows:
            text = row["text"]
            hour = row["hour"]
            count = row["cnt"]

            total_same_cmd = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE text = ?", (text,)
            ).fetchone()[0]

            confidence = min(1.0, (count / total_same_cmd) * 0.8 + 0.2)
            details = {"command": text, "hour": hour}

            person = self._dominant_person(conn, "commands", text=text, hour=hour)
            if person:
                details["person"] = person
                desc = (f"{person} says '{text}' around {hour:02d}:00 regularly "
                        f"({count} times)")
            else:
                desc = f"'{text}' is said around {hour:02d}:00 regularly ({count} times)"

            patterns.append(DetectedPattern(
                pattern_type="repeated_command",
                description=desc,
                entity_ids=[],
                confidence=confidence,
                occurrences=count,
                details=details,
            ))

        return patterns

    def _find_sequence_patterns(self, conn: sqlite3.Connection) -> list[DetectedPattern]:
        """Find state changes that consistently follow each other."""
        patterns = []

        # Get pairs of state changes within 10 minutes of each other
        try:
            rows = conn.execute("""
                SELECT
                    a.entity_id as entity_a,
                    a.new_state as state_a,
                    b.entity_id as entity_b,
                    b.new_state as state_b,
                    COUNT(*) as cnt
                FROM state_changes a
                JOIN state_changes b ON
                    datetime(b.timestamp) > datetime(a.timestamp) AND
                    datetime(b.timestamp) <= datetime(a.timestamp, '+10 minutes') AND
                    a.entity_id != b.entity_id AND
                    a.domain = b.domain
                WHERE a.timestamp > datetime('now', '-30 days')
                GROUP BY a.entity_id, a.new_state, b.entity_id, b.new_state
                HAVING cnt >= ?
                ORDER BY cnt DESC
                LIMIT 15
            """, (MIN_OCCURRENCES,)).fetchall()
        except Exception:
            return patterns

        for row in rows:
            ea, sa, eb, sb = row["entity_a"], row["state_a"], row["entity_b"], row["state_b"]
            count = row["cnt"]
            confidence = min(1.0, count / (MIN_OCCURRENCES * 3))

            patterns.append(DetectedPattern(
                pattern_type="sequence",
                description=(
                    f"When {ea} turns {sa}, {eb} turns {sb} shortly after "
                    f"({count} times in 30 days)"
                ),
                entity_ids=[ea, eb],
                confidence=confidence,
                occurrences=count,
                details={"trigger": {"entity": ea, "state": sa},
                         "action": {"entity": eb, "state": sb}},
            ))

        return patterns

    def _find_presence_patterns(self, conn: sqlite3.Connection) -> list[DetectedPattern]:
        """Find state changes correlated with person arrivals/departures."""
        patterns = []

        # Look for state changes that happen within 5 min of person state changes
        try:
            rows = conn.execute("""
                SELECT
                    a.entity_id as person_entity,
                    a.new_state as person_state,
                    b.entity_id as device_entity,
                    b.new_state as device_state,
                    COUNT(*) as cnt
                FROM state_changes a
                JOIN state_changes b ON
                    b.timestamp > a.timestamp AND
                    b.timestamp <= datetime(a.timestamp, '+5 minutes') AND
                    a.entity_id != b.entity_id
                WHERE a.timestamp > datetime('now', '-30 days')
                    AND a.domain = 'person'
                GROUP BY a.entity_id, a.new_state, b.entity_id, b.new_state
                HAVING cnt >= ?
                ORDER BY cnt DESC
                LIMIT 10
            """, (max(3, MIN_OCCURRENCES // 2),)).fetchall()
        except Exception:
            return patterns

        for row in rows:
            person = row["person_entity"]
            p_state = row["person_state"]
            device = row["device_entity"]
            d_state = row["device_state"]
            count = row["cnt"]

            action_word = "arrives" if p_state == "home" else "leaves"
            confidence = min(1.0, count / MIN_OCCURRENCES * 0.7)

            patterns.append(DetectedPattern(
                pattern_type="presence",
                description=(
                    f"When {person} {action_word}, {device} turns {d_state} "
                    f"({count} times)"
                ),
                entity_ids=[person, device],
                confidence=confidence,
                occurrences=count,
                details={"trigger_person": person, "trigger_state": p_state,
                         "action_entity": device, "action_state": d_state},
            ))

        return patterns

    def _dominant_person(self, conn: sqlite3.Connection, table: str, *,
                         hour: int, entity: str | None = None,
                         state: str | None = None,
                         text: str | None = None) -> Optional[str]:
        """
        If one known person accounts for most of a pattern's occurrences,
        return them; else None, meaning the pattern stays household-wide.
        `table` is "state_changes" (match on entity+state+hour) or
        "commands" (match on text+hour). Defensive: an unmigrated DB
        missing the `person` column just falls back to household (None).
        """
        # v6.77.0: weight each event by how CONFIDENT the attribution was, so a
        # room-scoped "probably Eliana (0.62)" contributes proportionally instead
        # of being thrown away. Commands keep full weight — the conversation path
        # runs the full identity resolver, so those attributions are strong.
        try:
            if table == "state_changes":
                rows = conn.execute("""
                    SELECT person, COUNT(*) as cnt,
                           SUM(COALESCE(NULLIF(person_confidence, 0), 0.5)) as wt
                    FROM state_changes
                    WHERE entity_id = ? AND new_state = ? AND hour = ?
                        AND timestamp > datetime('now', '-30 days')
                    GROUP BY person ORDER BY wt DESC
                """, (entity, state, hour)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT person, COUNT(*) as cnt, COUNT(*) * 1.0 as wt
                    FROM commands
                    WHERE text = ? AND hour = ?
                        AND timestamp > datetime('now', '-30 days')
                    GROUP BY person ORDER BY wt DESC
                """, (text, hour)).fetchall()
        except Exception:
            # older DB without the confidence column — fall back to raw counts
            try:
                if table == "state_changes":
                    rows = conn.execute("""
                        SELECT person, COUNT(*) as cnt, COUNT(*) * 1.0 as wt
                        FROM state_changes
                        WHERE entity_id = ? AND new_state = ? AND hour = ?
                            AND timestamp > datetime('now', '-30 days')
                        GROUP BY person ORDER BY wt DESC
                    """, (entity, state, hour)).fetchall()
                else:
                    return None
            except Exception:
                return None

        if not rows:
            return None
        # Ignore the unknown bucket rather than aborting on it: previously a
        # dominant 'unknown' killed the whole pattern, so multi-occupant houses
        # (where sole-occupancy rarely holds) never produced per-person routines.
        named = [r for r in rows if r["person"] and r["person"] != "unknown"]
        if not named:
            return None
        total = sum(float(r["wt"] or 0.0) for r in named)
        top = named[0]
        top_wt = float(top["wt"] or 0.0)
        if total <= 0:
            return None
        if (top_wt / total >= PERSON_DOMINANCE_RATIO
                and top["cnt"] >= MIN_OCCURRENCES):
            return top["person"]
        return None

    def _entity_label(self, entity_id: str) -> str:
        """Readable label from an entity_id (no friendly name available here)."""
        name = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        return name.replace("_", " ").strip()

    def _fact_for(self, pattern: "DetectedPattern"):
        """
        Map a detected pattern to an observed knowledge fact, or None if it's not
        the kind of thing worth stating as butler-knowledge. Returns
        (subject, kind, key, value). Deterministic so re-analysis upserts in place.
        """
        if pattern.pattern_type == "time_routine" and pattern.entity_ids:
            label = self._entity_label(pattern.entity_ids[0])
            state = str(pattern.details.get("state", "")).strip()
            hour = pattern.details.get("hour")
            if hour is None or not label:
                return None
            when = f"around {hour:02d}:00 most days"
            subject = self._subject_for_pattern(pattern)
            if state in ("on", "off"):
                return (subject, "fact", f"{label} turns {state}", when)
            return (subject, "fact", f"{label} set to {state}", when)
        if pattern.pattern_type == "repeated_command":
            text = str(pattern.details.get("command", "")).strip()
            hour = pattern.details.get("hour")
            if not text or hour is None:
                return None
            subject = self._subject_for_pattern(pattern)
            return (subject, "fact", f'asks "{text[:60]}"',
                    f"usually around {hour:02d}:00")
        return None

    def _subject_for_pattern(self, pattern: "DetectedPattern") -> str:
        """
        The knowledge subject to attribute a promoted fact to: a specific
        person's subject when the pattern is confidently theirs alone
        (v6.41.0), else "household" — identical to pre-6.41 behavior.
        """
        person = pattern.details.get("person")
        if not person:
            return "household"
        try:
            from . import identity
            return identity.normalize(person)
        except Exception:
            return "household"

    def _promote_to_knowledge(self, patterns: list) -> int:
        """Write the most reliable routines/commands as observed facts. SYNC."""
        try:
            from . import knowledge
        except Exception:
            return 0
        written = 0
        for p in patterns:
            if p.confidence < KNOWLEDGE_FACT_CONFIDENCE:
                continue
            mapped = self._fact_for(p)
            if not mapped:
                continue
            subject, kind, key, value = mapped
            try:
                stored = knowledge.remember(
                    key, value, subject=subject, kind=kind, source="observed",
                    confidence=round(float(p.confidence), 3), salience=0.8,
                    respect_stated=True,
                )
                if stored:
                    written += 1
            except Exception as exc:
                _LOGGER.debug("knowledge promote failed for %r: %s", key, exc)
        return written

    def _store_person_pattern(self, pattern: DetectedPattern) -> bool:
        """Upsert a person-owned routine into the person_patterns store (now
        owned by the person_patterns module). Deterministic key
        (person, pattern_type, description) so re-analysis refreshes in place."""
        person = pattern.details.get("person")
        if not person:
            return False
        from . import person_patterns
        return person_patterns.store(
            person, pattern.pattern_type, pattern.description,
            data=pattern.details, confidence=pattern.confidence,
            occurrences=pattern.occurrences, db_path=self._db,
        )

    def get_person_patterns(self, person: Optional[str] = None) -> list[dict]:
        """Read stored per-person routines (person_patterns module), optionally
        filtered to one person (matched on the already-normalized id)."""
        from . import person_patterns
        return person_patterns.read(person, db_path=self._db)

    def _store_suggestion(self, pattern: DetectedPattern) -> bool:
        """Store a pattern as a suggestion in the DB. Returns True if new."""
        try:
            conn = sqlite3.connect(self._db)
            # Check if similar suggestion already exists
            existing = conn.execute(
                "SELECT id FROM suggestions WHERE description = ?",
                (pattern.description,)
            ).fetchone()
            if existing:
                # Update occurrence count and confidence
                conn.execute(
                    "UPDATE suggestions SET confidence = ?, pattern_count = ? WHERE id = ?",
                    (pattern.confidence, pattern.occurrences, existing[0]),
                )
                conn.commit()
                conn.close()
                return False

            # Generate automation YAML suggestion
            auto_yaml = self._generate_automation(pattern)

            _cur = conn.execute(
                "INSERT INTO suggestions (created, description, automation_yaml, "
                "confidence, pattern_count, pattern_type, entity_ids, details, "
                "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (datetime.now().isoformat(), pattern.description,
                 auto_yaml, pattern.confidence, pattern.occurrences,
                 pattern.pattern_type,
                 json.dumps(pattern.entity_ids or []),
                 json.dumps(pattern.details or {})),
            )
            _new_sid = _cur.lastrowid
            conn.commit()
            conn.close()
            try:
                from . import decision_record
                decision_record.record(
                    "suggestion",
                    observation={"pattern_type": pattern.pattern_type,
                                 "entities": pattern.entity_ids or [],
                                 "occurrences": pattern.occurrences},
                    interpretation={"suggested": pattern.description},
                    decision="propose automation",
                    reason="recurring observed behavior",
                    confidence=pattern.confidence,
                    ref="suggestion:%d" % _new_sid,
                )
            except Exception:
                pass
            return True
        except Exception as exc:
            _LOGGER.debug("Store suggestion error: %s", exc)
            return False

    def _generate_automation(self, pattern: DetectedPattern) -> str:
        """Generate HA automation YAML from a detected pattern."""
        p = pattern
        d = p.details

        if p.pattern_type == "time_routine" and d.get("state") in ("on", "off"):
            return json.dumps({
                "alias": f"JARVIS Learned: {p.entity_ids[0]} {d['state']} at {d['hour']:02d}:00",
                "trigger": {"platform": "time", "at": f"{d['hour']:02d}:00:00"},
                "action": {
                    "service": f"{p.entity_ids[0].split('.')[0]}.turn_{d['state']}",
                    "entity_id": p.entity_ids[0],
                },
            }, indent=2)

        if p.pattern_type == "sequence":
            trigger = d.get("trigger", {})
            action = d.get("action", {})
            svc = service_for(action.get("entity", ""), action.get("state", ""))
            if not svc:
                return json.dumps({
                    "note": f"Consider automating: {action.get('entity','?')} → "
                            f"{action.get('state','?')} after "
                            f"{trigger.get('entity','?')} "
                            f"{trigger.get('state','?')}",
                    "type": "manual_review",
                }, indent=2)
            return json.dumps({
                "alias": f"JARVIS Learned: {action['entity']} after {trigger['entity']}",
                "trigger": {
                    "platform": "state",
                    "entity_id": trigger["entity"],
                    "to": trigger["state"],
                },
                "action": [
                    {"delay": "00:01:00"},
                    svc,
                ],
            }, indent=2)

        if p.pattern_type == "repeated_command":
            return json.dumps({
                "note": f"Consider automating: '{d.get('command', '')}' at {d.get('hour', 0):02d}:00",
                "type": "manual_review",
            }, indent=2)

        if p.pattern_type == "presence":
            svc = service_for(d.get("action_entity", ""), d.get("action_state", ""))
            if not svc:
                return json.dumps({
                    "note": f"Consider automating: {d.get('action_entity','?')} → "
                            f"{d.get('action_state','?')} when "
                            f"{d.get('trigger_person','?')} "
                            f"{d.get('trigger_state','?')}",
                    "type": "manual_review",
                }, indent=2)
            return json.dumps({
                "alias": f"JARVIS Learned: {d['action_entity']} when {d['trigger_person']} {d['trigger_state']}",
                "trigger": {
                    "platform": "state",
                    "entity_id": d["trigger_person"],
                    "to": d["trigger_state"],
                },
                "action": svc,
            }, indent=2)

        return json.dumps({"note": p.description}, indent=2)

    def get_pending_suggestions(self) -> list[dict]:
        """Get all pending suggestions for the user to review."""
        conn = self._connect()
        if not conn:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM suggestions WHERE status = 'pending' "
                "ORDER BY confidence DESC LIMIT 20"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def get_suggestion(self, suggestion_id: int) -> Optional[dict]:
        """One suggestion row by id, or None."""
        conn = self._connect()
        if not conn:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
        finally:
            conn.close()

    def mark_installed(self, suggestion_id: int, automation_id: str) -> None:
        """Record that an approved suggestion became a live automation."""
        conn = self._connect()
        if not conn:
            return
        try:
            # widen status vocabulary without a migration: 'installed' is just
            # another string the UI can render distinctly from 'approved'.
            conn.execute(
                "UPDATE suggestions SET status = 'installed', "
                "approved_at = ? WHERE id = ?",
                (datetime.now().isoformat(), suggestion_id),
            )
            try:  # Decision Record outcome (v7.40.0): an installed suggestion was useful
                from . import decision_record
                decision_record.set_outcome_by_ref(
                    "suggestion:%d" % suggestion_id, "good", "installed")
            except Exception:
                pass
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def approve_suggestion(self, suggestion_id: int) -> bool:
        """Mark a suggestion as approved."""
        conn = self._connect()
        if not conn:
            return False
        try:
            conn.execute(
                "UPDATE suggestions SET status = 'approved', "
                "approved_at = ? WHERE id = ?",
                (datetime.now().isoformat(), suggestion_id),
            )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def dismiss_suggestion(self, suggestion_id: int) -> bool:
        """Mark a suggestion as dismissed."""
        conn = self._connect()
        if not conn:
            return False
        try:
            conn.execute(
                "UPDATE suggestions SET status = 'dismissed', "
                "dismissed_at = ? WHERE id = ?",
                (datetime.now().isoformat(), suggestion_id),
            )
            conn.commit()
            try:  # Decision Record outcome (v7.40.0): a dismissed suggestion was unnecessary
                from . import decision_record
                decision_record.set_outcome_by_ref(
                    "suggestion:%d" % suggestion_id, "unnecessary", "dismiss_suggestion")
            except Exception:
                pass
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Return analysis statistics."""
        conn = self._connect()
        if not conn:
            return {"available": False}
        try:
            stats = {
                "available": True,
                "state_changes": conn.execute(
                    "SELECT COUNT(*) FROM state_changes").fetchone()[0],
                "commands": conn.execute(
                    "SELECT COUNT(*) FROM commands").fetchone()[0],
                "pending_suggestions": conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='pending'"
                ).fetchone()[0],
                "approved": conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='approved'"
                ).fetchone()[0],
                "dismissed": conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE status='dismissed'"
                ).fetchone()[0],
            }
            oldest = conn.execute(
                "SELECT MIN(timestamp) FROM state_changes"
            ).fetchone()[0]
            if oldest:
                stats["days_of_data"] = (
                    datetime.now() - datetime.fromisoformat(oldest)
                ).days
                stats["ready_for_analysis"] = stats["days_of_data"] >= MIN_DAYS
            else:
                stats["days_of_data"] = 0
                stats["ready_for_analysis"] = False
            return stats
        except Exception:
            return {"available": False}
        finally:
            conn.close()


# ── Singleton ───────────────────────────────────────────────────────────────

_ANALYZER = PatternAnalyzer()


def get_analyzer() -> PatternAnalyzer:
    return _ANALYZER


async def install_approved_suggestion(hass, suggestion_id: int) -> dict:
    """
    Close the pattern-engine loop (v6.52.0): approve a suggestion AND actually
    install its automation into Home Assistant, instead of only flagging it
    approved. Returns a dict the caller relays:

        {"ok": True, "installed": True, "automation_id": "...", "alias": "..."}
        {"ok": True, "installed": False, "reason": "..."}   # approved, advisory
        {"ok": False, "error": "..."}                        # not found / failed

    Advisory suggestions (repeated-command notes with no concrete trigger) are
    still marked approved — the user acknowledged them — but nothing is written
    to HA, and the reason says so plainly.
    """
    analyzer = get_analyzer()
    try:
        sug = await hass.async_add_executor_job(analyzer.get_suggestion, suggestion_id)
        if not sug:
            return {"ok": False, "error": f"suggestion #{suggestion_id} not found"}

        # Always record the user's approval first.
        await hass.async_add_executor_job(analyzer.approve_suggestion, suggestion_id)

        norm = normalize_suggestion_automation(sug.get("automation_yaml", ""))
        if not norm.get("installable"):
            return {"ok": True, "installed": False,
                    "reason": norm.get("reason", "not installable"),
                    "suggestion_id": suggestion_id}

        from .automation_creator import create_automation
        result = await create_automation(
            hass,
            alias=norm["alias"],
            description=sug.get("description", ""),
            trigger=norm["trigger"],
            action=norm["action"],
        )
        if result.get("success"):
            await hass.async_add_executor_job(
                analyzer.mark_installed, suggestion_id, result["automation_id"])
            try:
                from .websocket import jarvis_log
                jarvis_log("LEARN", f"Installed learned automation "
                                    f"'{result['alias']}' from suggestion "
                                    f"#{suggestion_id}")
            except Exception:
                pass
            return {"ok": True, "installed": True,
                    "automation_id": result["automation_id"],
                    "alias": result["alias"], "suggestion_id": suggestion_id}
        return {"ok": True, "installed": False,
                "reason": f"automation write failed: {result.get('error')}",
                "suggestion_id": suggestion_id}
    except Exception as exc:
        _LOGGER.exception("install_approved_suggestion failed: %s", exc)
        return {"ok": False, "error": str(exc)}

