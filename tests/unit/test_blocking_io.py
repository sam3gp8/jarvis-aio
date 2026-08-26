"""Blocking-I/O hygiene: persisted reads happen once, not on every call — v7.49.1.

reasoning_cache.load() and ha_secrets._read_secrets() previously re-read their
files on every call from async hot paths, tripping HA's blocking-I/O detector.
Both now cache after the first read.
"""
from __future__ import annotations

import pytest


# ── reasoning_cache.load() is idempotent once loaded ─────────────────────────
def test_reasoning_cache_load_reads_once(load):
    rc = load("reasoning_cache")
    rc._cache = {"sig1": {"speak": True}}
    rc._loaded = True
    # Without the guard this would re-read the (absent) file and clear _cache.
    n = rc.load()
    assert n == 1
    assert rc._cache == {"sig1": {"speak": True}}


def test_reasoning_cache_stats_does_not_clobber_cache(load):
    rc = load("reasoning_cache")
    rc._cache = {"a": {"speak": False}, "b": {"speak": True}}
    rc._loaded = True
    rc.stats()                                   # calls load() internally
    assert set(rc._cache) == {"a", "b"}          # still intact


# ── ha_secrets caches the default secrets file ───────────────────────────────
def test_ha_secrets_caches_default_path(load, tmp_path, monkeypatch):
    hs = load("ha_secrets")
    p = tmp_path / "secrets.yaml"
    p.write_text("jarvis_api_key: abc123\n")
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    hs._reset_secrets_cache()

    first = hs._read_secrets()                    # reads file (path=None -> SECRETS_PATH)
    assert first == {"jarvis_api_key": "abc123"}

    p.write_text("jarvis_api_key: CHANGED\n")     # change on disk
    cached = hs._read_secrets()                    # cached -> old value, no re-read
    assert cached == {"jarvis_api_key": "abc123"}

    forced = hs._read_secrets(force=True)          # force re-read
    assert forced == {"jarvis_api_key": "CHANGED"}


def test_ha_secrets_explicit_path_not_cached(load, tmp_path, monkeypatch):
    hs = load("ha_secrets")
    hs._reset_secrets_cache()
    p = tmp_path / "other.yaml"
    p.write_text("k: v1\n")
    r1 = hs._read_secrets(p)                        # explicit path -> not cached
    p.write_text("k: v2\n")
    r2 = hs._read_secrets(p)                        # re-reads (explicit paths bypass cache)
    assert r1 == {"k": "v1"} and r2 == {"k": "v2"}


def test_ha_secrets_missing_file_is_empty_and_cached(load, tmp_path, monkeypatch):
    hs = load("ha_secrets")
    monkeypatch.setattr(hs, "SECRETS_PATH", tmp_path / "nope.yaml")
    hs._reset_secrets_cache()
    assert hs._read_secrets() == {}
    assert hs._read_secrets() == {}                # cached empty, still fine
