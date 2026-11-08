import types


def test_config_path_uses_hass_config_path(load, tmp_path):
    paths = load("paths")
    hass = types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts)))
    )

    assert paths.config_path("jarvis", "config.json", hass=hass) == tmp_path / "jarvis" / "config.json"


def test_set_config_dir_from_hass_updates_sync_fallback(load, tmp_path, monkeypatch):
    paths = load("paths")
    original = paths._config_dir
    monkeypatch.setattr(paths, "_config_dir", original)
    hass = types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts)))
    )

    paths.set_config_dir_from_hass(hass)

    assert paths.config_path("jarvis", "patterns.db") == tmp_path / "jarvis" / "patterns.db"

def test_db_storage_override_redirects_db_files_only(load, tmp_path, monkeypatch):
    """External DB location (issue #491c): .db files move to the override dir, but
    config.json and other small bootstrap files stay in the config dir."""
    paths = load("paths")
    paths.clear_jarvis_storage_dir()
    monkeypatch.setattr(paths, "_config_dir", tmp_path)   # deterministic default
    ext = tmp_path / "share"
    try:
        got = paths.set_jarvis_storage_dir(str(ext))
        assert got is not None
        # a database moves to the external dir...
        assert paths.config_path("jarvis", "patterns.db") == ext / "patterns.db"
        assert paths.config_path("jarvis", "conversations.db") == ext / "conversations.db"
        # ...but config.json (not a .db) stays under the default config dir
        assert paths.config_path("jarvis", "config.json") == tmp_path / "jarvis" / "config.json"
    finally:
        paths.clear_jarvis_storage_dir()


def test_db_storage_override_blank_and_bad_paths_keep_default(load, tmp_path, monkeypatch):
    paths = load("paths")
    paths.clear_jarvis_storage_dir()
    monkeypatch.setattr(paths, "_config_dir", tmp_path)
    try:
        assert paths.set_jarvis_storage_dir("") is None
        assert paths.set_jarvis_storage_dir("   ") is None
        # a path under an existing *file* can't be created → unusable → None
        afile = tmp_path / "afile"
        afile.write_text("x")
        assert paths.set_jarvis_storage_dir(str(afile / "sub")) is None
        assert paths.jarvis_storage_dir() is None
        # default location is unaffected
        assert paths.config_path("jarvis", "patterns.db") == tmp_path / "jarvis" / "patterns.db"
    finally:
        paths.clear_jarvis_storage_dir()
