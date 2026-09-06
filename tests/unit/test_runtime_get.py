"""jarvis_config.runtime_get — the canonical per-key config resolver.

Precedence must match effective_config: runtime_config (panel-live) → config.json
→ entry.options → entry.data → default. The bug this fixes: on a panel-configured
install entry.options/data are empty, so a direct read silently returns the
default for anything set via the panel/config.json — runtime_get consults
config.json so those values are honoured.
"""
import pytest


@pytest.fixture
def jc(load):
    m = load("jarvis_config")
    m._loaded = True
    m._cache = {}
    return m


@pytest.fixture
def const(load):
    return load("const")


class _Entry:
    def __init__(self, options=None, data=None, entry_id="e1"):
        self.options = options or {}
        self.data = data or {}
        self.entry_id = entry_id


def _hass(const, runtime=None, entry_id="e1"):
    class H:
        pass
    h = H()
    h.data = {const.DOMAIN: {entry_id: {"runtime_config": runtime or {}}}}
    return h


def test_config_json_consulted_on_panel_install(jc, const):
    # the bug: empty options/data, value only in config.json → must NOT default
    jc._cache = {"vision_model": "qwen/qwen3.6-27b"}
    entry = _Entry(options={}, data={})
    h = _hass(const)
    assert jc.runtime_get(h, entry, "vision_model", "DEFAULT") == "qwen/qwen3.6-27b"


def test_runtime_config_wins_over_config_json(jc, const):
    jc._cache = {"vision_model": "from_json"}
    h = _hass(const, runtime={"vision_model": "from_panel_live"})
    assert jc.runtime_get(h, _Entry(), "vision_model", "d") == "from_panel_live"


def test_config_json_wins_over_entry(jc, const):
    # config.json overrides the entry (matches effective_config precedence)
    jc._cache = {"honorific": "monsieur"}
    entry = _Entry(options={"honorific": "sir"}, data={})
    assert jc.runtime_get(_hass(const), entry, "honorific", "d") == "monsieur"


def test_entry_used_on_yaml_install(jc, const):
    # nothing in runtime_config or config.json → options, then data
    jc._cache = {}
    entry = _Entry(options={"notify_service": "notify.opts"}, data={})
    assert jc.runtime_get(_hass(const), entry, "notify_service", "d") == "notify.opts"
    entry2 = _Entry(options={}, data={"notify_service": "notify.data"})
    assert jc.runtime_get(_hass(const), entry2, "notify_service", "d") == "notify.data"


def test_default_when_nowhere(jc, const):
    jc._cache = {}
    assert jc.runtime_get(_hass(const), _Entry(), "missing", "fallback") == "fallback"


def test_empty_values_do_not_win(jc, const):
    # a blank config.json value must not clobber a real entry value
    jc._cache = {"broadcast_group": ""}
    entry = _Entry(options={"broadcast_group": "media_player.home"}, data={})
    assert jc.runtime_get(_hass(const), entry, "broadcast_group", "d") == "media_player.home"


def test_false_is_preserved(jc, const):
    # boolean False from config.json must be returned, not treated as "unset"
    jc._cache = {"sentinel_enabled": False}
    assert jc.runtime_get(_hass(const), _Entry(), "sentinel_enabled", True) is False


def test_tolerates_missing_hass_and_entry(jc, const):
    jc._cache = {"honorific": "madam"}
    assert jc.runtime_get(None, None, "honorific", "d") == "madam"   # config.json still consulted
    assert jc.runtime_get(None, None, "missing", "d") == "d"
