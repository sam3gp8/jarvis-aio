"""Gemma tool-call parser — balanced recursive-descent (automation cleanup, #40).

The old non-greedy ``\\{(.*?)\\}`` regex truncated a ``create_automation`` call's
nested ``trigger:[{...}]`` / ``action:[{...}]`` at the first ``}``, so corrupted
strings passed validation and rendered as invalid YAML. These pin the real
structured parse, the scalar/array/escape handling, graceful skipping of malformed
calls, and the provider-arg guard.
"""
import pytest


@pytest.fixture
def llm(load):
    return load("llm_provider")


def test_nested_automation_parses_into_real_structures(llm):
    text = (
        '<|tool_call>call:create_automation{alias:<|"|>Porch at dusk<|"|>,'
        'trigger:[{trigger:<|"|>state<|"|>,entity_id:<|"|>binary_sensor.front_door<|"|>,to:<|"|>on<|"|>}],'
        'action:[{action:<|"|>light.turn_on<|"|>,target:{entity_id:<|"|>light.porch<|"|>}}]}<tool_call|>'
    )
    calls, cleaned = llm.parse_gemma_tool_calls(text, {"create_automation"})
    assert len(calls) == 1
    a = calls[0]["args"]
    assert a["alias"] == "Porch at dusk"
    # the nested lists are REAL list[dict], not truncated strings
    assert isinstance(a["trigger"], list) and isinstance(a["trigger"][0], dict)
    assert a["trigger"][0] == {"trigger": "state",
                               "entity_id": "binary_sensor.front_door", "to": "on"}
    assert isinstance(a["action"], list)
    assert a["action"][0]["action"] == "light.turn_on"
    assert a["action"][0]["target"] == {"entity_id": "light.porch"}
    assert cleaned == ""


def test_json_strings_arrays_and_scalars(llm):
    text = ('call:control_device{action:"turn_on",items:["a","b"],'
            'level:50,dim:3.5,on:true,extra:null}')
    calls, _ = llm.parse_gemma_tool_calls(text, {"control_device"})
    a = calls[0]["args"]
    assert a["action"] == "turn_on"
    assert a["items"] == ["a", "b"]
    assert a["level"] == 50 and a["dim"] == 3.5
    assert a["on"] is True and a["extra"] is None


def test_json_string_escapes(llm):
    # a backslash-escaped newline inside a "..." string decodes to a real newline
    calls, _ = llm.parse_gemma_tool_calls(r'call:x{note:"a\nb",q:"say \"hi\""}', {"x"})
    a = calls[0]["args"]
    assert a["note"] == "a\nb"        # real newline
    assert a["q"] == 'say "hi"'       # escaped quotes preserved


def test_truncated_call_is_skipped_not_raised(llm):
    # a truncated call must never raise — best-effort parse, no crash
    calls, _ = llm.parse_gemma_tool_calls(
        "call:create_automation{trigger:[{entity_id:light.x", {"create_automation"})
    assert isinstance(calls, list)   # did not raise


def test_two_calls_in_one_message(llm):
    text = ("call:control_device{action:turn_on,entity_id:light.a} and then "
            "call:control_device{action:turn_off,entity_id:light.b}")
    calls, cleaned = llm.parse_gemma_tool_calls(text, {"control_device"})
    assert [c["args"]["entity_id"] for c in calls] == ["light.a", "light.b"]
    assert "call:" not in cleaned


def test_unknown_tool_name_is_not_recovered(llm):
    calls, cleaned = llm.parse_gemma_tool_calls(
        "call:create_automation{trigger:[{a:b}],action:[{c:d}]}", {"control_device"})
    assert calls == []               # not a declared tool
    assert cleaned.startswith("call:create_automation")   # text left intact


def test_safe_parse_tool_args(llm):
    f = llm._safe_parse_tool_args
    assert f('{"a": 1}') == {"a": 1}
    assert f({"a": 1}) == {"a": 1}            # already a dict
    assert f("") == {} and f(None) == {}
    assert f("not json at all") == {}         # unparseable -> {}
    assert f("[1, 2, 3]") == {}               # valid JSON, but not a dict
    assert f('noise {"x": 2} tail') == {"x": 2}   # extract first balanced object
