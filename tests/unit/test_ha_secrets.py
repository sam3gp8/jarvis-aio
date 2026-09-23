"""Tests for the secrets.yaml resolver (v6.81.0).

Read-only, tolerant of a missing or malformed file, and executor-offloaded via
async_get_secret. Paths resolve at call time, so monkeypatching SECRETS_PATH
takes effect (guarding the default-binding trap).
"""
import pytest
import types


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


async def test_async_provider_key_uses_hass_config_path(hs, fake_hass, tmp_path, monkeypatch):
    p = tmp_path / "home-assistant" / "secrets.yaml"
    fake_hass.config = types.SimpleNamespace(
        path=lambda name: str(p.parent / name),
        time_zone="America/New_York",
    )
    monkeypatch.setattr(hs, "SECRETS_PATH", tmp_path / "wrong" / "secrets.yaml")

    assert await hs.async_set_provider_key(fake_hass, "gemini", "gk_real") is True
    assert hs.get_secret_sync("jarvis_gemini_api_key", path=p) == "gk_real"
    assert not hs.SECRETS_PATH.exists()


async def test_async_get_secret_no_hass(hs, tmp_path, monkeypatch):
    p = tmp_path / "secrets.yaml"
    p.write_text("k: v\n")
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    assert await hs.async_get_secret(None, "k") == "v"


async def test_async_get_secret_missing_returns_default(hs, fake_hass, tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "SECRETS_PATH", tmp_path / "none.yaml")
    assert await hs.async_get_secret(fake_hass, "k", default="DEF") == "DEF"


def test_get_legacy_provider_key_prefers_selected_provider_field(hs):
    cfg = {"llm_provider": "gemini", "gemini_api_key": "G", "api_key": "OLD"}
    assert hs.get_legacy_provider_key(cfg, "gemini") == "G"


def test_get_legacy_provider_key_does_not_reuse_shared_key_for_other_provider(hs):
    cfg = {"llm_provider": "groq", "api_key": "GROQ"}
    assert hs.get_legacy_provider_key(cfg, "openai") == ""


def test_get_legacy_provider_key_does_not_reuse_other_provider_shared_key_for_groq(hs):
    cfg = {"llm_provider": "openai", "api_key": "OPENAI"}
    assert hs.get_legacy_provider_key(cfg, "groq") == ""


def test_get_legacy_provider_key_keeps_groq_alias_available(hs):
    cfg = {"llm_provider": "openai", "groq_api_key": "GROQ"}
    assert hs.get_legacy_provider_key(cfg, "groq") == "GROQ"


def test_get_provider_key_sync_falls_back_to_runtime_plaintext(hs, monkeypatch, load):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"llm_provider": "openai", "openai_api_key": "sk-openai"})
    monkeypatch.setattr(hs, "get_secret_sync", lambda *a, **k: "")
    assert hs.get_provider_key_sync("openai") == "sk-openai"


def test_get_provider_key_sync_keeps_runtime_fallback_when_hass_path_is_used(
    hs, monkeypatch, load, tmp_path,
):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"llm_provider": "openai", "openai_api_key": "sk-openai"})
    monkeypatch.setattr(hs, "get_secret_sync", lambda *a, **k: "")
    assert hs.get_provider_key_sync("openai", path=tmp_path / "secrets.yaml") == "sk-openai"


def test_get_provider_key_sync_does_not_send_other_provider_shared_key_to_groq(
    hs, monkeypatch, load,
):
    jc = load("jarvis_config")
    monkeypatch.setattr(
        jc,
        "get_all",
        lambda: {"llm_provider": "openai", "api_key": "OPENAI", "groq_api_key": "GROQ"},
    )
    monkeypatch.setattr(hs, "get_secret_sync", lambda *a, **k: "")
    assert hs.get_provider_key_sync("groq") == "GROQ"


def test_get_stored_provider_key_sync_reads_secrets_only(hs, monkeypatch):
    monkeypatch.setattr(hs, "get_secret_sync",
                        lambda key, default="", path=None: "sk-openai"
                        if key == "jarvis_openai_api_key" else default)
    assert hs.get_stored_provider_key_sync("openai") == "sk-openai"


async def test_promote_shared_secret_for_provider_copies_legacy_shared_secret(
    hs, fake_hass, tmp_path, monkeypatch,
):
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "sk-openai"\n')
    monkeypatch.setattr(hs, "SECRETS_PATH", p)

    assert await hs.promote_shared_secret_for_provider(fake_hass, "openai") is True
    assert hs.get_secret_sync("jarvis_openai_api_key", path=p) == "sk-openai"


def test_get_provider_key_sync_uses_entry_fallback_when_relocation_failed(
    hs, monkeypatch, load, tmp_path,
):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(hs, "get_secret_sync", lambda *a, **k: "")
    jc.init_from_entry(
        {"llm_provider": "openai", "api_key": "legacy-openai"},
        {},
    )
    monkeypatch.setattr(jc, "get_all", lambda: {})
    assert hs.get_provider_key_sync("openai") == "legacy-openai"


def test_overlay_legacy_groq_secret_populates_canonical_api_key(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_groq_api_key: "LEGACY_GROQ"\n')
    out = hs.overlay_credentials({}, path=p)
    assert out["api_key"] == "LEGACY_GROQ"
    assert out["groq_api_key"] == "LEGACY_GROQ"
