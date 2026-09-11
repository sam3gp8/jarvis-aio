"""Guard: HA LLM tools carry voluptuous schemas, which are NOT JSON serializable.

_ha_tools_to_openai_format must convert each tool's parameters to a JSON Schema
dict, or the whole LLM request dies with "Object of type Schema is not JSON
serializable" and JARVIS falls back to the connectivity line (regression seen
once the conversation handler was un-orphaned in 7.53.4).
"""
import json
import importlib.util
from pathlib import Path

import pytest

vol = pytest.importorskip("voluptuous")
pytest.importorskip("voluptuous_openapi")

AGENT = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "agent.py"


def _load_fn():
    # Load just the function's source region to avoid importing the whole agent
    # (which needs HA). Exec the module-level defs it depends on: the _DROP
    # sentinel, _json_safe_schema, and _ha_tools_to_openai_format itself.
    import types, ast, logging
    src = AGENT.read_text()
    tree = ast.parse(src)
    mod = types.ModuleType("agent_stub")
    mod.__dict__["_LOGGER"] = logging.getLogger("stub")
    mod.__dict__["Sequence"] = list
    wanted = {"_json_safe_schema", "_ha_tools_to_openai_format"}
    for n in tree.body:
        seg = None
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_DROP" for t in n.targets):
            seg = ast.get_source_segment(src, n)
        elif isinstance(n, ast.FunctionDef) and n.name in wanted:
            seg = ast.get_source_segment(src, n)
        if seg is not None:
            exec(compile(seg, "<agent_fn>", "exec"), mod.__dict__)
    return mod.__dict__["_ha_tools_to_openai_format"]


class _FakeTool:
    def __init__(self, name, params):
        self.name = name
        self.description = "does a thing"
        self.parameters = params


def test_voluptuous_schema_is_converted_and_serializable():
    fn = _load_fn()
    schema = vol.Schema({vol.Required("name"): str, vol.Optional("count"): int})
    out = fn([_FakeTool("do_thing", schema)])
    # Must be JSON serializable — this is the exact failure being fixed
    json.dumps(out)
    params = out[0]["function"]["parameters"]
    assert isinstance(params, dict)
    assert params.get("type") == "object"
    assert "name" in params.get("properties", {})


def test_bad_schema_falls_back_without_raising():
    fn = _load_fn()

    class Weird:  # not a real schema; convert should fail -> fallback
        pass

    out = fn([_FakeTool("weird", Weird())])
    json.dumps(out)  # still serializable
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}
