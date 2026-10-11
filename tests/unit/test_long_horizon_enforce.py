"""Phase V enforce — the durable ledger is the authoritative boot continuity view.

When LONG_HORIZON_ENFORCE is on, `continuity._long_horizon_resume_line` surfaces the
in-flight multi-day goals being resumed FROM the ledger (titles, progress, next
milestone) — a boot continuity view nothing surfaced before. Non-actuating and
fail-safe: no ledger / nothing in flight / any error → "" (today's silent boot).
The parity + ledger round-trip stay covered by test_long_horizon_parity.
"""
import pytest


@pytest.fixture
def cont(load, tmp_path, monkeypatch):
    mod = load("continuity")

    def _path(*a, **k):
        name = a[1] if len(a) > 1 else k.get("name", "x")
        return str(tmp_path / name)

    monkeypatch.setattr(mod, "config_path_str", _path)
    return mod


def _goals(cont, monkeypatch, rows):
    from jc import goals as g
    monkeypatch.setattr(g, "active", lambda: rows)


def _goal(gid, *steps):
    return {"id": gid, "title": f"goal {gid}",
            "steps": [{"step": lbl, "status": st} for lbl, st in steps]}


# ── the authoritative resume line ────────────────────────────────────────────
def test_resume_line_surfaces_in_flight_goals(cont, monkeypatch):
    # persist a ledger with one in-flight goal (1 of 3 milestones done)
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "active"), ("c", "pending"))])
    assert cont._long_horizon_persist() == 1
    line = cont._long_horizon_resume_line()
    assert line.startswith("resuming 1 multi-day goal(s):")
    assert "goal g1" in line
    assert "33%" in line           # 1/3 resolved
    assert "next: b" in line        # first unresolved milestone


def test_resume_line_omits_complete_goals(cont, monkeypatch):
    # a fully-resolved goal is not "in flight" → nothing to resume
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "done"))])
    assert cont._long_horizon_persist() == 1
    assert cont._long_horizon_resume_line() == ""


def test_resume_line_empty_without_ledger(cont):
    # no ledger written → fail-safe empty (today's silent boot)
    assert cont._long_horizon_resume_line() == ""


def test_boot_summary_logs_resume_line(cont, monkeypatch, caplog):
    import logging
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "pending"))])
    cont._long_horizon_persist()
    with caplog.at_level(logging.INFO):
        cont.boot_summary()
    assert any(r.getMessage().startswith("JARVIS (long-horizon):")
               for r in caplog.records)


# ── kill-switches ────────────────────────────────────────────────────────────
def test_enforce_flag_default_on(cont):
    assert cont.LONG_HORIZON_ENFORCE is True
    assert cont._long_horizon_enforce_enabled() is True


def test_enforce_kill_switch_module_flag(cont, monkeypatch):
    _goals(cont, monkeypatch, [_goal("g1", ("a", "done"), ("b", "pending"))])
    cont._long_horizon_persist()
    monkeypatch.setattr(cont, "LONG_HORIZON_ENFORCE", False)
    assert cont._long_horizon_enforce_enabled() is False
    assert cont._long_horizon_resume_line() == ""


def test_enforce_kill_switch_config_key(cont, monkeypatch):
    import jc.jarvis_config as cfg
    monkeypatch.setattr(
        cfg, "get",
        lambda key, default=None: False if key == "long_horizon_enforce" else default)
    assert cont._long_horizon_enforce_enabled() is False


def test_resume_line_never_raises(cont, monkeypatch):
    # a broken loader is swallowed → "" (fail-safe), never propagated into boot
    monkeypatch.setattr(cont, "_long_horizon_load",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cont._long_horizon_resume_line() == ""
