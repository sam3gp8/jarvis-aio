"""KERNEL_COVERAGE.md prose must not drift from ground truth (audit Finding A).

The generated matrix block is already CI-checked by scripts/kernel_coverage.py
(it regenerates and compares). The *surrounding prose* is not — which is exactly
how it drifted: it still claimed `control_device` plan was shadow and that "no
primitive is at enforce" long after both became false. These guards tie the
load-bearing narrative claims back to code/matrix ground truth so they can't
silently go stale again.
"""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOC = ROOT / "KERNEL_COVERAGE.md"
ADOPTION = ROOT / "scripts" / "kernel_adoption.py"


def _doc():
    return DOC.read_text(encoding="utf-8")


def _adoption():
    spec = importlib.util.spec_from_file_location("kernel_adoption", ADOPTION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _control_device_cells():
    """The generated-matrix row for control_device as a list of 7 stage symbols
    [event, world_model, situation, authority, plan, verify, outcome]."""
    for line in _doc().splitlines():
        if line.strip().startswith("| `control_device`"):
            parts = [c.strip() for c in line.strip().strip("|").split("|")]
            return parts[1:]   # drop the path cell
    raise AssertionError("control_device row not found in the generated matrix")


def test_doc_does_not_deny_enforce():
    # Ground truth: at least one primitive is declared at enforce. So the doc
    # must not resurrect the (now false) "No primitive is at enforce" claim.
    mod = _adoption()
    enforced = [name for name, d in mod._DECLARED.items()
                if d.get("stage") == "enforce"]
    assert enforced, "expected at least one enforce primitive in kernel_adoption"
    assert "No primitive is at `enforce`" not in _doc(), (
        "KERNEL_COVERAGE.md denies enforce, but these primitives are enforced: "
        + ", ".join(sorted(enforced)))


def test_scoped_authority_enforce_is_live_and_acknowledged(load):
    # Ground truth: authority is enforced for a scoped allowlist (G4). The doc
    # must acknowledge that scoped flip, so it can't quietly revert to
    # "authority stays log-only".
    ab = load("authority_bridge")
    assert ab.AUTHORITY_ENFORCE is True
    assert ab.AUTHORITY_ENFORCE_CAPABILITIES, "allowlist unexpectedly empty"
    assert "G4" in _doc(), (
        "the scoped authority enforce flip (G4) is live but KERNEL_COVERAGE.md "
        "no longer references it")


def test_control_device_plan_cell_is_full_and_prose_agrees():
    # The matrix says control_device plan is ● (full) — execution routes through
    # the kernel planner. The prose must agree: no "still ahead" framing that
    # describes routing execution as future work.
    cells = _control_device_cells()
    assert cells[4] == "●", f"expected control_device plan ●, got {cells[4]!r}"
    assert "Still ahead: routing" not in _doc(), (
        "prose still frames routing execution through the planner as future "
        "work, but the matrix shows control_device plan at ● (done)")
    # And the Phase A paragraph must not call it a mere "plan expression ◐".
    assert "plan expression (`plan` ◐)" not in _doc()


def test_generated_block_is_intact():
    # Sanity: the auto-generated block markers and a plausible matrix are present,
    # so the prose edits didn't disturb the generated region.
    doc = _doc()
    assert "<!-- BEGIN kernel-coverage" in doc
    assert "<!-- END kernel-coverage -->" in doc
    assert re.search(r"Kernel coverage \(applicable\): \d+\.\d+%", doc)
