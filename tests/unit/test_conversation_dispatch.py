"""Guards for the conversation entity's HA dispatch contract.

HA's ConversationEntity.async_process is @final and sets up the chat session/log
before calling _async_handle_message. JARVIS must implement ONLY
_async_handle_message and must not override async_process — a shim that did
(v7.48.1) bypassed that setup on current HA and crashed voice turns with an
opaque "Unexpected error during intent recognition". These are source-level
guards because exercising the entity needs a live HA conversation stack.
"""
import inspect
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "conversation.py"


def test_does_not_override_final_async_process():
    src = SRC.read_text()
    # No `async def async_process` method on the entity (it's @final in HA).
    assert "async def async_process(" not in src


def test_handler_is_wrapped_against_crashes():
    src = SRC.read_text()
    # The public handler HA calls delegates to an impl inside try/except, so a
    # crash surfaces with a trace instead of HA's opaque error.
    assert "async def _async_handle_message(" in src
    assert "async def _handle_message_impl(" in src
    assert "conversation handler crashed" in src


def test_handler_is_actually_a_method_of_jarvis_agent():
    """The handler MUST be a method of the registered entity class JarvisAgent.
    A stray column-0 statement once closed the class early and orphaned the whole
    handler as dead code, so HA hit its base _async_handle_message and raised
    NotImplementedError. Text-presence isn't enough — check real class membership.
    """
    import ast
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JarvisAgent":
            methods = {
                n.name for n in node.body
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            }
            assert "_async_handle_message" in methods, \
                "_async_handle_message is not a method of JarvisAgent (orphaned?)"
            assert "_handle_message_impl" in methods
            return
    raise AssertionError("JarvisAgent class not found")


def test_agentic_loop_uses_standardized_result_fields_not_raw_message():
    """Anthropic's `raw` is a Message with content blocks and no `.tool_calls` —
    building the assistant turn from `raw_message.tool_calls`/`.content` (an
    OpenAI-only shape) raised AttributeError on the first Anthropic tool turn.
    The loop must build the assistant message from the standardized
    result["text"]/result["calls"] fields instead."""
    src = SRC.read_text()
    assert "raw_message" not in src
    assert 'result["calls"]' in src


def test_context_includes_household_temperature_unit():
    """Freeform LLM output must follow Home Assistant's unit system rather than
    defaulting to Fahrenheit. The live-context builder reads the configured
    temperature unit and instructs the model to express temperatures in it, so a
    metric household hears Celsius even when JARVIS isn't quoting a sensor."""
    src = SRC.read_text()
    assert "config.units.temperature_unit" in src, \
        "conversation context must read HA's configured temperature unit"
    assert "temperatures in" in src, \
        "context must instruct the model which temperature unit to use"


import re as _re


def _load_gate_fn():
    """Exec _is_addressed_to_jarvis + its constant deps in isolation (the entity
    needs a live HA stack, so the module can't be imported wholesale)."""
    import ast, types
    src = SRC.read_text()
    tree = ast.parse(src)
    mod = types.ModuleType("conv_gate_stub")
    want_fns = {"_is_addressed_to_jarvis"}
    want_assign = {"_COMMAND_VERBS", "_QUESTION_STARTS", "_DOMAIN_KEYWORDS", "_FILLER"}
    for n in tree.body:
        seg = None
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want_assign for t in n.targets):
            seg = ast.get_source_segment(src, n)
        elif isinstance(n, ast.FunctionDef) and n.name in want_fns:
            seg = ast.get_source_segment(src, n)
        if seg is not None:
            exec(compile(seg, "<conv_gate>", "exec"), mod.__dict__)
    return mod.__dict__["_is_addressed_to_jarvis"]


def test_relevance_gate_strict_drops_ambient_during_media():
    g = _load_gate_fn()
    # Real commands pass whether or not media is playing.
    for cmd in ["turn on the lights", "what's the temperature", "jarvis status", "lock the front door"]:
        assert g(cmd, strict=False) is True, cmd
        assert g(cmd, strict=True) is True, cmd
    # TV/movie dialogue fragments: still pass in a quiet room (lenient default,
    # unchanged), but are DROPPED when media is playing nearby.
    for frag in ["um sergeant", "Thank you.",
                 "than you're paid for and they're often knowing that you're"]:
        assert g(frag, strict=False) is True, frag       # lenient default unchanged
        assert g(frag, strict=True) is False, frag        # media playing → dropped


