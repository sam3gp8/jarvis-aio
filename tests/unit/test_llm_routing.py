"""Tests for v6.47.1: Ollama-tag routing correction and model-not-found
fallback classification — born from a live 'models/gemma4:26b is not found'
404 from Google's API after the GPU server's local model name was configured
against a cloud provider."""
import pytest


@pytest.fixture
def llm(load):
    return load("llm_provider")


@pytest.fixture
def agent_mod(load):
    return load("agent")


# ── normalize_routing ────────────────────────────────────────────────────────

def test_ollama_tag_on_cloud_provider_reroutes(llm):
    p, url, note = llm.normalize_routing("gemini", "gemma4:26b", None)
    assert p == "ollama"
    assert note and "gemma4:26b" in note and "ollama" in note


def test_ollama_tag_keeps_configured_base_url(llm):
    p, url, note = llm.normalize_routing(
        "groq", "llama3.3:70b", "http://gpu.local:11434/v1")
    assert p == "ollama"
    assert url == "http://gpu.local:11434/v1"


def test_cloud_model_on_cloud_provider_untouched(llm):
    for provider, model in (
        ("gemini", "gemini-2.0-flash"),
        ("groq", "llama-3.3-70b-versatile"),
        ("anthropic", "claude-sonnet-4-6"),
    ):
        p, _url, note = llm.normalize_routing(provider, model, None)
        assert p == provider and note is None, (provider, model)


def test_ollama_provider_untouched(llm):
    p, _url, note = llm.normalize_routing("ollama", "gemma4:26b", None)
    assert p == "ollama" and note is None


def test_custom_provider_untouched(llm):
    # Explicit custom endpoints may legitimately use colon-tagged names.
    p, _url, note = llm.normalize_routing("custom", "mymodel:latest", None)
    assert p == "custom" and note is None


def test_non_tag_colon_forms_not_rerouted(llm):
    # Paths/URLs with colons must not be mistaken for Ollama tags.
    p, _url, note = llm.normalize_routing("gemini", "models/gemini:pro extra", None)
    assert p == "gemini" and note is None


# ── _is_model_not_found ──────────────────────────────────────────────────────

def test_model_not_found_detects_google_404(agent_mod):
    exc = Exception(
        "Error code: 404 - [{'error': {'code': 404, 'message': "
        "'models/gemma4:26b is not found for API version v1main, or is not "
        "supported for generateContent.', 'status': 'NOT_FOUND'}}]")
    assert agent_mod._is_model_not_found(exc) is True


def test_model_not_found_ignores_connectivity(agent_mod):
    assert agent_mod._is_model_not_found(Exception("Connection timed out")) is False
    assert agent_mod._is_model_not_found(Exception("503 service unavailable")) is False


def test_model_not_found_vs_connectivity_disjoint(agent_mod):
    exc = Exception("404 model does not exist")
    assert agent_mod._is_model_not_found(exc) is True
    assert agent_mod._is_connectivity_error(exc) is False
