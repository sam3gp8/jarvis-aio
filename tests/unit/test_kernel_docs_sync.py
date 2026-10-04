"""MCU Phase H (H0): the doc ↔ implementation consistency gate.

`scripts/kernel_docs_sync.py` keeps the Constitution's enforcement ledger and the
KERNEL_ADOPTION matrix generated from `kernel_adoption._DECLARED`, and fails CI when
either doc describes a different stage than the implementation declares — the exact
governance drift the gap-audit flagged (the Constitution had claimed authority was
log-only after it was flipped to enforce).

The script imports only stdlib + kernel_adoption and reads real files, so it's
exercised directly rather than through the `jc` harness.
"""
import importlib.util
import pathlib

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def ds():
    spec = importlib.util.spec_from_file_location(
        "kernel_docs_sync", _SCRIPTS / "kernel_docs_sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_docs_are_in_sync(ds):
    # The committed Constitution ledger + adoption matrix must match the
    # implementation. If this fails, run `kernel_docs_sync.py --write`.
    assert ds.check() == []


def test_ledger_lists_every_primitive_with_a_stage(ds):
    block = ds.generate_ledger_block()
    import kernel_adoption as KA
    rows = KA.scan()
    for prim in rows:
        assert f"| `{prim}` |" in block
    # authority and the other flips must read as enforce in the generated ledger.
    assert "| `authority` | ● enforce |" in block
    assert "| `world_model` | ● enforce |" in block


def test_extract_replace_roundtrip(ds):
    begin, end = ds._LEDGER_BEGIN, ds._LEDGER_END
    text = f"intro\n{begin}\nOLD\n{end}\ntail\n"
    assert ds._extract_block(text, begin, end) == "OLD"
    out = ds._replace_block(text, begin, end, "NEW BODY")
    assert ds._extract_block(out, begin, end) == "NEW BODY"
    assert out.startswith("intro") and out.rstrip().endswith("tail")


def test_check_file_detects_stale_block(ds, tmp_path):
    begin, end = ds._LEDGER_BEGIN, ds._LEDGER_END
    f = tmp_path / "doc.md"
    f.write_text(f"{begin}\n| `authority` | ◑ parity |\n{end}\n")   # stale!
    problems = ds._check_file(f, begin, end, ds.generate_ledger_block(), "ledger")
    assert problems and "STALE" in problems[0]


def test_check_file_detects_missing_markers(ds, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("no markers here")
    problems = ds._check_file(f, ds._LEDGER_BEGIN, ds._LEDGER_END,
                              ds.generate_ledger_block(), "ledger")
    assert problems and "missing generated block" in problems[0]
