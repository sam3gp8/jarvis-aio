"""Home Assistant 2026.8 LLM API compatibility (v7.21.1). HA changed the
ToolInput / LLMContext fields across versions (2026.8 moved the request context
out of ToolInput and dropped user_prompt from LLMContext). _ha_kwargs filters the
kwargs to what each constructor accepts, so JARVIS works on old and new HA."""
import pytest


@pytest.fixture
def agent_mod(load):
    return load("agent")


def test_ha_kwargs_new_toolinput_drops_context(agent_mod):
    class NewToolInput:  # HA 2026.8 signature
        def __init__(self, tool_name, tool_args, id="", external=False):
            pass
    kept = agent_mod._ha_kwargs(NewToolInput, tool_name="x", tool_args={},
                                platform="d", context="c", user_prompt="p")
    assert set(kept) == {"tool_name", "tool_args"}


def test_ha_kwargs_old_toolinput_keeps_context(agent_mod):
    class OldToolInput:  # pre-2026.8 signature
        def __init__(self, tool_name, tool_args, platform=None, context=None,
                     user_prompt="", language="en", assistant="", device_id=None):
            pass
    kept = agent_mod._ha_kwargs(OldToolInput, tool_name="x", tool_args={},
                                platform="d", context="c", user_prompt="p",
                                language="en", assistant="a", device_id="dev")
    assert {"platform", "context", "user_prompt", "device_id"} <= set(kept)


def test_ha_kwargs_new_llmcontext_drops_user_prompt(agent_mod):
    class NewLLMContext:  # HA 2026.8: no user_prompt
        def __init__(self, platform, context, language, assistant, device_id):
            pass
    kept = agent_mod._ha_kwargs(NewLLMContext, platform="d", context="c",
                                user_prompt="p", language="en", assistant="a",
                                device_id="dev")
    assert "user_prompt" not in kept and "platform" in kept
