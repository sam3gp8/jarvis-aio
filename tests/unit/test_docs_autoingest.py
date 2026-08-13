"""Tests for automatic document ingestion (v6.79.0).

The scheduler lives in __init__ (can't run headless), so these cover the
incremental logic that carries the weight: auto_ingest_new must ingest a new
file, skip an unchanged one (so a timer doesn't re-embed the library every
run), re-ingest a changed one, and never raise. Ingestion itself is stubbed."""
import pathlib
import sqlite3
import time

import pytest


@pytest.fixture
def docs(load, tmp_path, monkeypatch):
    m = load("documents")
    # isolate the docs dir + the seen-table DB
    d = tmp_path / "docs"
    d.mkdir()
    monkeypatch.setattr(m, "DOCS_DIR", str(d))
    monkeypatch.setattr(m, "_DB_PATH", str(tmp_path / "jarvis.db"))
    # stub the actual ingest so we test the scan/skip logic, not embedding
    calls = []
    async def _fake_ingest(hass, path):
        calls.append(path)
        return {"ok": True, "source": pathlib.Path(path).name, "chunks": 3}
    monkeypatch.setattr(m, "save_and_ingest_upload_from_path", _fake_ingest)
    m._ingest_calls = calls
    m._docs_dir = d
    return m


def _write(d, name, text="hello"):
    p = d / name
    p.write_text(text)
    return p


async def test_ingests_a_new_file(docs):
    _write(docs._docs_dir, "manual.txt")
    res = await docs.auto_ingest_new(None)
    assert res["new_files"] == 1
    assert docs._ingest_calls              # ingest was actually called


async def test_skips_unchanged_file_on_rescan(docs):
    _write(docs._docs_dir, "manual.txt")
    await docs.auto_ingest_new(None)             # first scan ingests
    docs._ingest_calls.clear()
    res = await docs.auto_ingest_new(None)       # second scan: nothing new
    assert res["new_files"] == 0
    assert docs._ingest_calls == []              # NOT re-ingested


async def test_reingests_changed_file(docs):
    p = _write(docs._docs_dir, "manual.txt")
    await docs.auto_ingest_new(None)
    docs._ingest_calls.clear()
    # change the file + bump its mtime past the 1s threshold
    time.sleep(0.01)
    p.write_text("new content")
    import os
    future = time.time() + 5
    os.utime(p, (future, future))
    res = await docs.auto_ingest_new(None)
    assert res["new_files"] == 1                 # change detected → re-ingested


async def test_ignores_unsupported_extensions(docs):
    _write(docs._docs_dir, "photo.jpg")
    _write(docs._docs_dir, "notes.md")
    res = await docs.auto_ingest_new(None)
    assert res["new_files"] == 1                 # only the .md
    assert any("notes.md" in c for c in docs._ingest_calls)
    assert not any("photo.jpg" in c for c in docs._ingest_calls)


async def test_missing_docs_dir_is_safe(docs, monkeypatch):
    monkeypatch.setattr(docs, "DOCS_DIR", "/nonexistent/path/xyz")
    res = await docs.auto_ingest_new(None)
    assert res["ok"] is True
    assert res["new_files"] == 0


async def test_skips_oversized_files(docs, monkeypatch):
    monkeypatch.setattr(docs, "_MAX_FILE_MB", 0.0001)   # ~100 bytes
    _write(docs._docs_dir, "big.txt", "x" * 5000)
    res = await docs.auto_ingest_new(None)
    assert res["new_files"] == 0
