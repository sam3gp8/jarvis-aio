"""Vision provider handling (issue #17): text-only model for vision + the
blocking provider construction.

- _make_client caches a provider per (provider, model, key, base_url), so a
  provider is constructed once (callers run it in an executor, so this keeps the
  blocking SSL setup off the hot path).
- _vision_model_rejects_images turns the Groq/OpenAI 400 for a text-only model
  ("messages[1].content must be a string") into an actionable signal.
"""
import sys
import types

import pytest


@pytest.fixture
def cam(load, monkeypatch):
    if "aiohttp" not in sys.modules:
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    return load("cam" if False else "camera")


class _Entry:
    entry_id = "e1"
    options: dict = {}
    data = {"api_key": "k", "llm_base_url": ""}


class _Entries:
    def async_entries(self, domain):
        return [_Entry()]


class _Hass:
    def __init__(self):
        self.data = {}
        self.config_entries = _Entries()


def test_make_client_caches_provider(cam, monkeypatch):
    cam._PROVIDER_CACHE.clear()
    calls = {"n": 0}

    def fake_create(provider, api_key, model, base_url):
        calls["n"] += 1
        return types.SimpleNamespace(provider=provider, model=model)

    import importlib
    lp = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".llm_provider")
    monkeypatch.setattr(lp, "create_provider", fake_create)

    hass = _Hass()
    c1 = cam._make_client(hass, "groq", "some/vision-model", fallback="FB")
    c2 = cam._make_client(hass, "groq", "some/vision-model", fallback="FB")
    assert c1 is c2                      # cached — same instance
    assert calls["n"] == 1               # constructed once
    # a different model is a different cache entry
    cam._make_client(hass, "groq", "other-model", fallback="FB")
    assert calls["n"] == 2


def test_make_client_returns_fallback_without_key(cam):
    cam._PROVIDER_CACHE.clear()

    class _NoKeyEntry(_Entry):
        data = {"api_key": "", "groq_api_key": ""}

    class _NoKeyEntries:
        def async_entries(self, domain):
            return [_NoKeyEntry()]

    hass = _Hass()
    hass.config_entries = _NoKeyEntries()
    assert cam._make_client(hass, "groq", "m", fallback="FB") == "FB"


def test_vision_model_rejects_images_classifier(cam):
    groq_400 = ("Error code: 400 - {'error': {'message': "
                "'messages[1].content must be a string', 'type': "
                "'invalid_request_error'}}")
    assert cam._vision_model_rejects_images(Exception(groq_400)) is True
    assert cam._vision_model_rejects_images(Exception("rate limit exceeded")) is False
    assert cam._vision_model_rejects_images(Exception("connection reset")) is False
