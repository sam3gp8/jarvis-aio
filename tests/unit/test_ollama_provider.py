"""Tests for the Ollama provider tuning (v6.32.0)."""
import pytest


@pytest.fixture
def llm(load):
    return load("llm_provider")


def test_ollama_registered_as_its_own_provider(llm):
    assert llm.PROVIDERS["ollama"] is llm.OllamaProvider
    assert issubclass(llm.OllamaProvider, llm.OpenAIProvider)


def test_ollama_extra_body_keeps_model_resident_and_raises_context(llm):
    extra = llm.OllamaProvider._extra_body(None)   # self is unused
    assert extra["keep_alive"] == llm.OLLAMA_KEEP_ALIVE
    assert extra["options"]["num_ctx"] == llm.OLLAMA_NUM_CTX
    assert llm.OLLAMA_NUM_CTX > 2048   # above Ollama's small default


def test_vanilla_openai_sends_no_extra_body(llm):
    assert llm.OpenAIProvider._extra_body(None) == {}


# ── Gemma native function-calling recovery ───────────────────────────────────

def test_parse_gemma_full_token_form(llm):
    # The exact format from ai.google.dev/gemma function-calling docs, incl. a
    # comma inside a quoted string value.
    text = ('<|tool_call>call:get_current_weather'
            '{location:<|"|>Tokyo, JP<|"|>}<tool_call|>')
    calls, cleaned = llm.parse_gemma_tool_calls(text, {"get_current_weather"})
    assert calls == [{"id": "gemma_call_0", "name": "get_current_weather",
                      "args": {"location": "Tokyo, JP"}}]
    assert cleaned == ""


def test_parse_gemma_stripped_form_and_casts(llm):
    # If Ollama strips the special tokens, the call arrives as plain text.
    text = "Sure. call:control_device{action:turn_on,entity_id:light.den,brightness:200}"
    calls, _cleaned = llm.parse_gemma_tool_calls(text, {"control_device"})
    assert calls[0]["name"] == "control_device"
    assert calls[0]["args"] == {"action": "turn_on", "entity_id": "light.den",
                                "brightness": 200}


def test_parse_gemma_gated_on_declared_tool_names(llm):
    # A call naming something that isn't a declared tool is NOT recovered, so
    # ordinary prose can never masquerade as a tool call.
    calls, cleaned = llm.parse_gemma_tool_calls("call:rm_rf{path:/}", {"control_device"})
    assert calls == []
    assert cleaned == "call:rm_rf{path:/}"


def test_parse_gemma_absent_passes_text_through(llm):
    calls, cleaned = llm.parse_gemma_tool_calls("A normal reply, sir.", {"x"})
    assert calls == [] and cleaned == "A normal reply, sir."


def test_ollama_chat_recovers_gemma_tool_call(llm, monkeypatch):
    prov = object.__new__(llm.OllamaProvider)
    prov.name, prov.model = "ollama", "gemma4:26b"

    def fake_super_chat(self, messages, tools=None, *a, **k):
        return {"text": '<|tool_call>call:control_device'
                        '{action:<|"|>turn_on<|"|>,entity_id:<|"|>light.den<|"|>}<tool_call|>',
                "tool_calls": [], "raw": None}

    monkeypatch.setattr(llm.OpenAIProvider, "chat", fake_super_chat)
    tools = [{"type": "function",
              "function": {"name": "control_device", "description": "", "parameters": {}}}]
    res = prov.chat([{"role": "user", "content": "turn on den"}], tools=tools)
    assert res["tool_calls"][0]["name"] == "control_device"
    assert res["tool_calls"][0]["args"] == {"action": "turn_on", "entity_id": "light.den"}
    assert "tool_call" not in res["text"]   # call syntax stripped from the reply


def test_ollama_chat_leaves_real_tool_calls_alone(llm, monkeypatch):
    prov = object.__new__(llm.OllamaProvider)
    prov.name, prov.model = "ollama", "llama3.1"
    sentinel = [{"id": "c1", "name": "x", "args": {}}]

    def fake_super_chat(self, messages, tools=None, *a, **k):
        return {"text": "ok", "tool_calls": sentinel, "raw": None}

    monkeypatch.setattr(llm.OpenAIProvider, "chat", fake_super_chat)
    res = prov.chat([{"role": "user", "content": "hi"}],
                    tools=[{"type": "function", "function": {"name": "x"}}])
    assert res["tool_calls"] is sentinel       # not re-parsed
    assert res["text"] == "ok"


def test_is_ollama_toolparse_error(llm):
    assert llm._is_ollama_toolparse_error(
        ValueError("invalid character after object key:value pair"))
    assert llm._is_ollama_toolparse_error(
        RuntimeError("json: cannot unmarshal tool_calls"))
    assert not llm._is_ollama_toolparse_error(ConnectionError("connection refused"))
    assert not llm._is_ollama_toolparse_error(TimeoutError("timed out"))


def test_ollama_chat_warns_once_on_toolparse_error_and_reraises(llm, monkeypatch):
    prov = object.__new__(llm.OllamaProvider)
    prov.name, prov.model = "ollama", "gemma4:31b"
    monkeypatch.setattr(llm, "_OLLAMA_TOOLPARSE_WARNED", False)

    def boom(self, *a, **k):
        raise ValueError("invalid character after object key:value pair")

    monkeypatch.setattr(llm.OpenAIProvider, "chat", boom)
    tools = [{"type": "function", "function": {"name": "x"}}]
    with pytest.raises(ValueError):
        prov.chat([{"role": "user", "content": "hi"}], tools=tools)
    assert llm._OLLAMA_TOOLPARSE_WARNED is True


@pytest.mark.parametrize("inp,out", [
    ("http://haos.local:11434", "http://haos.local:11434/v1"),
    ("http://haos.local:11434/", "http://haos.local:11434/v1"),
    ("http://haos.local:11434/v1", "http://haos.local:11434/v1"),  # already pathed
    # Any path-less base URL gains /v1 — Ollama's OpenAI-compatible API always
    # lives under /v1, so a reverse proxy on another port or host needs it too
    # (issue #383 follow-up). Previously only a bare :11434 was handled, leaving a
    # proxied endpoint pointing at the wrong path.
    ("http://x:8080", "http://x:8080/v1"),
    ("https://ollama.example.com", "https://ollama.example.com/v1"),
    # A URL that already carries a path (a deliberate proxy subpath) is untouched.
    ("https://host.tld/ollama", "https://host.tld/ollama"),
    (None, None),
])
def test_ollama_url_normalization(llm, inp, out):
    assert llm.OllamaProvider._normalize_url(inp) == out
