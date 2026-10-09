"""Phase N enforce — kernel-governed graduated autonomy (owner-gated, opt-in).

When the household opts in (`autonomy_enforce`, written only after the panel's
confirmation screen), a granted proactive pattern whose capability is
SECURITY-class never auto-acts — the kernel autonomy ladder's hard ceiling.
Default OFF is behaviour-preserving; SENSITIVE patterns (lights/hvac) are
unaffected; fail-safe leaves the legacy gate (grant + mode flag) untouched.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def modes_auto_on(load, monkeypatch):
    modes = load("modes")
    monkeypatch.setattr(modes, "mode_allows_auto_actions", lambda: True)
    return modes


def _granted(am, key):
    am._grants[key] = {"granted": True, "approvals": 5}


# ── the security-ceiling helper ──────────────────────────────────────────────
def test_pattern_is_security(cc, monkeypatch):
    monkeypatch.setitem(cc._PATTERN_CAPABILITY, "unlock_front", "lock.unlock")  # SECURITY
    assert cc._pattern_is_security("unlock_front:door") is True
    assert cc._pattern_is_security("lights_on_when_dark:den") is False          # SENSITIVE
    assert cc._pattern_is_security("totally_unknown:x") is False                # fail-safe


def test_enforce_off_by_default(cc):
    assert cc._graduated_autonomy_enforce_on() is False


def test_enforce_on_via_config(cc, cfg):
    cfg["autonomy_enforce"] = True
    assert cc._graduated_autonomy_enforce_on() is True


# ── the live gate ────────────────────────────────────────────────────────────
def test_security_pattern_blocked_only_under_enforce(cc, cfg, modes_auto_on, monkeypatch):
    monkeypatch.setitem(cc._PATTERN_CAPABILITY, "unlock_front", "lock.unlock")
    am = cc.AutonomyManager()
    _granted(am, "unlock_front:door")
    # Default OFF: a granted pattern with the mode flag on is autonomous (legacy).
    assert am.is_autonomous("unlock_front:door") is True
    # Opt-in ON: the kernel security ceiling blocks it — always ask.
    cfg["autonomy_enforce"] = True
    assert am.is_autonomous("unlock_front:door") is False


def test_sensitive_pattern_unaffected_under_enforce(cc, cfg, modes_auto_on):
    am = cc.AutonomyManager()
    _granted(am, "lights_on_when_dark:den")        # light → SENSITIVE, not pinned
    cfg["autonomy_enforce"] = True
    assert am.is_autonomous("lights_on_when_dark:den") is True


def test_ungranted_never_autonomous(cc, cfg, modes_auto_on):
    am = cc.AutonomyManager()
    cfg["autonomy_enforce"] = True
    assert am.is_autonomous("lights_on_when_dark:den") is False   # no grant
