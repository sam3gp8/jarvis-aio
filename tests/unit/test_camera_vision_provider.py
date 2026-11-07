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
    hs = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".ha_secrets")
    monkeypatch.setattr(hs, "get_provider_key_sync", lambda provider: "k")

    hass = _Hass()
    c1 = cam._make_client(hass, "groq", "some/vision-model", fallback="FB")
    c2 = cam._make_client(hass, "groq", "some/vision-model", fallback="FB")
    assert c1 is c2                      # cached — same instance
    assert calls["n"] == 1               # constructed once
    # a different model is a different cache entry
    cam._make_client(hass, "groq", "other-model", fallback="FB")
    assert calls["n"] == 2


def test_make_client_tags_vision_tier(cam, monkeypatch):
    """Camera vision clients are tagged tier='vision' so token telemetry's
    by_tier split attributes camera spend to the vision tier (MCU Tranche 1 —
    token cost accounting), instead of landing as 'unknown'."""
    cam._PROVIDER_CACHE.clear()

    def fake_create(provider, api_key, model, base_url):
        return types.SimpleNamespace(provider=provider, model=model)

    import importlib
    lp = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".llm_provider")
    monkeypatch.setattr(lp, "create_provider", fake_create)
    hs = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".ha_secrets")
    monkeypatch.setattr(hs, "get_provider_key_sync", lambda provider: "k")

    hass = _Hass()
    client = cam._make_client(hass, "groq", "some/vision-model", fallback="FB")
    assert getattr(client, "tier", None) == "vision"


def test_make_client_vision_tag_failure_is_nonfatal(cam, monkeypatch):
    """A client that refuses the .tier attribute must not break provider
    creation — the telemetry tag is strictly best-effort."""
    cam._PROVIDER_CACHE.clear()

    class _NoAttr:
        __slots__ = ()                       # cannot set .tier → AttributeError

    def fake_create(provider, api_key, model, base_url):
        return _NoAttr()

    import importlib
    lp = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".llm_provider")
    monkeypatch.setattr(lp, "create_provider", fake_create)
    hs = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".ha_secrets")
    monkeypatch.setattr(hs, "get_provider_key_sync", lambda provider: "k")

    hass = _Hass()
    client = cam._make_client(hass, "groq", "m", fallback="FB")
    assert isinstance(client, _NoAttr)        # returned despite the tag failing


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


def test_make_client_does_not_apply_shared_url_to_cloud_provider(cam, monkeypatch):
    cam._PROVIDER_CACHE.clear()
    seen = {}

    def fake_create(provider, api_key, model, base_url):
        seen["base_url"] = base_url
        return types.SimpleNamespace(provider=provider, model=model)

    import importlib
    lp = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".llm_provider")
    monkeypatch.setattr(lp, "create_provider", fake_create)
    hs = importlib.import_module(cam.__name__.rsplit(".", 1)[0] + ".ha_secrets")
    monkeypatch.setattr(hs, "get_provider_key_sync", lambda provider: "k")

    hass = _Hass()
    _Entry.data = {"api_key": "k", "llm_base_url": "http://stale:11434/v1"}
    cam._make_client(hass, "groq", "some/vision-model", fallback="FB")
    assert seen["base_url"] is None


async def test_async_make_client_uses_executor(cam, fake_hass, monkeypatch):
    monkeypatch.setattr(cam, "_make_client", lambda *a, **k: "CLIENT")
    assert await cam.async_make_client(fake_hass, "groq", "m", "FB") == "CLIENT"


def test_vision_model_rejects_images_classifier(cam):
    groq_400 = ("Error code: 400 - {'error': {'message': "
                "'messages[1].content must be a string', 'type': "
                "'invalid_request_error'}}")
    assert cam._vision_model_rejects_images(Exception(groq_400)) is True
    assert cam._vision_model_rejects_images(Exception("rate limit exceeded")) is False
    assert cam._vision_model_rejects_images(Exception("connection reset")) is False