def test_gate_and_followups_are_media_aware():
    src = SRC.read_text()
    assert "_media_playing_near(device_id)" in src, "gate must check media state"
    assert "_is_addressed_to_jarvis(user_input.text, strict=" in src, "gate must pass strict"
    assert "not self._media_playing_near(reopen_device)" in src, \
        "continued-conversation reopen must be suppressed during media"
    m = _re.search(r"def _media_playing_near\(.*?\n(.*?)\n\n    def ", src, _re.S)
    assert m and "movie_media_player" in m.group(1) and "entity_area" in m.group(1), \
        "media detector must check the movie player + the satellite's area"


def _load_conversation_module(monkeypatch):
    """Import the real conversation module under minimal HA stubs so the saved
    file's runtime logic is exercised instead of only checking source text."""
    import importlib.util
    import sys
    import types

    pkg_name = "jarvis_cov_stub"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(SRC.parent)]
    monkeypatch.setitem(sys.modules, pkg_name, pkg)

    if "homeassistant" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = type("HomeAssistant", (), {})
        sys.modules["homeassistant"] = ha
        sys.modules["homeassistant.core"] = core
        ha.core = core

    helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    intent_mod = types.ModuleType("homeassistant.helpers.intent")
    llm_mod = types.ModuleType("homeassistant.helpers.llm")
    dr_mod = types.ModuleType("homeassistant.helpers.device_registry")
    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    cfg_mod = types.ModuleType("homeassistant.config_entries")

    class IntentResponse:
        def __init__(self, language=None):
            self.language = language
            self.speech = ""

        def async_set_speech(self, speech):
            self.speech = speech

    class LLMContext:
        def __init__(self, platform=None, context=None, user_prompt=None, language=None,
                     assistant=None, device_id=None):
            self.platform = platform
            self.context = context
            self.user_prompt = user_prompt
            self.language = language
            self.assistant = assistant
            self.device_id = device_id

    class ToolInput:
        def __init__(self, tool_name=None, tool_args=None, platform=None, context=None,
                     user_prompt=None, language=None, assistant=None, device_id=None):
            self.tool_name = tool_name
            self.tool_args = tool_args
            self.platform = platform
            self.context = context
            self.user_prompt = user_prompt
            self.language = language
            self.assistant = assistant
            self.device_id = device_id

    class DeviceInfo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ConfigEntry:
        def __init__(self, entry_id="test-entry"):
            self.entry_id = entry_id

    class ConversationEntityFeature:
        CONTROL = "control"

    class ConversationEntity:
        pass

    class ConversationInput:
        def __init__(self, text="", device_id=None, conversation_id="cid", language="en"):
            self.text = text
            self.device_id = device_id
            self.conversation_id = conversation_id
            self.language = language
            self.context = {}

    class ConversationResult:
        def __init__(self, response=None, conversation_id=None):
            self.response = response
            self.conversation_id = conversation_id
            self.continue_conversation = False

    intent_mod.IntentResponse = IntentResponse
    llm_mod.LLMContext = LLMContext
    llm_mod.ToolInput = ToolInput
    llm_mod.async_get_api = lambda *a, **k: None
    dr_mod.DeviceInfo = DeviceInfo
    ep_mod.AddEntitiesCallback = object
    cfg_mod.ConfigEntry = ConfigEntry

    components = types.ModuleType("homeassistant.components")
    conv_mod = types.ModuleType("homeassistant.components.conversation")
    conv_mod.ConversationEntity = ConversationEntity
    conv_mod.ConversationEntityFeature = ConversationEntityFeature
    conv_mod.ConversationInput = ConversationInput
    conv_mod.ConversationResult = ConversationResult
    conv_mod.HOME_ASSISTANT_AGENT = "home_assistant"

    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.intent", intent_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.llm", llm_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.device_registry", dr_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.entity_platform", ep_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", cfg_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.components", components)
    monkeypatch.setitem(sys.modules, "homeassistant.components.conversation", conv_mod)
    components.conversation = conv_mod
    helpers.intent = intent_mod
    helpers.llm = llm_mod
    helpers.device_registry = dr_mod
    helpers.entity_platform = ep_mod

    const_mod = types.ModuleType(f"{pkg_name}.const")
    directive_presets = {"guardian_steward": "Directive text"}
    for name, value in {
        "DOMAIN": "jarvis",
        "CONF_BROADCAST_SPEAKERS": "broadcast_speakers",
        "CONF_CAST_ANNOUNCE": "cast_announce",
        "CONF_CAST_SPEAKERS": "cast_speakers",
        "CONF_DIRECTIVE": "directive",
        "CONF_DIRECTIVE_PRESET": "directive_preset",
        "CONF_HONORIFIC": "honorific",
        "CONF_MODEL": "model",
        "CONF_REPLY_SPEAKERS": "reply_speakers",
        "CONF_ROOM_ROUTING": "room_routing",
        "CONF_TTS_ENGINE": "tts_engine",
        "CONF_USE_HASS_API": "use_hass_api",
        "CONF_VOICE_SATELLITES": "voice_satellites",
        "DEFAULT_DIRECTIVE_PRESET": "guardian_steward",
        "DEFAULT_HONORIFIC": "sir",
        "DEFAULT_MODEL": "openai/gpt-oss-120b",
        "DEFAULT_ROOM_ROUTING": True,
        "DEFAULT_TTS_ENGINE": "auto",
        "JARVIS_PERSONA": "You are JARVIS.",
        "ALL_SPEAKERS_VALUE": "__all__",
    }.items():
        setattr(const_mod, name, value)

    def get_directive(preset_name: str = "guardian_steward", custom_directive: str = "") -> str:
        custom = (custom_directive or "").strip()
        if custom:
            return custom
        return directive_presets.get(preset_name, directive_presets["guardian_steward"])

    const_mod.get_directive = get_directive
    const_mod.resolve_provider_base_url = lambda config, provider: (
        config.get("custom_base_url") or config.get("ollama_base_url")
        or config.get("llm_base_url")
    )
    monkeypatch.setitem(sys.modules, f"{pkg_name}.const", const_mod)

    def _setter(name, mod):
        monkeypatch.setitem(sys.modules, f"{pkg_name}.{name}", mod)

    audio_routing = types.ModuleType(f"{pkg_name}.audio_routing")
    audio_routing.reply_targets = lambda *a, **k: ["media_player.living_room"]
    audio_routing.reply_target = lambda *a, **k: None
    audio_routing.entity_area = lambda hass, entity_id: "living_room" if entity_id else None
    audio_routing._entities_by_domain = lambda hass, domain: ["media_player.tv"]
    _setter("audio_routing", audio_routing)

    database_mod = types.ModuleType(f"{pkg_name}.database")
    database_mod.save_message = lambda *a, **k: None
    _setter("database", database_mod)

    llm_provider = types.ModuleType(f"{pkg_name}.llm_provider")
    llm_provider.create_provider = lambda *a, **k: object()
    _setter("llm_provider", llm_provider)

    presence_mod = types.ModuleType(f"{pkg_name}.presence")
    presence_mod.presence_context_string = lambda hass: "Sam is home."
    _setter("presence", presence_mod)

    tts_mod = types.ModuleType(f"{pkg_name}.tts_helper")
    tts_mod.resolve_tts_entity = lambda hass, configured: "tts.piper"
    tts_mod.async_announce = lambda *a, **k: None
    _setter("tts_helper", tts_mod)

    paths_mod = types.ModuleType(f"{pkg_name}.paths")
    paths_mod.config_path_str = lambda name: str(SRC.parent / name)
    _setter("paths", paths_mod)

    spy_mod = types.ModuleType(f"{pkg_name}.recognition")
    spy_mod.recognition_context_string = lambda hass: "Recognition: Sam"
    _setter("recognition", spy_mod)

    async def try_local(hass, text, honorific, force=False):
        return types.SimpleNamespace(handled=True, text=f"Handled: {text}")

    local_engine = types.ModuleType(f"{pkg_name}.local_engine")
    local_engine.try_local = try_local
    local_engine.score_complexity = lambda text: 0
    _setter("local_engine", local_engine)

    memory_thread = types.ModuleType(f"{pkg_name}.memory_thread")
    memory_thread.config = lambda: (False, 0, 0)
    memory_thread.load_recent = lambda *a, **k: []
    _setter("memory_thread", memory_thread)

    memory_mod = types.ModuleType(f"{pkg_name}.memory")
    memory_mod.store_memory = lambda *a, **k: None
    memory_mod.get_conversation_context = lambda *a, **k: None
    _setter("memory", memory_mod)

    knowledge_mod = types.ModuleType(f"{pkg_name}.knowledge")
    knowledge_mod.prompt_block = lambda *a, **k: ""
    _setter("knowledge", knowledge_mod)

    identity_mod = types.ModuleType(f"{pkg_name}.identity")
    identity_mod.resolve = lambda *a, **k: types.SimpleNamespace(person="Sam")
    identity_mod.subject_for = lambda ident: "person:Sam"
    _setter("identity", identity_mod)

    cognitive_core = types.ModuleType(f"{pkg_name}.cognitive_core")
    cognitive_core.get_pending_offer = lambda: None
    cognitive_core.log_command = lambda *a, **k: None
    _setter("cognitive_core", cognitive_core)

    connectivity = types.ModuleType(f"{pkg_name}.connectivity")
    connectivity.allow_request = lambda: True
    connectivity.record_success = lambda: None
    connectivity.record_failure = lambda: None
    _setter("connectivity", connectivity)

    ha_secrets = types.ModuleType(f"{pkg_name}.ha_secrets")
    async def get_provider_key(hass, provider):
        return f"key-for-{provider}"
    ha_secrets.async_get_provider_key = get_provider_key
    _setter("ha_secrets", ha_secrets)

    jarvis_config = types.ModuleType(f"{pkg_name}.jarvis_config")
    jarvis_config.runtime_get = lambda hass, entry, key, default=None: default
    jarvis_config.effective_config = lambda entry: {
        "custom_base_url": "https://persisted.example",
        "keep": "persisted",
        "empty": "persisted",
    }
    _setter("jarvis_config", jarvis_config)

    agent_mod = types.ModuleType(f"{pkg_name}.agent")
    async def run_agent(*args, **kwargs):
        return "A concise answer."
    agent_mod.run_agent = run_agent
    _setter("agent", agent_mod)

    continued = types.ModuleType(f"{pkg_name}.continued_conversation")
    continued.enabled = lambda: False
    continued.should_continue = lambda text: False
    continued.satellite_for_device = lambda hass, device_id: None
    continued.speaker_reopen_enabled = lambda: False
    continued.schedule_reopen = lambda *a, **k: None
    _setter("continued_conversation", continued)

    websocket = types.ModuleType(f"{pkg_name}.websocket")
    websocket.jarvis_log = lambda *a, **k: None
    _setter("websocket", websocket)

    voice_recognition = types.ModuleType(f"{pkg_name}.voice_recognition")
    voice_recognition.maybe_fire_enrollment = lambda *a, **k: None
    _setter("voice_recognition", voice_recognition)

    if f"{pkg_name}.conversation" in sys.modules:
        del sys.modules[f"{pkg_name}.conversation"]

    spec = importlib.util.spec_from_file_location(f"{pkg_name}.conversation", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.conversation"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_dispatch_agent(mod):
    class FakeHass:
        def __init__(self):
            self.data = {}
            self.config = types.SimpleNamespace(
                units=types.SimpleNamespace(temperature_unit="C"))
            self.states = types.SimpleNamespace(
                async_all=lambda domain: [],
                get=lambda entity_id: types.SimpleNamespace(state="idle"),
            )
            self.created_tasks = []

        async def async_add_executor_job(self, func, *args):
            return func(*args)

        def async_create_task(self, task):
            self.created_tasks.append(task)

    agent = object.__new__(mod.JarvisAgent)
    agent.hass = FakeHass()
    agent.entry = types.SimpleNamespace(entry_id="entry-1")
    agent._client = object()
    agent._histories = {}
    agent._threaded = set()
    agent._fallback_idx = 0
    agent._opt = lambda key, default=None: default
    agent._rt_opt = lambda key, default=None: default
    agent._honorific = lambda: "sir"
    agent._use_hass_api = lambda: False
    agent._media_playing_near = lambda device_id: False
    return agent


def test_real_runtime_helpers_execute_dedup_and_gate_branches(monkeypatch):
    mod = _load_conversation_module(monkeypatch)

    assert mod._dedup_key("Turn on the lights!") == "turn on the lights"
    assert mod._check_and_claim_dedup("Turn on the lights", "device-1") == (False, None)
    assert mod._check_and_claim_dedup("Turn on the lights", "device-2") == (True, None)
    mod._record_dedup_response("Turn on the lights", "done sir")
    assert mod._check_and_claim_dedup("Turn on the lights", "device-3") == (True, "done sir")

    assert mod._is_addressed_to_jarvis("turn on the lights") is True
    assert mod._is_addressed_to_jarvis("um, so you know", strict=True) is False
    assert mod._is_addressed_to_jarvis("what is the weather", strict=False) is True
    assert mod._is_connectivity_failure("Reasoning systems are connecting to the model") is True
    assert mod._is_connectivity_failure("Reasoning systems are stable") is False

    assert mod._ha_kwargs(type("X", (), {"__init__": lambda self, *, a, b, **kwargs: None}),
                          a=1, b=2, c=3) == {"a": 1, "b": 2}
    assert mod._ha_kwargs(type("Y", (), {"__init__": lambda self, *, a, **kwargs: None}),
                          a=1, c=3) == {"a": 1}


def test_agent_media_detector_executes_real_state_logic(monkeypatch):
    import sys

    mod = _load_conversation_module(monkeypatch)

    class FakeState:
        def __init__(self, state):
            self.state = state

    class FakeHass:
        def __init__(self):
            self.data = {}
            self.config = types.SimpleNamespace(units=types.SimpleNamespace(temperature_unit="C"))
            self.states = types.SimpleNamespace(
                get=lambda entity_id: {"media_player.movie": FakeState("playing")} .get(entity_id),
                async_all=lambda domain: [FakeState("playing")] if domain == "media_player" else [],
            )

    fake_hass = FakeHass()
    agent = object.__new__(mod.JarvisAgent)
    agent.hass = fake_hass
    agent.entry = types.SimpleNamespace(entry_id="entry-1")
    agent._rt_opt = lambda key, default=None: "media_player.movie" if key == "movie_media_player" else default

    assert agent._media_playing_near("device-1") is True

    fake_hass.states.get = lambda entity_id: {
        "media_player.tv": FakeState("playing")}.get(entity_id)
    agent._rt_opt = lambda key, default=None: default
    sys.modules[f"{mod.__package__}.continued_conversation"].satellite_for_device = (
        lambda hass, device_id: "assist_satellite.room")
    sys.modules[f"{mod.__package__}.audio_routing"].entity_area = (
        lambda hass, entity_id: "living_room")
    assert agent._media_playing_near("device-1") is True


def test_persona_cache_read_and_refresh_run_in_executor_flow(monkeypatch, tmp_path):
    mod = _load_conversation_module(monkeypatch)
    file_path = tmp_path / "jarvis_persona.txt"
    file_path.write_text("I am JARVIS.")
    mod.PERSONA_FILE = str(file_path)
    mod._persona_cache.update({"mtime": 0.0, "text": None, "checked": 0.0})

    mtime, text = mod._sync_load_persona()
    assert text == "I am JARVIS."
    assert mtime > 0

    class FakeHass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    async def _run():
        await mod._ensure_persona_loaded(FakeHass())

    import asyncio
    asyncio.run(_run())
    assert mod._persona_cache["text"] == "I am JARVIS."


def test_handle_message_local_success_builds_response_and_history(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)

    user_input = types.SimpleNamespace(
        text="turn on the lights",
        device_id=None,
        conversation_id="conversation-1",
        language="en",
        context={},
    )
    result = asyncio.run(agent._handle_message_impl(user_input))

    assert result.conversation_id == "conversation-1"
    assert result.response.speech == "Handled: turn on the lights"
    assert agent._histories["conversation-1"] == [
        {"role": "user", "content": "turn on the lights"},
        {"role": "assistant", "content": "Handled: turn on the lights"},
    ]


def test_agent_constructor_and_config_helpers_use_shared_client(monkeypatch):
    mod = _load_conversation_module(monkeypatch)
    client = types.SimpleNamespace(name="test-provider")
    hass = types.SimpleNamespace(data={"jarvis": {"entry-1": {"client": client}}})
    entry = types.SimpleNamespace(entry_id="entry-1")

    agent = mod.JarvisAgent(hass, entry)

    assert agent._client is client
    assert agent._attr_unique_id == "entry-1"
    assert agent._model() == "openai/gpt-oss-120b"
    assert agent._honorific() == "sir"
    assert agent._use_hass_api() is True


def test_speaker_helpers_parse_fallbacks_and_runtime_pairings(monkeypatch):
    import sys

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    options = {
        "announcement_speakers": '["assist_satellite.mic", "media_player.den"]',
        "voice_satellites": ["assist_satellite.mic"],
        "reply_speakers": ["media_player.den"],
        "broadcast_speakers": [],
        "cast_speakers": [],
        "room_routing": True,
    }
    agent._opt = lambda key, default=None: options.get(key, default)
    agent.hass.data = {"jarvis": {"entry-1": {
        "runtime_config": {"satellite_pairings": '{"sat-1": "media_player.den"}'},
    }}}
    routed = {}

    def reply_targets(hass, **kwargs):
        routed.update(kwargs)
        return ["media_player.den"]

    mod.reply_targets = reply_targets

    assert agent._broadcast_fallback_speakers() == ["media_player.den"]
    assert agent._speakers("sat-1") == ["media_player.den"]
    assert routed["satellite_pairings"] == {"sat-1": "media_player.den"}
    assert routed["room_routing"] is True


def test_handle_message_routes_reply_to_configured_speaker(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    user_input = types.SimpleNamespace(
        text="turn on the lights", device_id="sat-1",
        conversation_id="routed", language="en", context={})

    result = asyncio.run(agent._handle_message_impl(user_input))

    assert result.response.speech == ""
    assert result.conversation_id == "routed"
    assert len(agent.hass.created_tasks) == 1


def test_history_is_bounded_and_cross_session_seeded_once(monkeypatch):
    import asyncio
    import sys

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    agent._histories["long"] = [
        {"role": "user", "content": str(index)} for index in range(25)]

    bounded = agent._history("long")
    assert len(bounded) == mod.MAX_HISTORY
    assert bounded[0]["content"] == "5"

    memory_thread = sys.modules[f"{mod.__package__}.memory_thread"]
    calls = []
    memory_thread.config = lambda: (True, 24, 5)

    async def load_recent(hass, hours, limit):
        calls.append((hours, limit))
        return [{"role": "assistant", "content": "Earlier context"}]

    memory_thread.load_recent = load_recent
    history = []

    async def seed_twice():
        await agent._maybe_seed_history("fresh", history)
        await agent._maybe_seed_history("fresh", history)

    asyncio.run(seed_twice())

    assert history == [{"role": "assistant", "content": "Earlier context"}]
    assert calls == [(24, 5)]


def test_get_hass_api_returns_api_or_degrades_to_chat_only(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    user_input = types.SimpleNamespace(
        context={}, text="turn on the lights", language="en", device_id="sat-1")
    api = types.SimpleNamespace(tools=[])

    async def get_api(hass, domain, context):
        assert domain == "assist"
        assert context.user_prompt == user_input.text
        return api

    mod.llm.async_get_api = get_api
    assert asyncio.run(agent._get_hass_api(user_input)) is api

    async def fail_api(*args):
        raise RuntimeError("Assist API unavailable")

    mod.llm.async_get_api = fail_api
    assert asyncio.run(agent._get_hass_api(user_input)) is None


def test_agentic_loop_executes_tool_and_returns_followup_text(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)

    class FakeClient:
        def __init__(self):
            self.results = [
                {"tool_calls": [{"id": "call-1", "name": "turn_on",
                                 "args": {"entity_id": "light.study"}}], "text": ""},
                {"tool_calls": [], "text": "The study light is on."},
            ]

        def chat(self, **kwargs):
            return self.results.pop(0)

    class FakeApi:
        tools = [types.SimpleNamespace(
            name="turn_on", description="Turn on an entity", parameters={})]

        async def async_call_tool(self, tool_input):
            assert tool_input.tool_name == "turn_on"
            assert tool_input.tool_args == {"entity_id": "light.study"}
            return {"success": True}

    agent._client = FakeClient()
    user_input = types.SimpleNamespace(
        text="turn on the study light", language="en", device_id=None, context={})
    result = asyncio.run(agent._agentic_loop(
        [{"role": "user", "content": user_input.text}], "persona", FakeApi(), user_input))

    assert result == "The study light is on."
    assert agent._client.results == []


def test_agentic_loop_uses_plain_chat_without_tools(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)

    class FakeClient:
        def chat(self, **kwargs):
            assert kwargs["messages"][0] == {"role": "system", "content": "persona"}
            assert "tools" not in kwargs
            return {"text": "A plain response."}

    agent._client = FakeClient()
    result = asyncio.run(agent._agentic_loop(
        [{"role": "user", "content": "hello"}], "persona", None,
        types.SimpleNamespace()))

    assert result == "A plain response."


def test_async_setup_entry_uses_shared_or_deferred_provider(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    entry = types.SimpleNamespace(entry_id="entry-1")
    shared_client = object()

    class FakeHass:
        def __init__(self, data):
            self.data = data

        async def async_add_executor_job(self, func, *args):
            return func(*args)

    async def setup(data):
        hass = FakeHass(data)
        added = []
        await mod.async_setup_entry(hass, entry, added.extend)
        return added

    shared = asyncio.run(setup({"jarvis": {"entry-1": {"client": shared_client}}}))
    assert len(shared) == 1
    assert shared[0]._client is shared_client

    created = object()
    provider_calls = []

    def create_provider(*args):
        provider_calls.append(args)
        return created

    mod.create_provider = create_provider
    deferred = asyncio.run(setup({}))
    assert len(deferred) == 1
    assert deferred[0]._client is created
    assert provider_calls == [(
        "groq", "key-for-groq", "openai/gpt-oss-120b", "https://persisted.example")]


def test_handle_message_silences_duplicate_and_ambient_turns(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    mod._check_and_claim_dedup("turn on the lights", "winner")
    duplicate = types.SimpleNamespace(
        text="turn on the lights", device_id="other", conversation_id="dup",
        language="en", context={})
    duplicate_result = asyncio.run(agent._handle_message_impl(duplicate))
    assert duplicate_result.response.speech == ""
    assert duplicate_result.conversation_id == "dup"

    ambient = types.SimpleNamespace(
        text="um so", device_id=None,
        conversation_id="ambient", language="en", context={})
    ambient_result = asyncio.run(agent._handle_message_impl(ambient))
    assert ambient_result.response.speech == ""
    assert ambient_result.conversation_id == "ambient"


def test_public_handler_converts_unexpected_exception_to_spoken_error(monkeypatch):
    import asyncio

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)

    async def fail_impl(user_input, chat_log=None):
        raise RuntimeError("unexpected failure")

    agent._handle_message_impl = fail_impl
    user_input = types.SimpleNamespace(
        text="hello", device_id=None, conversation_id="crashed",
        language="en", context={})

    result = asyncio.run(agent._async_handle_message(user_input))

    assert result.conversation_id == "crashed"
    assert result.response.speech == "I ran into an internal error handling that request."


def test_handle_message_uses_offline_response_when_local_salvage_fails(monkeypatch):
    import asyncio
    import sys

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    local_engine = sys.modules[f"{mod.__package__}.local_engine"]
    connectivity = sys.modules[f"{mod.__package__}.connectivity"]
    calls = []

    async def unhandled_local(hass, text, honorific, force=False):
        calls.append(force)
        return types.SimpleNamespace(handled=False, text="")

    local_engine.try_local = unhandled_local
    connectivity.allow_request = lambda: False
    user_input = types.SimpleNamespace(
        text="explain the history of the Roman empire", device_id=None,
        conversation_id="offline", language="en", context={})

    result = asyncio.run(agent._handle_message_impl(user_input))

    assert calls == [False, True]
    assert "offline at the moment" in result.response.speech
    assert result.conversation_id == "offline"


def test_handle_message_escalates_with_merged_runtime_config(monkeypatch):
    import asyncio
    import sys

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    runtime_config = {
        "custom_base_url": "https://live.example",
        "keep": "live",
        "empty": "",
    }
    agent.hass.data = {"jarvis": {"entry-1": {"runtime_config": runtime_config}}}
    options = {
        "llm_provider": "custom",
        "model": "live-model",
        "custom_base_url": "https://live.example",
        "ollama_base_url": "",
        "llm_base_url": "",
    }
    agent._rt_opt = lambda key, default=None: options.get(key, default)
    local_engine = sys.modules[f"{mod.__package__}.local_engine"]
    async def unhandled_local(*args, **kwargs):
        return types.SimpleNamespace(handled=False, text="")
    local_engine.try_local = unhandled_local

    calls = []
    agent_mod = sys.modules[f"{mod.__package__}.agent"]
    async def capture_agent(hass, **kwargs):
        calls.append(kwargs)
        return "A concise answer."
    agent_mod.run_agent = capture_agent
    user_input = types.SimpleNamespace(
        text="what is the history of the Roman empire", device_id=None,
        conversation_id="escalated", language="en", context={})

    result = asyncio.run(agent._handle_message_impl(user_input))

    assert result.response.speech == "A concise answer."
    assert calls[0]["provider_name"] == "custom"
    assert calls[0]["api_key"] == "key-for-custom"
    assert calls[0]["model"] == "live-model"
    assert calls[0]["base_url"] == "https://live.example"
    assert calls[0]["config"] == {
        "custom_base_url": "https://live.example",
        "keep": "live",
        "empty": "persisted",
    }


def test_handle_message_salvages_when_agent_raises(monkeypatch):
    import asyncio
    import sys

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    local_engine = sys.modules[f"{mod.__package__}.local_engine"]
    calls = []

    async def local_sequence(hass, text, honorific, force=False):
        calls.append(force)
        return types.SimpleNamespace(
            handled=force, text="Local salvage succeeded" if force else "")

    local_engine.try_local = local_sequence
    agent_mod = sys.modules[f"{mod.__package__}.agent"]

    async def fail_agent(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    agent_mod.run_agent = fail_agent
    user_input = types.SimpleNamespace(
        text="what is the history of the Roman empire", device_id=None,
        conversation_id="salvaged", language="en", context={})

    result = asyncio.run(agent._handle_message_impl(user_input))

    assert calls == [False, True]
    assert result.response.speech == "Local salvage succeeded"


def test_handle_message_accepts_and_declines_pending_offers(monkeypatch):
    import asyncio
    import sys

    mod = _load_conversation_module(monkeypatch)
    agent = _make_dispatch_agent(mod)
    cognitive_core = sys.modules[f"{mod.__package__}.cognitive_core"]
    cognitive_core.get_pending_offer = lambda: {"offer": "routine"}

    async def accept_offer():
        return {"ok": True, "approvals": 2}

    declined = []

    async def decline_offer():
        declined.append(True)

    cognitive_core.accept_pending_offer = accept_offer
    cognitive_core.async_decline_pending_offer = decline_offer
    accepted = types.SimpleNamespace(
        text="yes", device_id=None, conversation_id="accepted",
        language="en", context={})
    accepted_result = asyncio.run(agent._handle_message_impl(accepted))
    assert accepted_result.response.speech == (
        "Done, sir. (A couple more times and I'll handle this automatically.)")

    declined_input = types.SimpleNamespace(
        text="no", device_id=None, conversation_id="declined",
        language="en", context={})
    declined_result = asyncio.run(agent._handle_message_impl(declined_input))
    assert declined_result.response.speech == "Understood, sir. I'll leave it."
    assert declined == [True]


def _load_dedup_helpers():
    """Exec the dedup/connectivity helpers in isolation so they can be tested
    without importing the full Home Assistant entity stack."""
    import ast, types
    src = SRC.read_text()
    tree = ast.parse(src)
    mod = types.ModuleType("conv_dedup_stub")
    mod.time = __import__("time")
    want_fns = {"_dedup_key", "_check_and_claim_dedup", "_record_dedup_response", "_is_connectivity_failure"}
    want_assign = {"_DEDUP_WINDOW", "_dedup_cache"}
    for n in tree.body:
        seg = None
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want_assign for t in n.targets):
            seg = ast.get_source_segment(src, n)
        elif isinstance(n, ast.FunctionDef) and n.name in want_fns:
            seg = ast.get_source_segment(src, n)
        if seg is not None:
            exec(compile(seg, "<conv_dedup>", "exec"), mod.__dict__)
    mod._DEDUP_WINDOW = 4.0
    mod._dedup_cache = {}
    return mod


def test_dedup_claims_first_pipeline_and_caches_the_response():
    mod = _load_dedup_helpers()
    text = "Turn on the living room lights"

    assert mod._check_and_claim_dedup(text, "device-a") == (False, None)
    assert mod._check_and_claim_dedup(text, "device-b") == (True, None)

    mod._record_dedup_response(text, "Done, sir.")
    assert mod._check_and_claim_dedup(text, "device-c") == (True, "Done, sir.")
    assert mod._check_and_claim_dedup(text, "device-a") == (False, None)


def test_dedup_expires_stale_entries_and_allows_new_claims():
    mod = _load_dedup_helpers()
    text = "Lock the front door"
    key = mod._dedup_key(text)
    mod._dedup_cache[key] = (mod.time.time() - mod._DEDUP_WINDOW * 3, None, "device-1")

    assert mod._check_and_claim_dedup(text, "device-2") == (False, None)
    assert mod._dedup_cache[key][2] == "device-2"


def test_connectivity_failure_sentinel_only_matches_real_failure_phrases():
    mod = _load_dedup_helpers()
    assert mod._is_connectivity_failure(
        "I am unable to reach my reasoning systems because of connectivity issues while connecting to the model."
    ) is True
    assert mod._is_connectivity_failure("My reasoning systems are working fine.") is False
    assert mod._is_connectivity_failure("") is True
