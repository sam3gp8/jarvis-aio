"""Tests for the secrets.yaml resolver (v6.81.0).

Read-only, tolerant of a missing or malformed file, and executor-offloaded via
async_get_secret. Paths resolve at call time, so monkeypatching SECRETS_PATH
takes effect (guarding the default-binding trap).
"""
import pytest


@pytest.fixture
def hs(load):
    return load("ha_secrets")


def test_read_missing_file_is_empty(hs, tmp_path):
    assert hs._read_secrets(tmp_path / "nope.yaml") == {}


def test_read_valid_yaml(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("jarvis_imap_password: hunter2\nother_key: value\n")
    data = hs._read_secrets(p)
    assert data["jarvis_imap_password"] == "hunter2"
    assert data["other_key"] == "value"


def test_read_malformed_yaml_is_empty(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('"unterminated string')          # scanner error → {}
    assert hs._read_secrets(p) == {}


def test_read_non_mapping_top_level_is_empty(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("- just\n- a\n- list\n")          # a list, not a mapping
    assert hs._read_secrets(p) == {}


def test_get_secret_sync_present(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("jarvis_imap_password: pw\n")
    assert hs.get_secret_sync("jarvis_imap_password", path=p) == "pw"


def test_get_secret_sync_absent_returns_default(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("something_else: x\n")
    assert hs.get_secret_sync("missing", default="DFLT", path=p) == "DFLT"


def test_get_secret_sync_empty_value_is_default(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('empty_key: ""\n')
    assert hs.get_secret_sync("empty_key", default="DFLT", path=p) == "DFLT"


def test_get_secret_sync_empty_key(hs):
    assert hs.get_secret_sync("", default="D") == "D"


async def test_async_get_secret_via_executor(hs, fake_hass, tmp_path, monkeypatch):
    p = tmp_path / "secrets.yaml"
    p.write_text("jarvis_imap_password: async_pw\n")
    monkeypatch.setattr(hs, "SECRETS_PATH", p)   # resolved at call time
    assert await hs.async_get_secret(fake_hass, "jarvis_imap_password") == "async_pw"


async def test_async_get_secret_no_hass(hs, tmp_path, monkeypatch):
    p = tmp_path / "secrets.yaml"
    p.write_text("k: v\n")
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    assert await hs.async_get_secret(None, "k") == "v"


async def test_async_get_secret_missing_returns_default(hs, fake_hass, tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "SECRETS_PATH", tmp_path / "none.yaml")
    assert await hs.async_get_secret(fake_hass, "k", default="DEF") == "DEF"
