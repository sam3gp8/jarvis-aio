"""Tests for HA config-entry diagnostics (v6.70.2). The safety-critical property
is redaction: a diagnostics dump must never leak API keys, tokens, or the
address. Also verifies the function never raises so the download always yields a
file."""
import importlib.util
import pathlib
import sys

import pytest

_COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def diag():
    """Load the diagnostics package __init__.py directly (the shared _load helper
    only handles single .py modules, but diagnostics is a package)."""
    key = "jc.diagnostics"
    if key in sys.modules:
        return sys.modules[key]
    init = _COMP / "diagnostics" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        key, init, submodule_search_locations=[str(_COMP / "diagnostics")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def test_redact_strips_api_keys(diag):
    src = {
        "groq_api_key": "gsk_secret123",
        "gemini_api_key": "AItotallysecret",
        "honorific": "sir",
        "nested": {"anthropic_api_key": "sk-ant-xxx", "banter_level": 2},
    }
    out = diag._redact(src)
    assert out["groq_api_key"] == "**REDACTED**"
    assert out["gemini_api_key"] == "**REDACTED**"
    assert out["nested"]["anthropic_api_key"] == "**REDACTED**"
    # non-sensitive values pass through untouched
    assert out["honorific"] == "sir"
    assert out["nested"]["banter_level"] == 2


def test_redact_strips_tokens_and_address(diag):
    src = {"refresh_token": "1//xxx", "floor_plan_address": "123 Real St",
           "client_secret": "shh", "password": "hunter2"}
    out = diag._redact(src)
    for k in src:
        assert out[k] == "**REDACTED**", f"{k} was not redacted"


def test_redact_leaves_empty_values_alone(diag):
    # an unset key shouldn't become "**REDACTED**" (nothing to hide)
    out = diag._redact({"api_key": "", "token": None})
    assert out["api_key"] == ""
    assert out["token"] is None


def test_redact_handles_lists(diag):
    out = diag._redact({"items": [{"api_key": "x"}, {"name": "ok"}]})
    assert out["items"][0]["api_key"] == "**REDACTED**"
    assert out["items"][1]["name"] == "ok"


def test_redact_never_raises_on_weird_input(diag):
    # should degrade gracefully, not crash
    class Weird:
        def __iter__(self):
            raise RuntimeError("nope")
    assert diag._redact(Weird()) is not None    # returns something, no raise


async def test_diagnostics_entry_point_redacts_and_returns_dict(diag):
    # end-to-end: the HA entry point returns a dict with keys redacted, no raise
    class _Entry:
        version = 1
        data = {"groq_api_key": "gsk_leak", "honorific": "sir"}
        options = {"gemini_api_key": "leak2", "banter_level": 2}
    class _States:
        def async_all(self):
            return []
    class _Hass:
        states = _States()
        async def async_add_executor_job(self, fn, *a):
            return fn(*a)
    out = await diag.async_get_config_entry_diagnostics(_Hass(), _Entry())
    assert isinstance(out, dict)
    assert out["integration"] == "jarvis"
    # the API keys must not appear anywhere in the serialized dump
    import json
    dumped = json.dumps(out)
    assert "gsk_leak" not in dumped
    assert "leak2" not in dumped


def test_diagnostics_exports_entry_point(diag):
    assert hasattr(diag, "async_get_config_entry_diagnostics")
    assert "async_get_config_entry_diagnostics" in diag.__all__


# ── JSON-safety: the download must always be strict, parseable JSON (v9.78.0) ───
# HA serialises the dump with json.dumps. A non-string dict key raises TypeError
# (download fails); a NaN/Infinity float emits invalid-JSON tokens (file won't
# parse). _json_safe() must neutralise both while preserving the data.

def _strict_dumps(obj):
    # allow_nan=False makes json.dumps raise on NaN/Infinity instead of emitting
    # the non-standard tokens — i.e. it accepts ONLY strict, parseable JSON.
    import json
    return json.dumps(obj, allow_nan=False)


def test_json_safe_stringifies_non_string_keys(diag):
    import enum

    class Cap(enum.Enum):
        LOCK = "lock"
    out = diag._json_safe({1: "a", 3.5: "b", None: "c", True: "d",
                           ("x", "y"): "e", Cap.LOCK: "f"})
    # every key is now a string, and the structure still serialises strictly
    assert all(isinstance(k, str) for k in out)
    _strict_dumps(out)  # must not raise


def test_json_safe_coerces_nan_and_infinity(diag):
    out = diag._json_safe({"rate": float("nan"), "hi": float("inf"),
                           "lo": float("-inf"), "ok": 0.5})
    assert out["rate"] is None and out["hi"] is None and out["lo"] is None
    assert out["ok"] == 0.5
    _strict_dumps(out)  # must not raise


def test_json_safe_handles_sets_bytes_datetimes_and_objects(diag):
    import datetime

    class Opaque:
        def __repr__(self):
            return "<opaque>"
    val = {
        "a_set": {1, 2, 3},
        "a_tuple": (1, 2),
        "some_bytes": b"\xff\xfehi",
        "when": datetime.datetime(2026, 1, 2, 3, 4, 5),
        "obj": Opaque(),
    }
    out = diag._json_safe(val)
    assert isinstance(out["a_set"], list) and sorted(out["a_set"]) == [1, 2, 3]
    assert out["a_tuple"] == [1, 2]
    assert isinstance(out["some_bytes"], str)
    assert out["when"] == "2026-01-02T03:04:05"
    assert out["obj"] == "<opaque>"
    _strict_dumps(out)  # must not raise


def test_json_safe_survives_circular_reference(diag):
    d = {"name": "loop"}
    d["self"] = d
    out = diag._json_safe(d)
    assert out["name"] == "loop"
    assert out["self"] == "**circular**"
    _strict_dumps(out)  # must not raise


async def test_diagnostics_entry_point_output_is_strict_json(diag):
    # A live-shaped entry whose values include the exact hazards (a NaN, a set,
    # a datetime) must still yield a dump that strict JSON accepts.
    import datetime

    class _Entry:
        version = 1
        data = {"api_key": "secret", "ratio": float("nan")}
        options = {"when": datetime.datetime(2026, 1, 1), "tags": {"a", "b"}}
    class _States:
        def async_all(self):
            return []
    class _Hass:
        states = _States()
        async def async_add_executor_job(self, fn, *a):
            return fn(*a)
    out = await diag.async_get_config_entry_diagnostics(_Hass(), _Entry())
    _strict_dumps(out)  # the whole dump parses strictly — no NaN, no bad keys
    assert out["integration"] == "jarvis"


# ── authority-enforce rollout data (Step A of the allowlist-widening flip) ──────

class _AuthHass:
    """Minimal hass with a .data dict so the authority parity tally can be read."""
    def __init__(self):
        self.data = {}
    class _States:
        def async_all(self):
            return []
    states = _States()
    async def async_add_executor_job(self, fn, *a):
        return fn(*a)


async def test_diagnostics_surfaces_authority_parity(diag, load):
    # `load` registers the synthetic `jc` package so the diag function's
    # `from .. import authority_bridge` resolves; seed one engine-vs-gate record.
    ab = load("authority_bridge")
    hass = _AuthHass()
    ab.record_control_parity(hass, "lock", "unlock", allowed=True)

    class _Entry:
        version = 1
        data = {}
        options = {}

    out = await diag.async_get_config_entry_diagnostics(hass, _Entry())
    ap = out.get("authority_parity")
    assert ap, out.get("authority_parity_error")
    assert ap["enforce_on"] is True                     # mirrors AUTHORITY_ENFORCE
    assert "lock.unlock" in ap["enforced_capabilities"]  # the allowlist is surfaced
    # the per-capability tally (what gates widening) is present
    assert "by_capability" in ap["tally"] and "agreement_rate" in ap["tally"]
