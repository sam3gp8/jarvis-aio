"""Tests for the behavioral kernel-coverage matrix (MCU audit P0.5).

The script imports only stdlib and reads real source files, so it's exercised
directly rather than through the `jc` harness.
"""
import importlib.util
import pathlib

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "kernel_coverage.py")


@pytest.fixture(scope="module")
def kc():
    spec = importlib.util.spec_from_file_location("kernel_coverage", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scan_covers_every_path_and_contract(kc):
    rows = kc.scan()
    assert set(rows) == set(kc._PATHS)
    for contracts in rows.values():
        assert set(contracts) == set(kc._CONTRACTS)


def test_no_evidence_drift(kc):
    # Every declared non-'none' cell must have its evidence present in source.
    assert kc.drift() == []


def test_coverage_is_a_sane_percentage(kc):
    pct = kc.coverage_pct()
    assert 0.0 <= pct <= 100.0
    # Today it is deliberately low — most paths are still legacy.
    assert pct < 50.0


def test_control_device_authority_and_verify_declared(kc):
    rows = kc.scan()
    assert rows["control_device"]["authority"] == "parity"
    assert rows["control_device"]["verify"] == "full"
    # 8.30.0 (MCU Phase A): pre-action context read routes through WorldModel.
    assert rows["control_device"]["world_model"] == "parity"
    # 8.31.0 (MCU Phase A): the verify step produces a canonical ActuatorOutcome.
    assert rows["control_device"]["outcome"] == "full"
    # 8.32.0 (MCU Phase A): the actuation publishes a canonical JarvisEvent.
    assert rows["control_device"]["event"] == "parity"
    # B4b: control_device's actuation routes through the kernel planner.
    assert rows["control_device"]["plan"] == "full"
    # homer is read-only: its row carries no kernel-wired stage at all (every
    # cell is N/A, which scans as the 'none' stage). See the dedicated homer test.
    assert set(rows["homer"].values()) == {"none"}


def test_goals_plan_full_and_event_parity(kc):
    # goals onto the spine: the kernel journal is now AUTHORITATIVE for a goal's
    # step state (H4b, _apply_journal_authority) — plan is full — and the
    # goal.transition events are parity.
    rows = kc.scan()
    assert rows["goals"]["plan"] == "full"
    assert rows["goals"]["event"] == "parity"
    # proactive (9.34.0) is now the real discretionary-autonomous device path and
    # routes through the universal actuation seam, so plan is full and event parity.
    assert rows["proactive"]["plan"] == "full"
    assert rows["proactive"]["event"] == "parity"
    # friday/homer add no event/plan wiring of their own.
    for p in ("friday", "homer"):
        assert rows[p]["event"] == "none" and rows[p]["plan"] == "none"


# ── 9.34.0: proactive re-pointed, friday authority parity, homer read-only ───

def test_proactive_points_at_cognitive_core_and_is_wired(kc):
    # The 'proactive' row names the REAL discretionary-autonomous device path
    # (cognitive_core._execute_action_data), not the proactive_audio TTS path it
    # used to point at. It routes through the universal seam, so plan is full and
    # the actuation event is published (parity); authority is recorded at parity.
    assert kc._PATHS["proactive"]["module"] == "cognitive_core.py"
    rows = kc.scan()
    assert rows["proactive"]["plan"] == "full"
    assert rows["proactive"]["event"] == "parity"
    assert rows["proactive"]["authority"] == "parity"
    # Verify-after-act for deterministic autonomous targets (9.36.0): the path
    # reads the target back and records the canonical outcome.
    assert rows["proactive"]["verify"] == "full"
    assert rows["proactive"]["outcome"] == "full"
    # Pre-action WorldModel context snapshot (9.37.0), same read as control_device.
    assert rows["proactive"]["world_model"] == "parity"
    # situation is the only N/A for this imperative path.
    assert kc._is_na("proactive", "situation")


def test_friday_authority_is_delegation_attributed_parity(kc):
    # FRIDAY's own row credits exactly ONE cell — authority at parity — because it
    # actuates only through its (authority-recording) grant set and the H6
    # delegation scope attributes each verdict to actor="friday". It claims no
    # other actuation contract (those are owned/counted on the grant-set rows).
    rows = kc.scan()
    assert rows["friday"]["authority"] == "parity"
    wired = {c: s for c, s in rows["friday"].items() if s != "none"}
    assert wired == {"authority": "parity"}


def test_homer_is_entirely_na_readonly(kc):
    # HOMER is read-only (actuating: False): every contract is N/A-by-design, so
    # it contributes zero applicable cells (neither numerator nor denominator).
    for contract in kc._CONTRACTS:
        assert kc._is_na("homer", contract), f"homer.{contract} should be N/A"
    assert kc._PATHS["homer"]["contracts"] == {}


def test_drift_detects_a_bad_declaration(kc, monkeypatch):
    # Inject a path claiming a stage with evidence that cannot be found.
    bad = dict(kc._PATHS)
    bad["phantom"] = {"module": "agent.py",
                      "contracts": {"authority": {"stage": "full",
                                                  "evidence": "__definitely_not_in_source__"}}}
    monkeypatch.setattr(kc, "_PATHS", bad)
    problems = kc.drift()
    assert any("phantom.authority" in p for p in problems)


def test_markdown_lists_paths_and_coverage(kc):
    md = kc.render_markdown()
    assert "control_device" in md and "Kernel coverage" in md
    for contract in kc._CONTRACTS:
        assert contract in md


# ── reconciled weighted vs honest figures (audit follow-up) ──────────────────

def test_strict_coverage_excludes_shadow(kc):
    weighted = kc.coverage_pct()
    strict = kc.strict_coverage_pct()
    assert 0.0 <= strict <= weighted <= 100.0
    # strict drops shadow's 1/3 credit, so it is strictly lower while any shadow
    # cell exists in the matrix (it does today).
    has_shadow = any(stage == "shadow"
                     for contracts in kc.scan().values()
                     for stage in contracts.values())
    if has_shadow:
        assert strict < weighted


def test_markdown_emits_headline_and_all_cell_figures(kc):
    md = kc.render_markdown()
    # The headline is the applicable figure; the all-84 weighted/honest pair
    # follows underneath for continuity.
    assert "applicable" in md and "weighted" in md and "honest" in md


def test_doc_block_in_sync_with_generator(kc):
    # The committed KERNEL_COVERAGE.md block must match the generator exactly.
    assert kc.doc_block_drift() == []


def test_doc_block_drift_detects_divergence(kc, monkeypatch):
    # If the generator and the embedded block disagree, --check must flag it.
    monkeypatch.setattr(kc, "render_markdown",
                        lambda rows=None: "| totally | different |\n**x**")
    problems = kc.doc_block_drift()
    assert problems and "stale" in problems[0]


# ── 8.34.0 governance gate ───────────────────────────────────────────────────

def test_governance_gate_satisfied(kc):
    # Every consequential agent tool is either a declared path or allowlisted.
    assert kc.undeclared_actuator_tools() == []


def test_known_actuators_are_declared_paths(kc):
    for tool in ("control_device", "bulk_control", "execute_plan",
                 "run_scene_or_script", "set_mode"):
        assert tool in kc._PATHS


def test_gate_catches_a_new_undeclared_actuator(kc, monkeypatch):
    # A new tool that is neither a path nor allowlisted must trip the gate.
    monkeypatch.setattr(kc, "_tool_map_names",
                        lambda _src: ["control_device", "launch_missiles"])
    problems = kc.undeclared_actuator_tools()
    assert any("launch_missiles" in p for p in problems)


def test_gate_accepts_allowlisted_tool(kc, monkeypatch):
    monkeypatch.setattr(kc, "_tool_map_names",
                        lambda _src: ["get_entity_state", "control_device"])
    assert kc.undeclared_actuator_tools() == []


def test_tool_map_names_parses_agent(kc):
    # The parser finds the real tool keys without importing agent.py.
    comp = kc._component_dir()
    names = kc._tool_map_names((comp / "agent.py").read_text())
    assert "control_device" in names and "get_entity_state" in names


# ── applicable-cell denominator + N/A ledger (metric reform) ─────────────────

def test_applicable_denominator_excludes_na(kc):
    total = len(kc._PATHS) * len(kc._CONTRACTS)
    na = len(kc.na_ledger())
    # Every N/A cell is excluded from the applicable denominator, exactly once.
    assert kc.applicable_cell_count() == total - na
    assert 0 < kc.applicable_cell_count() < total  # some, not all, are N/A


def test_applicable_coverage_is_the_honest_headline(kc):
    applicable = kc.applicable_coverage_pct()
    all_cells_honest = kc.strict_coverage_pct()
    assert 0.0 <= applicable <= 100.0
    # Same honest numerator over a smaller (applicable-only) denominator, so the
    # headline is strictly higher than the all-84 honest figure while any N/A
    # cell exists (one does today) — it is not diluted by meaningless cells.
    assert kc.na_ledger()  # guard the premise
    assert applicable > all_cells_honest


def test_na_cells_do_not_change_the_numerator(kc):
    # N/A cells are "none"-staged, so excluding them must leave the honest
    # numerator untouched — the reform changes the denominator, never the credit.
    denom = kc.applicable_cell_count()
    rows = kc.scan()
    got_applicable = sum(
        kc._STRICT_WEIGHT[rows[p][c]]
        for p in kc._PATHS for c in kc._CONTRACTS if not kc._is_na(p, c))
    got_all = sum(kc._STRICT_WEIGHT[rows[p][c]]
                  for p in kc._PATHS for c in kc._CONTRACTS)
    assert got_applicable == got_all
    assert kc.applicable_coverage_pct() == round(100.0 * got_all / denom, 1)


def test_every_na_cell_has_a_reason_and_is_a_real_contract(kc):
    for path, contract, reason in kc.na_ledger():
        assert contract in kc._CONTRACTS
        assert isinstance(reason, str) and reason.strip()


def test_na_and_wired_are_disjoint(kc):
    # A cell is wired XOR N/A — never declared as both.
    for path, spec in kc._PATHS.items():
        overlap = set(spec["contracts"]) & set(spec.get("na", {}))
        assert overlap == set(), f"{path}: {overlap} declared both wired and N/A"


def test_known_na_cells_are_present(kc):
    # The structurally-meaningless cells the reform documents must be N/A.
    assert kc._is_na("control_device", "situation")       # one-shot ≠ situation
    assert kc._is_na("bulk_control", "verify")            # fire-and-forget
    assert kc._is_na("intrusion", "authority")            # detection ≠ actuation
    assert kc._is_na("set_mode", "world_model")           # no single entity
    # ...and a genuine gap is NOT hidden as N/A: run_scene authority stays a gap.
    assert not kc._is_na("run_scene_or_script", "authority")
    assert not kc._is_na("goals", "plan")


def test_drift_catches_a_cell_declared_both_wired_and_na(kc, monkeypatch):
    bad = {k: dict(v) for k, v in kc._PATHS.items()}
    bad["control_device"] = {
        "module": "agent.py",
        "contracts": {"plan": {"stage": "full", "evidence": "aexecute_plan"}},
        "na": {"plan": "contradictory"},
    }
    monkeypatch.setattr(kc, "_PATHS", bad)
    problems = kc.drift()
    assert any("both wired and N/A" in p for p in problems)


def test_drift_catches_an_empty_na_reason(kc, monkeypatch):
    bad = {k: dict(v) for k, v in kc._PATHS.items()}
    bad["goals"] = {"module": "goals.py", "contracts": {},
                    "na": {"verify": "   "}}
    monkeypatch.setattr(kc, "_PATHS", bad)
    problems = kc.drift()
    assert any("non-empty reason" in p for p in problems)


def test_markdown_renders_na_glyph_and_ledger(kc):
    md = kc.render_markdown()
    assert kc._NA_ICON in md                      # the N/A glyph appears in the grid
    assert "N/A-by-design" in md                  # the ledger header
    # a specific ledger line with its reason is rendered
    assert "`control_device` · **situation**" in md
    assert "not a situation lifecycle" in md


def test_doc_block_still_in_sync_after_reform(kc):
    # The committed KERNEL_COVERAGE.md block (now incl. the ledger) matches.
    assert kc.doc_block_drift() == []
