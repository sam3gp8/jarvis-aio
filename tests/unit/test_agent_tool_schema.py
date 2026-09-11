"""Agent tool-schema conversion must produce JSON-serializable output even when
voluptuous_openapi leaves an UNSUPPORTED / _Unsupported sentinel in the schema
(issue #24: 'Object of type _Unsupported is not JSON serializable')."""
import json


def test_json_safe_schema_strips_sentinels(load):
    agent = load("agent")

    class _Unsupported:  # stand-in for voluptuous_openapi's sentinel
        pass

    s = _Unsupported()
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": s},
        "c": [1, s, "x"],
        "d": s,
        "keep_none": None,
    }
    out = agent._json_safe_schema(schema)
    json.dumps(out)  # must not raise
    assert out["properties"] == {"a": {"type": "string"}}
    assert out["c"] == [1, "x"]
    assert "d" not in out
    assert out["keep_none"] is None


def test_ha_tools_format_json_serializable_with_sentinel(load, monkeypatch):
    agent = load("agent")
    import sys, types

    class _Unsupported:
        pass

    s = _Unsupported()
    fake = types.ModuleType("voluptuous_openapi")
    fake.convert = lambda raw, custom_serializer=None: {
        "type": "object", "properties": {"x": {"type": "string"}, "y": s}}
    monkeypatch.setitem(sys.modules, "voluptuous_openapi", fake)

    class _Tool:
        name = "test_tool"
        description = "desc"
        parameters = {"dummy": 1}

    out = agent._ha_tools_to_openai_format([_Tool()])
    json.dumps(out)  # the whole point — must not raise
    props = out[0]["function"]["parameters"]["properties"]
    assert props == {"x": {"type": "string"}}
