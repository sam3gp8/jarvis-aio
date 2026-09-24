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