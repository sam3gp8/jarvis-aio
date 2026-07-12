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


@pytest.mark.parametrize("inp,out", [
    ("http://haos.local:11434", "http://haos.local:11434/v1"),
    ("http://haos.local:11434/", "http://haos.local:11434/v1"),
    ("http://haos.local:11434/v1", "http://haos.local:11434/v1"),  # already pathed
    ("http://x:8080", "http://x:8080"),                            # not ollama's port
    (None, None),
])
def test_ollama_url_normalization(llm, inp, out):
    assert llm.OllamaProvider._normalize_url(inp) == out
