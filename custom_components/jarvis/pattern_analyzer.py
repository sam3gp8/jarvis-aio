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


@dataclass
class DetectedPattern:
    pattern_type: str      # time_routine, sequence, repeated_command, temp_pref, presence
    description: str
    entity_ids: list[str]
    confidence: float
    occurrences: int
    details: dict = field(default_factory=dict)


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
        for p in patterns:
            if p.confidence >= CONFIDENCE_THRESHOLD:
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
                CONFIDENCE_THRESHOLD * 100,
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

            # Check consistency — does this happen most days?
            total_days = conn.execute("""
                SELECT COUNT(DISTINCT date(timestamp)) FROM state_changes
                WHERE timestamp > datetime('now', '-30 days')
            """).fetchone()[0] or 1

            consistency = count / total_days
            if consistency < 0.3:
                continue

            confidence = min(1.0, consistency * (count / MIN_OCCURRENCES) * 0.5)

            time_str = f"{hour:02d}:00"
            details = {"hour": hour, "state": state, "consistency": round(consistency, 2)}

            # v6.41.0: a single sole-occupant person can own this routine
            # outright; otherwise it stays household-wide, unchanged.
            person = self._dominant_person(conn, "state_changes", entity=entity,
                                            state=state, hour=hour)
            if person:
                details["person"] = person
                desc = (f"{entity} turns {state} around {time_str} most days "
                        f"when {person} is home ({count} times in 30 days)")
            elif state in ("on", "off"):
                desc = f"{entity} turns {state} around {time_str} most days ({count} times in 30 days)"
            else:
                desc = f"{entity} changes to '{state}' around {time_str} ({count} times in 30 days)"

            patterns.append(DetectedPattern(
                pattern_type="time_routine",
                description=desc,
                entity_ids=[entity],
                confidence=confidence,
                occurrences=count,
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
        try:
            if table == "state_changes":
                rows = conn.execute("""
                    SELECT person, COUNT(*) as cnt FROM state_changes
                    WHERE entity_id = ? AND new_state = ? AND hour = ?
                        AND timestamp > datetime('now', '-30 days')
                    GROUP BY person ORDER BY cnt DESC
                """, (entity, state, hour)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT person, COUNT(*) as cnt FROM commands
                    WHERE text = ? AND hour = ?
                        AND timestamp > datetime('now', '-30 days')
                    GROUP BY person ORDER BY cnt DESC
                """, (text, hour)).fetchall()
        except Exception:
            return None

        if not rows:
            return None
        total = sum(r["cnt"] for r in rows)
        top = rows[0]
        if not top["person"] or top["person"] == "unknown" or total <= 0:
            return None
        if (top["cnt"] / total >= PERSON_DOMINANCE_RATIO
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
        """
        Upsert a person-owned pattern into person_patterns — the dedicated
        per-person routine store (separate from the household suggestions/
        knowledge flow) that a future Routines panel card reads from.
        Deterministic key (person, pattern_type, description) so
        re-analysis refreshes in place rather than duplicating.
        """
        person = pattern.details.get("person")
        if not person:
            return False
        try:
            from . import identity
            person = identity.normalize(person)
        except Exception:
            pass
        try:
            conn = sqlite3.connect(self._db)
            existing = conn.execute(
                "SELECT id FROM person_patterns "
                "WHERE person = ? AND pattern_type = ? AND description = ?",
                (person, pattern.pattern_type, pattern.description),
            ).fetchone()
            now_iso = datetime.now().isoformat()
            if existing:
                conn.execute(
                    "UPDATE person_patterns SET confidence = ?, occurrences = ?, "
                    "last_seen = ?, data = ? WHERE id = ?",
                    (pattern.confidence, pattern.occurrences, now_iso,
                     json.dumps(pattern.details), existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO person_patterns "
                    "(person, pattern_type, description, data, confidence, "
                    "last_seen, occurrences) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (person, pattern.pattern_type, pattern.description,
                     json.dumps(pattern.details), pattern.confidence,
                     now_iso, pattern.occurrences),
                )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            _LOGGER.debug("person_patterns store failed: %s", exc)
            return False

    def get_person_patterns(self, person: Optional[str] = None) -> list[dict]:
        """Read stored per-person routines, optionally filtered to one
        person (matched on the already-normalized id, e.g. 'sam')."""
        conn = self._connect()
        if not conn:
            return []
        try:
            if person:
                rows = conn.execute(
                    "SELECT * FROM person_patterns WHERE person = ? "
                    "ORDER BY confidence DESC", (person,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM person_patterns ORDER BY person, confidence DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

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

            conn.execute(
                "INSERT INTO suggestions (created, description, automation_yaml, "
                "confidence, pattern_count, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                (datetime.now().isoformat(), pattern.description,
                 auto_yaml, pattern.confidence, pattern.occurrences),
            )
            conn.commit()
            conn.close()
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
            return json.dumps({
                "alias": f"JARVIS Learned: {action['entity']} after {trigger['entity']}",
                "trigger": {
                    "platform": "state",
                    "entity_id": trigger["entity"],
                    "to": trigger["state"],
                },
                "action": [
                    {"delay": "00:01:00"},
                    {
                        "service": f"{action['entity'].split('.')[0]}.turn_{action['state']}",
                        "entity_id": action["entity"],
                    },
                ],
            }, indent=2)

        if p.pattern_type == "repeated_command":
            return json.dumps({
                "note": f"Consider automating: '{d.get('command', '')}' at {d.get('hour', 0):02d}:00",
                "type": "manual_review",
            }, indent=2)

        if p.pattern_type == "presence":
            return json.dumps({
                "alias": f"JARVIS Learned: {d['action_entity']} when {d['trigger_person']} {d['trigger_state']}",
                "trigger": {
                    "platform": "state",
                    "entity_id": d["trigger_person"],
                    "to": d["trigger_state"],
                },
                "action": {
                    "service": f"{d['action_entity'].split('.')[0]}.turn_{d['action_state']}",
                    "entity_id": d["action_entity"],
                },
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
