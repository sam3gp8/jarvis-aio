"""Phase K enforce — attention arbitration reads the canonical working set.

When WORKING_MEMORY_ENFORCE (or the ``working_memory_enforce`` config key) is on,
output_gate's announce gate consults the shared kernel WorkingMemory and may
WITHHOLD a non-critical announcement the legacy gate allowed, when the working
set's knowledge (the household is asleep) is exactly what tips the kernel
attention arbitration from ALLOW to DEFER/SUPPRESS. Default OFF is
behaviour-preserving; the gate is tighten-only (never surfaces a withheld
announcement), critical always bypasses, and any error / unpopulated working set
falls back to the legacy decision (fail-safe = current attention inputs).
Non-safety: gates only the announce surface.
"""
import pytest


@pytest.fixture
def og(load, monkeypatch):
    m = load("output_gate")
    # Hermetic gate: neutralise any persisted mute file (a live-home
    # output_mutes.json with all:true would otherwise blanket-block the gate) and
    # start from a clean in-memory state.
    monkeypatch.setattr(m, "_load_mutes", lambda: None)
    monkeypatch.setattr(m, "_STATE", m.GateState())
    return m


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def wm(load):
    # The SAME module instance output_gate reads via `from .kernel import
    # working_memory` (both resolve to jc.kernel.working_memory).
    m = load("kernel.working_memory")
    m.reset_shared()
    yield m
    m.reset_shared()


# ── the enforce switch ────────────────────────────────────────────────────────
def test_enforce_off_by_default(og, cfg):
    assert og._working_memory_enforce_on() is False


def test_enforce_on_via_config(og, cfg):
    cfg["working_memory_enforce"] = True
    assert og._working_memory_enforce_on() is True


def test_enforce_on_via_flag(og, monkeypatch):
    monkeypatch.setattr(og, "WORKING_MEMORY_ENFORCE", True)
    assert og._working_memory_enforce_on() is True


# ── the pure gate decision ──────────────────────────────────────────────────
def test_gate_withholds_when_working_set_tips_it(og):
    # Legacy allowed, non-critical, blind=ALLOW, informed=blocked → withhold.
    assert og._working_memory_gates_out(
        allowed=True, urgency="normal",
        blind_allowed=True, informed_allowed=False) is True


def test_gate_noop_when_legacy_already_blocked(og):
    # Nothing to withhold if the legacy gate already blocked it.
    assert og._working_memory_gates_out(
        allowed=False, urgency="normal",
        blind_allowed=True, informed_allowed=False) is False


def test_gate_never_touches_critical(og):
    assert og._working_memory_gates_out(
        allowed=True, urgency="critical",
        blind_allowed=True, informed_allowed=False) is False


def test_gate_noop_when_working_set_does_not_tip_it(og):
    # Informed still allows → the working set's knowledge changed nothing.
    assert og._working_memory_gates_out(
        allowed=True, urgency="normal",
        blind_allowed=True, informed_allowed=True) is False


def test_gate_is_tighten_only(og):
    # Blind already blocked (the baseline, not the working set, is the cause) →
    # the working-set rung does not act; it only ever withholds on its own signal.
    assert og._working_memory_gates_out(
        allowed=True, urgency="normal",
        blind_allowed=False, informed_allowed=False) is False


def test_gate_noop_when_not_allowed_regardless_of_urgency(og):
    # A falsey `allowed` short-circuits before anything else is consulted.
    assert og._working_memory_gates_out(
        allowed=None, urgency="normal",
        blind_allowed=True, informed_allowed=False) is False


# ── end-to-end through the gate ──────────────────────────────────────────────
def _asleep(wm):
    """Populate the shared working set so informed arbitration defers a NORMAL
    announcement (household asleep → quiet hours)."""
    wm.shared().remember(wm.KIND_SITUATION, "home_occupancy",
                         content="2 home — asleep", salience=0.9, pinned=True)


def test_default_off_is_behaviour_preserving(og, cfg, wm):
    _asleep(wm)
    # Enforce OFF: a normal announcement the legacy gate allows still goes through.
    allowed, _reason = og._can_announce_with_multiplier(
        1.0, entity_id="sensor.x", category="info", urgency="normal",
        message="unique message one", max_per_hour=10)
    assert allowed is True


def test_enforce_withholds_when_asleep(og, cfg, wm):
    _asleep(wm)
    cfg["working_memory_enforce"] = True
    allowed, reason = og._can_announce_with_multiplier(
        1.0, entity_id="sensor.x", category="info", urgency="normal",
        message="unique message two", max_per_hour=10)
    assert allowed is False
    assert "working memory" in reason


def test_enforce_still_allows_critical_when_asleep(og, cfg, wm):
    _asleep(wm)
    cfg["working_memory_enforce"] = True
    allowed, _reason = og._can_announce_with_multiplier(
        1.0, entity_id="sensor.x", category="alarm", urgency="critical",
        message="critical message", max_per_hour=10)
    assert allowed is True


def test_enforce_failsafe_when_working_set_empty(og, cfg, wm):
    # Nothing to consult → legacy decision stands.
    cfg["working_memory_enforce"] = True
    allowed, _reason = og._can_announce_with_multiplier(
        1.0, entity_id="sensor.x", category="info", urgency="normal",
        message="unique message three", max_per_hour=10)
    assert allowed is True


def test_enforce_allows_when_awake(og, cfg, wm):
    wm.shared().remember(wm.KIND_SITUATION, "home_occupancy",
                         content="2 home — awake", salience=0.9, pinned=True)
    cfg["working_memory_enforce"] = True
    allowed, _reason = og._can_announce_with_multiplier(
        1.0, entity_id="sensor.x", category="info", urgency="normal",
        message="unique message four", max_per_hour=10)
    assert allowed is True
