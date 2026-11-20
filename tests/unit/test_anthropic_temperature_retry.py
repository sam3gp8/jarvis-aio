"""Newer Claude models (e.g. claude-sonnet-5) reject `temperature` outright
("temperature is deprecated for this model") instead of just clamping it.
chat() must retry once without it rather than surfacing a 400 and falling
back to a different provider entirely."""
import pytest


@pytest.fixture
def provider(load):
    llm = load("llm_provider")
    # Bypass __init__ (it imports the real `anthropic` package) and wire up
    # a fake client directly — only chat()'s retry logic is under test.
    p = object.__new__(llm.AnthropicProvider)
    p.model = "claude-sonnet-5"
    p.base_url = None
    return p


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Resp:
    def __init__(self, text="ok"):
        self.content = [_Block("text", text)]


class _FakeMessages:
    def __init__(self, fail_once_on_temperature=False):
        self._fail_once = fail_once_on_temperature
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_once and "temperature" in kwargs:
            raise Exception(
                "Error code: 400 - {'type': 'error', 'error': {'type': "
                "'invalid_request_error', 'message': '`temperature` is "
                "deprecated for this model.'}}"
            )
        return _Resp()


class _FakeClient:
    def __init__(self, fail_once_on_temperature=False):
        self.messages = _FakeMessages(fail_once_on_temperature)


def test_retries_without_temperature_on_deprecation_error(provider):
    provider._client = _FakeClient(fail_once_on_temperature=True)
    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert result["text"] == "ok"
    calls = provider._client.messages.calls
    assert len(calls) == 2
    assert "temperature" in calls[0]           # first attempt included it
    assert "temperature" not in calls[1]       # retry dropped it


def test_retries_without_temperature_when_sdk_rejects_the_kwarg(provider):
    """Newer `anthropic` SDKs removed `temperature` from Messages.create(), so
    the call raises a client-side TypeError instead of an API deprecation
    error. That must take the same retry-without-temperature path."""
    class _SdkWithoutTemperature(_FakeMessages):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "temperature" in kwargs:
                raise TypeError(
                    "Messages.create() got an unexpected keyword argument "
                    "'temperature'"
                )
            return _Resp()

    provider._client = _FakeClient()
    provider._client.messages = _SdkWithoutTemperature()
    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert result["text"] == "ok"
    calls = provider._client.messages.calls
    assert len(calls) == 2
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_does_not_retry_on_unrelated_typeerror(provider):
    class _BadKwarg(_FakeMessages):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise TypeError("Messages.create() got an unexpected keyword argument 'foo'")

    provider._client = _FakeClient()
    provider._client.messages = _BadKwarg()
    with pytest.raises(TypeError, match="'foo'"):
        provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert len(provider._client.messages.calls) == 1


def test_does_not_retry_on_unrelated_error(provider):
    class _AlwaysFails(_FakeMessages):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise Exception("401 invalid api key")

    provider._client = _FakeClient()
    provider._client.messages = _AlwaysFails()
    with pytest.raises(Exception, match="invalid api key"):
        provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert len(provider._client.messages.calls) == 1   # no pointless retry


def test_normal_call_unaffected(provider):
    provider._client = _FakeClient(fail_once_on_temperature=False)
    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert result["text"] == "ok"
    assert len(provider._client.messages.calls) == 1
