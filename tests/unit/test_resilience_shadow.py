"""Phase Z shadow — connectivity's circuit breaker folds each transition (cloud
reachable ↔ not) into the kernel resilience model and logs the would-be
compute-tier policy, observe-only.

`_emit_resilience_shadow` drives nothing: the breaker's own CLOSED/OPEN verdict is
unchanged; this only adds a `resilience(shadow)` log line. These tests pin the
binder — that a real open/recovery transition fires it (and a non-transition does
not), the local-first / offline-safe / cloud-as-disposable-offload policy it logs,
and the two kill-switches. The resilience model's own derivations are covered in
test_kernel_resilience.
"""
import logging

import pytest


@pytest.fixture
def conn(connectivity):
    # fresh breaker each test (module state is process-local)
    connectivity.reset()
    connectivity._BREAKER.consecutive_failures = 0
    connectivity._BREAKER.total_failures = 0
    connectivity._BREAKER.total_successes = 0
    return connectivity


# ── transitions fire the shadow (via a recorder) ────────────────────────────
def test_open_transition_fires_offline(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(conn, "_emit_resilience_shadow",
                        lambda **k: calls.append(k))
    conn.record_failure()
    conn.record_failure()   # second consecutive failure opens the breaker
    assert conn.is_offline() is True
    assert calls == [{"cloud_online": False}]


def test_recovery_transition_fires_online(conn, monkeypatch):
    conn.record_failure()
    conn.record_failure()   # open
    calls = []
    monkeypatch.setattr(conn, "_emit_resilience_shadow",
                        lambda **k: calls.append(k))
    conn.record_success()   # recovery → breaker closes
    assert conn.is_online() is True
    assert calls == [{"cloud_online": True}]


def test_no_emit_without_transition(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(conn, "_emit_resilience_shadow",
                        lambda **k: calls.append(k))
    # a single failure (below threshold) does not open the breaker → no transition
    conn.record_failure()
    assert conn.is_online() is True
    # a success while already closed is not a recovery transition
    conn.record_success()
    assert calls == []


def test_no_duplicate_emit_while_already_open(conn, monkeypatch):
    conn.record_failure()
    conn.record_failure()   # opens
    calls = []
    monkeypatch.setattr(conn, "_emit_resilience_shadow",
                        lambda **k: calls.append(k))
    conn.record_failure()   # still open — not a fresh transition into open
    assert calls == []


# ── the policy the emitter logs ──────────────────────────────────────────────
def _summary_from_caplog(caplog):
    for rec in caplog.records:
        if rec.getMessage().startswith("resilience(shadow):"):
            # logging special-cases a lone dict arg: rec.args IS the dict, not a
            # 1-tuple holding it.
            return rec.args if isinstance(rec.args, dict) else rec.args[0]
    return None


def test_emitter_online_offers_cloud_offload(conn, caplog):
    with caplog.at_level(logging.INFO):
        conn._emit_resilience_shadow(cloud_online=True)
    s = _summary_from_caplog(caplog)
    assert s is not None
    assert s["chosen"] == "haos-local"      # local-first, always
    assert s["offline_safe"] is True        # the brain is home
    assert s["offload_targets"] == ["cloud-llm"]   # cloud disposable, when healthy
    assert s["down"] == 0


def test_emitter_offline_keeps_brain_home(conn, caplog):
    with caplog.at_level(logging.INFO):
        conn._emit_resilience_shadow(cloud_online=False)
    s = _summary_from_caplog(caplog)
    assert s is not None
    assert s["chosen"] == "haos-local"      # still runs, at home
    assert s["offline_safe"] is True        # offline-safe even with cloud down
    assert s["offload_targets"] == []       # nothing to offload to
    assert s["down"] == 1


# ── kill-switches ────────────────────────────────────────────────────────────
def test_kill_switch_module_flag(conn, caplog, monkeypatch):
    monkeypatch.setattr(conn, "RESILIENCE_SHADOW", False)
    with caplog.at_level(logging.INFO):
        conn._emit_resilience_shadow(cloud_online=True)
    assert _summary_from_caplog(caplog) is None


def test_kill_switch_config_key(conn, caplog, monkeypatch):
    import jc.jarvis_config as cfg
    monkeypatch.setattr(
        cfg, "get",
        lambda key, default=None: False if key == "resilience_shadow" else default)
    with caplog.at_level(logging.INFO):
        conn._emit_resilience_shadow(cloud_online=True)
    assert _summary_from_caplog(caplog) is None
