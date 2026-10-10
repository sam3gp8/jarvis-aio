"""Phase V parity — durable long-horizon ledger + resume-after-restart proof.

continuity persists the live goals (modeled as kernel.long_horizon goals, with
milestone progress) to a durable ledger on each capture, and on boot RESUMES them
from that ledger and compares their progress against the goals re-derived from
the live store. The parity logs how many goals resumed with identical progress —
the resume-after-restart proof. Observe-only; nothing resumes FROM the ledger yet.
"""
import logging

import pytest


@pytest.fixture
def cont(load, tmp_path, monkeypatch):
    mod = load("continuity")
    # Redirect every config_path_str the binder resolves to its own tmp file.
    def _path(*a, **k):
        name = a[1] if len(a) > 1 else k.get("name", "x")
        return str(tmp_path / name)
    monkeypatch.setattr(mod, "config_path_str", _path)
    return mod


def _goals(cont, monkeypatch, rows):
    """Stub goals.active() to return the given capture rows."""
    from jc import goals as g
    monkeypatch.setattr(g, "active", lambda: rows)


def _goal(gid, *steps):
    # steps: (label, status) tuples
    return {"id": gid, "title": f"goal {gid}",
            "steps": [{"step": lbl, "status": st} for lbl, st in steps]}


# ── the durable ledger round-trips ───────────────────────────────────────────
def test_persist_then_load_reconstructs_progress(cont, monkeypatch):
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "active"), ("c", "pending"))])
    n = cont._long_horizon_persist()
    assert n == 1
    loaded = cont._long_horizon_load(now=0.0)
    assert len(loaded) == 1
    g = loaded[0]
    assert g.id == "goal:g1"
    assert g.total == 3
    assert g.resolved == 1          # only the 'done' milestone is resolved
    assert abs(g.progress - (1 / 3)) < 1e-6


def test_load_missing_ledger_is_empty(cont):
    assert cont._long_horizon_load(now=0.0) == []


# ── resume-after-restart proof ───────────────────────────────────────────────
def test_parity_resume_matches_when_progress_survives(cont, monkeypatch, caplog):
    # Capture a goal, persist the ledger (simulate the pre-restart snapshot)...
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "pending"))])
    cont._long_horizon_persist()
    # ...then "reboot" with the live store carrying the SAME progress.
    with caplog.at_level(logging.DEBUG):
        cont._long_horizon_parity()
    line = next(r.message for r in caplog.records if "long_horizon(parity)" in r.message)
    assert "ledger=1" in line and "live=1" in line
    assert "resumed=1/1" in line


def test_parity_flags_progress_drift(cont, monkeypatch, caplog):
    # Ledger has the goal at 1/2 done...
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "pending"))])
    cont._long_horizon_persist()
    # ...but the live store now shows BOTH steps done (progress advanced past the
    # ledger) → shared goal, but not a progress match.
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "done"))])
    with caplog.at_level(logging.DEBUG):
        cont._long_horizon_parity()
    line = next(r.message for r in caplog.records if "long_horizon(parity)" in r.message)
    assert "resumed=0/1" in line


def test_parity_counts_new_and_dropped(cont, monkeypatch, caplog):
    # Ledger: g1, g2. Live now: g2, g3 → g3 new, g1 dropped, g2 shared.
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done")), _goal("g2", ("a", "pending"))])
    cont._long_horizon_persist()
    _goals(cont, monkeypatch, [_goal("g2", ("a", "pending")), _goal("g3", ("a", "pending"))])
    with caplog.at_level(logging.DEBUG):
        cont._long_horizon_parity()
    line = next(r.message for r in caplog.records if "long_horizon(parity)" in r.message)
    assert "new=1" in line and "dropped=1" in line and "resumed=1/1" in line


# ── kill-switch + defensiveness ──────────────────────────────────────────────
def test_parity_kill_switch_silences(cont, monkeypatch, caplog):
    monkeypatch.setattr(cont, "LONG_HORIZON_PARITY", False)
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"))])
    cont._long_horizon_persist()
    with caplog.at_level(logging.DEBUG):
        cont._long_horizon_parity()
    assert not any("long_horizon(parity)" in r.message for r in caplog.records)


def test_parity_without_ledger_logs_zero(cont, monkeypatch, caplog):
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"))])
    with caplog.at_level(logging.DEBUG):
        cont._long_horizon_parity()   # no ledger written yet
    line = next(r.message for r in caplog.records if "long_horizon(parity)" in r.message)
    assert "ledger=0" in line and "live=1" in line and "resumed=0/0" in line


def test_persist_is_defensive_on_failing_goals(cont, monkeypatch):
    from jc import goals as g
    def boom():
        raise RuntimeError("goals down")
    monkeypatch.setattr(g, "active", boom)
    assert cont._long_horizon_persist() == 0     # never raises
