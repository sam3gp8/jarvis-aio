"""Infrastructure triage for JARVIS.

``InfrastructureTriage`` reads a handful of health entities straight off the
Home Assistant state machine, grades each against thresholds, and synthesises a
single natural-language verdict in JARVIS's voice. It is intentionally
defensive: every check is isolated, and unreadable/unknown states are reported
as a *degraded-visibility* warning rather than silently passing or crashing.

The check table is data-driven so new probes are a one-line addition.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# States that mean "Home Assistant cannot currently read this entity".
_UNKNOWN_STATES = {"unknown", "unavailable", "none", ""}

# Severity ranking for ordering the spoken summary (higher = spoken first).
_SEV_CRITICAL = 3
_SEV_WARNING = 2
_SEV_INFO = 1


@dataclass
class Finding:
    severity: int
    phrase: str  # a self-contained spoken clause, e.g. "root storage is at 97 percent"
    label: str = ""  # short tag for memory recall, e.g. "root storage"


@dataclass
class _ThresholdCheck:
    entity_id: str
    label: str
    warn_above: float
    critical_above: float
    unit: str = "percent"


@dataclass
class _BinaryCheck:
    entity_id: str
    label: str
    bad_state: str  # the state that constitutes a fault, e.g. "off"


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


class InfrastructureTriage:
    """Evaluate core infrastructure entities and produce a spoken verdict."""

    #: Percentage utilisation probes.
    THRESHOLD_CHECKS: tuple[_ThresholdCheck, ...] = (
        _ThresholdCheck(
            "sensor.server_root_storage_usage", "root storage",
            warn_above=90.0, critical_above=96.0,
        ),
        _ThresholdCheck(
            "sensor.server_ram_usage", "system memory",
            warn_above=92.0, critical_above=92.0,
        ),
    )

    #: Connectivity / liveness probes (a binary_sensor in ``bad_state`` is a fault).
    BINARY_CHECKS: tuple[_BinaryCheck, ...] = (
        _BinaryCheck(
            "binary_sensor.core_switch_status", "the core network switch", "off",
        ),
        _BinaryCheck(
            "binary_sensor.basement_freeze_sensor_connectivity",
            "the basement freeze sensor", "off",
        ),
    )

    #: Root-cause diagnostic tree for the core switch — when it drops offline,
    #: its upstream power monitor distinguishes a power loss from a link fault.
    CORE_SWITCH_LABEL = "the core network switch"
    CORE_SWITCH_POWER_ENTITY = "sensor.core_switch_power_watts"
    CORE_SWITCH_POWER_FLOOR = 1.0  # watts below this ⇒ treat as unpowered

    def __init__(self, hass, *, honorific: str = "sir") -> None:
        self.hass = hass
        self.honorific = honorific

    # ── Individual checks (each fully guarded) ────────────────────────────
    def _eval_threshold(self, check: _ThresholdCheck) -> Finding | None:
        try:
            state = self.hass.states.get(check.entity_id)
            if state is None or str(state.state).lower() in _UNKNOWN_STATES:
                return Finding(
                    _SEV_WARNING,
                    f"I can't read {check.label} — that sensor is unavailable",
                    check.label,
                )
            pct = _as_float(state.state)
            if pct is None:
                return Finding(
                    _SEV_WARNING,
                    f"{check.label} is reporting an unreadable value",
                    check.label,
                )
            if pct > check.critical_above:
                return Finding(
                    _SEV_CRITICAL,
                    f"{check.label} is critically high at {pct:.0f} {check.unit}",
                    check.label,
                )
            if pct > check.warn_above:
                return Finding(
                    _SEV_WARNING,
                    f"{check.label} is elevated at {pct:.0f} {check.unit}",
                    check.label,
                )
            return None
        except Exception:  # noqa: BLE001 - never let one probe abort the audit
            _LOGGER.exception("Triage threshold check failed for %s", check.entity_id)
            return None

    def _eval_binary(self, check: _BinaryCheck) -> Finding | None:
        try:
            state = self.hass.states.get(check.entity_id)
            if state is None:
                return Finding(
                    _SEV_WARNING,
                    f"{check.label} is not reporting to Home Assistant",
                    check.label,
                )
            value = str(state.state).lower()
            if value in _UNKNOWN_STATES:
                return Finding(
                    _SEV_WARNING,
                    f"{check.label} has dropped offline and can't be reached",
                    check.label,
                )
            if value == check.bad_state.lower():
                return Finding(
                    _SEV_CRITICAL,
                    f"{check.label} is reporting offline",
                    check.label,
                )
            return None
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Triage binary check failed for %s", check.entity_id)
            return None

    # ── Aggregation ───────────────────────────────────────────────────────
    def evaluate(self) -> dict:
        """Run every probe and synthesise the verdict.

        Returns a dict with:
            alert_required (bool) – any finding at warning severity or above
            message        (str)  – a single natural-language summary ('' if clear)
            critical       (bool) – any finding at critical severity
        """
        findings: list[Finding] = []
        for tcheck in self.THRESHOLD_CHECKS:
            if (f := self._eval_threshold(tcheck)) is not None:
                findings.append(f)
        for bcheck in self.BINARY_CHECKS:
            if (f := self._eval_binary(bcheck)) is not None:
                findings.append(f)

        if not findings:
            return {"alert_required": False, "message": "", "critical": False, "tags": []}

        # Walk root-cause dependency trees to turn bare symptoms into cause/effect.
        self._enrich_root_cause(findings)

        critical = any(f.severity >= _SEV_CRITICAL for f in findings)
        # Speak the most severe items first.
        findings.sort(key=lambda f: f.severity, reverse=True)
        message = self._compose(findings, critical)
        # De-duplicated finding labels, for memory recall / commit.
        tags: list[str] = []
        for f in findings:
            if f.label and f.label not in tags:
                tags.append(f.label)
        return {"alert_required": True, "message": message, "critical": critical, "tags": tags}

    def _enrich_root_cause(self, findings: list[Finding]) -> None:
        """Inspect cross-entity dependencies to deduce why a fault occurred and
        fold the deduction into the finding's spoken clause. Currently models the
        core switch ← upstream power monitor relationship."""
        for f in findings:
            if f.label != self.CORE_SWITCH_LABEL or f.severity < _SEV_CRITICAL:
                continue
            state = self.hass.states.get(self.CORE_SWITCH_POWER_ENTITY)
            watts = _as_float(state.state) if state is not None else None
            if state is None or str(state.state).lower() in _UNKNOWN_STATES or watts is None:
                f.phrase += (
                    ", and its upstream power monitor is unreachable too — "
                    "most likely an upstream power loss on its utility circuit"
                )
            elif watts < self.CORE_SWITCH_POWER_FLOOR:
                f.phrase += (
                    f", drawing only {watts:.0f} watts upstream — consistent with "
                    "a power loss on its utility circuit rather than the switch itself failing"
                )
            else:
                f.phrase += (
                    f", though it's still drawing {watts:.0f} watts upstream, so this "
                    "points to a network or uplink fault rather than a power loss"
                )

    def _compose(self, findings: list[Finding], critical: bool) -> str:
        honorific = self.honorific.title()
        clauses = [f.phrase for f in findings]

        if len(clauses) == 1:
            body = clauses[0]
        elif len(clauses) == 2:
            body = f"{clauses[0]}, and {clauses[1]}"
        else:
            body = ", ".join(clauses[:-1]) + f", and {clauses[-1]}"

        if critical:
            lead = f"{honorific}, infrastructure attention is required."
        else:
            lead = f"{honorific}, a minor infrastructure note."
        # Capitalise the first clause for a clean sentence.
        body = body[0].upper() + body[1:] if body else body
        return f"{lead} {body}."
