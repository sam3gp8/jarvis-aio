"""Atomic state-recovery ledger for JARVIS.

A small write-ahead log: before a high-stakes action (lock, cover, alarm) fires,
its *intent* is appended to disk; after it's confirmed, a completion record is
appended. On boot, ``reconcile`` replays any intent that never completed and
checks whether the physical device actually reached the desired state — surfacing
actions that a crash or power loss interrupted mid-flight.

Append-only JSON-lines on disk (crash-friendly), compacted to just the
outstanding intents once reconciled. Stdlib-only, so the ledger and its
reconciliation logic test without Home Assistant.

NOTE: lives at the top level as ``state_ledger.py`` — deliberately NOT under a
``memory/`` package, which would shadow the canonical ``memory.py`` module.
"""
from __future__ import annotations

import json
import logging
import os
import time
from threading import Lock

_LOGGER = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = "/config/jarvis/state_ledger.jsonl"
MAX_RECORDS = 5000


class StateLedger:
    """Append-only write-ahead log for high-stakes device intents."""

    def __init__(self, path: str = DEFAULT_LEDGER_PATH, *, max_records: int = MAX_RECORDS) -> None:
        self.path = path
        self.max_records = max_records
        self._lock = Lock()
        self._seq = 0

    # ── Disk (append-only JSON lines, corruption-tolerant) ────────────────
    def _read(self) -> list[dict]:
        records: list[dict] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # skip a torn final line from a crash
        except FileNotFoundError:
            return []
        except OSError as exc:
            _LOGGER.warning("state ledger unreadable (%s)", exc)
            return []
        return records

    def _append(self, record: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())  # durable before the action proceeds
        except OSError as exc:
            _LOGGER.error("Failed to append to state ledger: %s", exc)

    def _rewrite(self, records: list[dict]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            _LOGGER.error("Failed to rewrite state ledger: %s", exc)

    # ── Write-ahead API ───────────────────────────────────────────────────
    def record_intent(self, entity_id: str, desired_state: str, **meta) -> str:
        """Durably log the intent to drive ``entity_id`` to ``desired_state``
        BEFORE issuing the service call. Returns a txn id."""
        with self._lock:
            self._seq += 1
            txn = f"{time.time():.6f}-{entity_id}-{self._seq}"
            self._append({
                "txn": txn,
                "op": "intent",
                "entity_id": entity_id,
                "desired_state": str(desired_state),
                "meta": meta,
                "ts": time.time(),
            })
            # Bound growth: compact if the log has grown large.
            if len(self._read()) > self.max_records:
                self._compact_locked()
        return txn

    def mark_complete(self, txn: str) -> None:
        """Log that ``txn``'s action was confirmed."""
        with self._lock:
            self._append({"txn": txn, "op": "complete", "ts": time.time()})

    # ── Recovery ──────────────────────────────────────────────────────────
    def pending_intents(self) -> list[dict]:
        """Intents with no matching completion — i.e. possibly interrupted."""
        records = self._read()
        completed = {r.get("txn") for r in records if r.get("op") == "complete"}
        return [
            r for r in records
            if r.get("op") == "intent" and r.get("txn") not in completed
        ]

    def reconcile(self, verify_fn) -> list[dict]:
        """For every outstanding intent, compare the desired state against the
        device's current state via ``verify_fn(entity_id) -> state | None``.
        Returns the mismatches as {..intent.., 'actual': <state>}."""
        discrepancies: list[dict] = []
        for intent in self.pending_intents():
            try:
                actual = verify_fn(intent["entity_id"])
            except Exception:  # noqa: BLE001
                actual = None
            if str(actual) != intent["desired_state"]:
                discrepancies.append({**intent, "actual": actual})
        return discrepancies

    def compact(self) -> None:
        """Drop completed transactions, keeping only outstanding intents."""
        with self._lock:
            self._compact_locked()

    def _compact_locked(self) -> None:
        self._rewrite(self.pending_intents())
