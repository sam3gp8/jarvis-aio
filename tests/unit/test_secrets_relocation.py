"""Tests for the secrets writer + credential relocation (v6.83.0).

The writer must preserve the rest of the user's secrets.yaml and never lose
data; relocation must write→verify→strip and, on any failure, leave config.json
untouched so a credential can never be lost or auth broken.
"""
import pytest

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


@pytest.fixture
def hs(load):
    return load("ha_secrets")


# ── line upsert ──────────────────────────────────────────────────────────────

def test_upsert_appends_when_absent(hs):
    out = hs._upsert_secret_line("existing: 1\n", "jarvis_api_key", "K")
    assert "existing: 1" in out
    assert 'jarvis_api_key: "K"' in out


def test_upsert_replaces_when_present(hs):
    out = hs._upsert_secret_line('a: 1\njarvis_api_key: "OLD"\nb: 2\n',
                                 "jarvis_api_key", "NEW")
    assert 'jarvis_api_key: "NEW"' in out
    assert '"OLD"' not in out
    assert "a: 1" in out and "b: 2" in out            # rest preserved


@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_upsert_escapes_quotes_and_backslashes(hs):
    out = hs._upsert_secret_line("", "k", 'a"b\\c')
    assert yaml.safe_load(out)["k"] == 'a"b\\c'


# ── set_secret_sync (safe write) ─────────────────────────────────────────────

@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_set_secret_writes_and_preserves(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("# my secrets\nother_key: value\n")
    assert hs.set_secret_sync("jarvis_api_key", "K", path=p) is True
    text = p.read_text()
    assert "# my secrets" in text                     # comment preserved
    assert "other_key: value" in text                 # other key preserved
    assert yaml.safe_load(text)["jarvis_api_key"] == "K"
    assert (tmp_path / "secrets.yaml.jarvis.bak").exists()   # backup made


@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_set_secret_updates_existing(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "OLD"\n')
    hs.set_secret_sync("jarvis_api_key", "NEW", path=p)
    assert yaml.safe_load(p.read_text())["jarvis_api_key"] == "NEW"


@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_set_secret_creates_missing_file(hs, tmp_path):
    p = tmp_path / "sub" / "secrets.yaml"
    assert hs.set_secret_sync("jarvis_api_key", "K", path=p) is True
    assert yaml.safe_load(p.read_text())["jarvis_api_key"] == "K"


def test_set_secret_empty_key_false(hs, tmp_path):
    assert hs.set_secret_sync("", "K", path=tmp_path / "s.yaml") is False


# ── overlay_credentials ──────────────────────────────────────────────────────

def test_overlay_secrets_win(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_gemini_api_key: "SECRET_G"\n')
    out = hs.overlay_credentials({"gemini_api_key": "PLAIN_G", "model": "x"}, path=p)
    assert out["gemini_api_key"] == "SECRET_G"        # secrets win for creds
    assert out["model"] == "x"                        # non-cred untouched


def test_overlay_absent_secret_no_change(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('unrelated: "x"\n')
    out = hs.overlay_credentials({"api_key": "PLAIN"}, path=p)
    assert out["api_key"] == "PLAIN"


# ── relocate_plaintext_credentials (verify-before-strip) ─────────────────────

@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
async def test_relocate_writes_verifies_strips(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    deleted = []
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"gemini_api_key": "GKEY", "api_key": "", "model": "x"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 1
    assert deleted == ["gemini_api_key"]              # only the non-empty cred moved
    assert hs.get_secret_sync("jarvis_gemini_api_key", path=p) == "GKEY"


async def test_relocate_leaves_config_when_write_fails(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(hs, "SECRETS_PATH", tmp_path / "secrets.yaml")
    deleted = []
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "KEY"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    monkeypatch.setattr(hs, "set_secret_sync", lambda k, v, path=None: False)
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 0
    assert deleted == []                              # never stripped on write failure


async def test_relocate_drops_redundant_when_same(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "KEY"\n')
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    deleted = []
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "KEY"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 1 and deleted == ["api_key"]          # redundant plaintext dropped


async def test_relocate_keeps_both_when_different(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "SECRETVAL"\n')
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    deleted = []
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "DIFFERENT"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 0 and deleted == []                   # differ -> leave both, don't guess
