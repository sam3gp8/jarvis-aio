"""Governance enforcement registry — the owner's control surface for every
kernel *enforce* kill-switch.

Each kernel capability walks the shadow → parity → enforce ladder behind a
module-level ``*_ENFORCE`` flag. Those flags are the single points at which the
kernel becomes authoritative — or, for the safety rungs that already ship ON,
the kill-switch that reverts instantly to observe-only. This module is a
*registry* over them so the panel's Governance tab can show each switch, explain
what flipping it does and what the fail-safe is, report its live value, and let
the owner persist an override — **without changing how any consumer reads its
own flag**.

Design (minimally invasive + behaviour-preserving):

* Each :class:`Switch` records the target ``module`` + ``attr``, the shipped
  ``default``, a human ``name`` / ``explanation``, a ``category`` — ``safety``
  for the governance rungs that ship ON (turning one OFF reverts to observe-only)
  vs ``capability`` for the owner-opt-in rungs that ship OFF — and its roadmap
  ``phase``.
* :func:`apply_overrides` runs once at boot: for each switch with a stored
  override it sets the target module's attribute to the stored bool; switches
  with no stored override keep their code default. With nothing stored (the
  shipped state) **nothing changes** — the owner's home behaves exactly as before
  until they toggle something.
* :func:`set_switch` persists an override and updates the live attribute, so a
  panel toggle takes effect at once and survives a restart.
* :func:`current` reports each switch's live value for the panel to render.

Pure-ish and defensive: no Home Assistant import, every target import / setattr
and every config read is guarded, and nothing here raises into a caller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import import_module
from typing import Callable, List, Mapping, Optional

_LOGGER = logging.getLogger(__name__)

CATEGORY_SAFETY = "safety"          # ships ON; OFF reverts to observe-only
CATEGORY_CAPABILITY = "capability"  # ships OFF; owner opt-in makes kernel authoritative


@dataclass(frozen=True)
class Switch:
    """One governance enforce flag the owner can see and control."""

    key: str            # stable id used in config + the panel
    module: str         # package-relative module, e.g. "continuity"
    attr: str           # the flag attribute, e.g. "CONTINUITY_RESUME_ENFORCE"
    default: bool       # the shipped code default
    category: str       # CATEGORY_SAFETY | CATEGORY_CAPABILITY
    name: str
    explanation: str
    phase: str = ""

    @property
    def config_key(self) -> str:
        """Namespaced jarvis_config key holding the owner's override (if any)."""
        return f"enforce_override__{self.key}"


# The live enforce flags, verified present in the code (not planned/future rungs).
_REGISTRY: List[Switch] = [
    # ── Owner opt-in capabilities (ship OFF; flipping ON makes the kernel the
    #    authority for that capability — behaviour-changing, your call) ──────────
    Switch(
        key="continuity_resume", module="continuity",
        attr="CONTINUITY_RESUME_ENFORCE", default=False,
        category=CATEGORY_CAPABILITY, phase="I-B.4",
        name="Cognitive continuity resume",
        explanation=(
            "After a restart, resume from the cognitive snapshot — what JARVIS "
            "was thinking (intent, chosen plan, beliefs) — not just the goals it "
            "had committed to. OFF (default): commitment-only continuity. Safe to "
            "enable; today nothing announces the resume yet, so this is a no-op "
            "until a resume-announce step is wired."),
    ),
    Switch(
        key="agency_orchestration", module="agent",
        attr="AGENCY_ORCHESTRATION_ENFORCE", default=False,
        category=CATEGORY_CAPABILITY, phase="O",
        name="Multi-agent orchestration",
        explanation=(
            "Let the kernel be the authority source for delegated sub-agents — it "
            "can only *narrow* a sub-agent's tools, never widen them. OFF "
            "(default): the existing delegation path. Behaviour-identical today "
            "because JARVIS holds every capability, so the narrowing never bites "
            "until you scope its token."),
    ),
    Switch(
        key="knowledge_graph", module="knowledge",
        attr="KNOWLEDGE_GRAPH_ENFORCE", default=True,
        category=CATEGORY_CAPABILITY, phase="T",
        name="Knowledge-graph recall",
        explanation=(
            "Serve the knowledge prompt block from the typed knowledge graph "
            "(entities + relations): the recalled facts are expanded one hop to "
            "pull in directly-related facts. ON (enabled 8.158.0, after the graph "
            "earned parity against flat recall). Fail-safe: any failure falls back "
            "to flat recall, so this only ADDS context. Turn OFF to revert to "
            "plain flat recall."),
    ),
    # ── Live safety / governance rungs (ship ON; these are KILL-SWITCHES — turn
    #    one OFF only to revert that rung to observe-only in an emergency) ───────
    Switch(
        key="authority", module="authority_bridge",
        attr="AUTHORITY_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="G4",
        name="Capability authority",
        explanation=(
            "Enforce capability authority on actuating tools — the max-restriction "
            "belt, allowlist and kill-switch that stop an un-authorized action. ON "
            "(default) is the safe state; OFF reverts to parity (log-only) and "
            "removes that guard. Leave ON unless you are debugging."),
    ),
    Switch(
        key="agency_budget", module="actuation",
        attr="AGENCY_BUDGET_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="G1",
        name="Autonomy budget ceiling",
        explanation=(
            "Block autonomous actions that exceed the configured budget ceiling. "
            "ON (default) is the safe state; OFF reverts to shadow (log-only), so "
            "over-budget autonomous actions would no longer be blocked."),
    ),
    Switch(
        key="loop_detect", module="actuation",
        attr="LOOP_DETECT_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="G2",
        name="Loop / thrash suppression",
        explanation=(
            "Suppress thrashing or looping autonomous actuations (the same action "
            "firing repeatedly). ON (default) is the safe state; OFF reverts to "
            "shadow and such loops would no longer be suppressed."),
    ),
    Switch(
        key="safety_seam", module="actuation",
        attr="SAFETY_SEAM_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="H8",
        name="Safety actuator seam",
        explanation=(
            "Route safety-critical actuators through the policy-mode seam so the "
            "active mode's restrictions always apply. ON (default) is the safe "
            "state; OFF bypasses the seam for those actuators."),
    ),
    Switch(
        key="hazard_situation", module="cognitive_core",
        attr="HAZARD_SITUATION_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="R2",
        name="Freeze-hazard verdict",
        explanation=(
            "Let the kernel own the freeze-hazard verdict (it fails TOWARD "
            "alerting). ON (default) is the safe state; OFF reverts to the legacy "
            "pure path."),
    ),
    Switch(
        key="intrusion_gate", module="cognitive_core",
        attr="INTRUSION_GATE_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="R3",
        name="Intrusion verdict",
        explanation=(
            "Let the kernel own the intrusion verdict used to arm/alert. ON "
            "(default) is the safe state; OFF reverts to the legacy path."),
    ),
    Switch(
        key="delivery_situation", module="package_monitor",
        attr="DELIVERY_SITUATION_ENFORCE", default=True,
        category=CATEGORY_SAFETY, phase="R1",
        name="Delivery presence verdict",
        explanation=(
            "Let the kernel own per-camera delivery presence. ON (default) is the "
            "current behaviour; OFF reverts to the legacy per-camera flag."),
    ),
]

_BY_KEY = {s.key: s for s in _REGISTRY}


def all_switches() -> List[Switch]:
    """The registry, in display order (capabilities first, then safety rungs)."""
    return list(_REGISTRY)


def _resolve(module_name: str):
    """Import a target module by its package-relative name. Never raises."""
    try:
        return import_module(f".{module_name}", __package__)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("enforcement: cannot import %s: %s", module_name, exc)
        return None


def _cfg_get(key: str, default=None):
    try:
        from . import jarvis_config
        return jarvis_config.get(key, default)
    except Exception:  # pragma: no cover - defensive
        return default


def _cfg_set(key: str, value) -> None:
    from . import jarvis_config
    jarvis_config.set(key, value)


def live_value(sw: Switch) -> bool:
    """The flag's current live value (the target module attribute), or its code
    default if the module/attr can't be read. Never raises."""
    mod = _resolve(sw.module)
    if mod is None:
        return bool(sw.default)
    return bool(getattr(mod, sw.attr, sw.default))


def current() -> List[dict]:
    """A panel-ready description of every switch: identity, explanation, the
    shipped default, the live value, and whether an override is stored."""
    out: List[dict] = []
    for sw in _REGISTRY:
        override = _cfg_get(sw.config_key, None)
        out.append({
            "key": sw.key,
            "name": sw.name,
            "explanation": sw.explanation,
            "category": sw.category,
            "phase": sw.phase,
            "default": bool(sw.default),
            "enabled": live_value(sw),
            "overridden": override is not None,
        })
    return out


def apply_overrides() -> int:
    """Apply any stored owner overrides to the live flags (call once at boot).

    For each switch with a stored override, set the target module's attribute to
    the stored bool; switches with no override keep their code default. Returns
    how many overrides were applied. Never raises into the boot path — a single
    bad switch is skipped, the rest still apply.
    """
    applied = 0
    for sw in _REGISTRY:
        try:
            override = _cfg_get(sw.config_key, None)
            if override is None:
                continue
            mod = _resolve(sw.module)
            if mod is None:
                continue
            setattr(mod, sw.attr, bool(override))
            applied += 1
            _LOGGER.info(
                "JARVIS governance: %s = %s (owner override)",
                sw.attr, bool(override))
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("enforcement: skipping %s: %s", sw.key, exc)
    return applied


def set_switch(key: str, enabled: bool) -> Optional[dict]:
    """Persist an owner override for ``key`` and update the live flag at once.

    Returns the switch's refreshed :func:`current` entry, or None if the key is
    unknown. The write persists through ``jarvis_config`` (survives restarts) and
    sets the target module attribute so the change takes effect immediately.
    """
    sw = _BY_KEY.get(key)
    if sw is None:
        return None
    value = bool(enabled)
    _cfg_set(sw.config_key, value)
    mod = _resolve(sw.module)
    if mod is not None:
        try:
            setattr(mod, sw.attr, value)
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.debug("enforcement: set %s failed: %s", sw.attr, exc)
    _LOGGER.info("JARVIS governance: %s set to %s via panel", sw.attr, value)
    for entry in current():
        if entry["key"] == key:
            return entry
    return None
