"""Tests for proactive briefing triggers, context, and delivery."""
import sys
import types

import pytest


@pytest.fixture
def pb(load):
    module = load("proactive_briefing")
    module._SNAPSHOTS.clear()
    module._STATE = module._ProactiveState()
    yield module
    module._SNAPSHOTS.clear()
    module._STATE = module._ProactiveState()


def _event(entity_id, new_state, old_state=None):
    from homeassistant.core import Event

    return Event(data={
        "entity_id": entity_id,
        "new_state": new_state,
        "old_state": old_state,
    })


def test_snapshot_ring_buffer_filters_groups_and_truncates(pb, monkeypatch):
    now = 10_000
    monkeypatch.setattr(pb.time, "time", lambda: now)
    for index in range(49):
        pb.record_snapshot("Front", "camera.front", f"person {index}", "person")
    pb.record_snapshot("Drive", "camera.drive", "car", "vehicle")
    pb._SNAPSHOTS[0] = pb.CameraSnapshot(
        timestamp=now - 13 * 3600,
        camera_name="Old",
        camera_entity="camera.old",
        analysis="stale",
        detection_type="motion",
    )

    recent = pb.get_recent_snapshots(hours=1)
    assert len(recent) == 49
    summary = pb.get_snapshot_summary(hours=1)
    assert "Front: 48 detection(s) (48 person)" in summary
    assert "Latest: person 48" in summary
    assert "Drive: 1 detection(s) (1 vehicle)" in summary
    assert "Old" not in summary

    pb._SNAPSHOTS.clear()
    pb.record_snapshot("Front", "camera.front", "x" * 130)
    assert pb.get_snapshot_summary().endswith("Latest: " + "x" * 120)


def test_snapshot_summary_is_empty_without_recent_detections(pb, monkeypatch):
    monkeypatch.setattr(pb.time, "time", lambda: 100_000)
    pb._SNAPSHOTS.append(pb.CameraSnapshot(
        timestamp=1,
        camera_name="Front",
        camera_entity="camera.front",
        analysis="old",
        detection_type="motion",
    ))
    assert pb.get_snapshot_summary() == ""


def test_anyone_home_checks_people_and_devices_and_fails_closed(pb, fake_hass):
    fake_hass.states.set("device_tracker.phone", "home")
    assert pb._anyone_home(fake_hass)
    fake_hass.states.set("device_tracker.phone", "not_home")
    assert not pb._anyone_home(fake_hass)

    class BrokenStates:
        def async_all(self, _domain):
            raise RuntimeError("state registry unavailable")

    assert not pb._anyone_home(types.SimpleNamespace(states=BrokenStates()))


@pytest.mark.asyncio
async def test_start_replaces_running_listener_and_stop_unsubscribes(pb, fake_hass):
    unsubscribed = []
    first = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda *_: lambda: unsubscribed.append(True)),
    )
    await pb.start(first, {"honorific": "captain"})
    assert pb._STATE.running
    assert pb._STATE.config == {"honorific": "captain"}

    await pb.start(fake_hass, {})
    assert unsubscribed == [True]
    assert pb._STATE.hass is fake_hass
    assert pb._STATE.running
    await pb.stop()
    assert unsubscribed == [True]
    assert not pb._STATE.running


@pytest.mark.asyncio
async def test_state_handler_ignores_disabled_and_missing_state(pb, monkeypatch):
    called = []

    async def trigger(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr(pb, "_trigger_briefing", trigger)
    pb._on_state_changed(_event("person.sam", types.SimpleNamespace(state="home")))
    pb._STATE.running = True
    pb._on_state_changed(_event("person.sam", None))
    assert called == []


@pytest.mark.asyncio
async def test_arrival_triggers_named_welcome_once_after_cooldown(pb, fake_hass, monkeypatch):
    now = 1_000_000
    monkeypatch.setattr(pb.time, "time", lambda: now)
    calls = []

    async def trigger(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(pb, "_trigger_briefing", trigger)
    pb._STATE.hass = fake_hass
    pb._STATE.running = True
    event = _event(
        "person.sam",
        types.SimpleNamespace(state="home", attributes={"friendly_name": "Sam"}),
        types.SimpleNamespace(state="not_home"),
    )
    pb._on_state_changed(event)
    await fake_hass.drain()
    pb._on_state_changed(event)
    await fake_hass.drain()
    assert calls == [(("arrival",), {"person_name": "Sam"})]


@pytest.mark.asyncio
async def test_arrival_uses_entity_name_and_ignores_non_arrival(pb, fake_hass, monkeypatch):
    calls = []

    async def trigger(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(pb, "_trigger_briefing", trigger)
    pb._STATE.hass = fake_hass
    pb._STATE.running = True
    pb._on_state_changed(_event(
        "person.jane_doe", types.SimpleNamespace(state="home", attributes={}),
        types.SimpleNamespace(state="home"),
    ))
    pb._on_state_changed(_event(
        "person.jane_doe", types.SimpleNamespace(state="home", attributes={}), None,
    ))
    await fake_hass.drain()
    assert calls == [(("arrival",), {"person_name": "Jane_Doe"})]


@pytest.mark.asyncio
async def test_security_events_require_empty_home_and_trigger_at_threshold(
    pb, fake_hass, monkeypatch,
):
    now = 1_000_000
    monkeypatch.setattr(pb.time, "time", lambda: now)
    calls = []

    async def trigger(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(pb, "_trigger_briefing", trigger)
    pb._STATE.hass = fake_hass
    pb._STATE.running = True

    fake_hass.states.set("person.sam", "home")
    pb._on_state_changed(_event(
        "binary_sensor.front_door", types.SimpleNamespace(
            state="on", attributes={"device_class": "door"}),
    ))
    assert pb._STATE.security_events == []
    fake_hass.states.set("person.sam", "not_home")

    irrelevant = types.SimpleNamespace(state="on", attributes={"device_class": "motion"})
    pb._on_state_changed(_event("binary_sensor.motion", irrelevant))
    pb._STATE.security_events = [now - 1900]
    for entity_id, state in (
        ("binary_sensor.front_door", "on"),
        ("binary_sensor.window", "on"),
        ("lock.front_door", "unlocked"),
    ):
        domain = "lock" if entity_id.startswith("lock.") else "binary_sensor"
        attrs = {"device_class": "window" if "window" in entity_id else "door"}
        pb._on_state_changed(_event(
            entity_id, types.SimpleNamespace(state=state, attributes=attrs),
        ))

    await fake_hass.drain()
    assert calls == [(("security",), {})]
    assert pb._STATE.security_events == []


@pytest.mark.asyncio
async def test_security_trigger_respects_briefing_cooldown(pb, fake_hass, monkeypatch):
    now = 1_000_000
    monkeypatch.setattr(pb.time, "time", lambda: now)
    calls = []

    async def trigger(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(pb, "_trigger_briefing", trigger)
    pb._STATE.hass = fake_hass
    pb._STATE.running = True
    pb._STATE.last_briefing_time = now - 60
    for _ in range(3):
        pb._on_state_changed(_event(
            "lock.front_door", types.SimpleNamespace(state="unlocked", attributes={}),
        ))
    await fake_hass.drain()
    assert calls == []
    assert len(pb._STATE.security_events) == 3


def _install_trigger_dependencies(pb, monkeypatch, *, provider=None, sleeping=False):
    briefing = types.ModuleType("jc.briefing")
    briefing._gather_weather = lambda _hass: "clear"
    briefing._gather_open_things = lambda _hass: ["garage"]
    briefing._gather_overnight_events = lambda _hass, _hours: ["door opened"]
    briefing._gather_calendar = lambda _hass: []
    briefing._gather_energy_anomalies = lambda _hass: []
    briefing._greeting_instruction = lambda _hass, name: f"Greet {name}. "
    monkeypatch.setitem(sys.modules, "jc.briefing", briefing)

    tts = types.ModuleType("jc.tts_helper")
    tts.resolve_tts_for_context = lambda *_args: "tts.house"
    announcements = []

    async def announce(*args, **kwargs):
        announcements.append((args, kwargs))

    tts.async_announce = announce
    monkeypatch.setitem(sys.modules, "jc.tts_helper", tts)

    audio = types.ModuleType("jc.audio_routing")
    audio.observer_speak_target = lambda *_args, **_kwargs: (["media_player.kitchen"], "broadcast")
    monkeypatch.setitem(sys.modules, "jc.audio_routing", audio)

    sleep = types.ModuleType("jc.sleep_detection")
    sleep.is_sleeping = lambda *_args, **_kwargs: (sleeping, None)
    monkeypatch.setitem(sys.modules, "jc.sleep_detection", sleep)
    monkeypatch.setattr(sys.modules["jc"], "sleep_detection", sleep, raising=False)

    provider_module = types.ModuleType("jc.llm_provider")
    provider_module.create_tier_provider = lambda *_args: provider
    provider_module.create_provider = lambda *_args: provider
    monkeypatch.setitem(sys.modules, "jc.llm_provider", provider_module)

    secrets = types.ModuleType("jc.ha_secrets")

    async def get_key(*_args):
        return "key"

    secrets.async_get_provider_key = get_key
    monkeypatch.setitem(sys.modules, "jc.ha_secrets", secrets)
    const = types.ModuleType("jc.const")
    const.resolve_provider_base_url = lambda *_args: "https://example.test"
    monkeypatch.setitem(sys.modules, "jc.const", const)

    directive = sys.modules["jc.directive_helper"]
    monkeypatch.setattr(directive, "build_system_prompt", lambda *_args: "system prompt")
    return announcements


class _Provider:
    def __init__(self, text="Briefing text", error=None):
        self.text = text
        self.error = error
        self.messages = None

    def chat(self, messages, *_args):
        self.messages = messages
        if self.error:
            raise self.error
        return {"text": self.text}


@pytest.mark.asyncio
async def test_trigger_skips_during_cooldown(pb, fake_hass, monkeypatch):
    pb._STATE.hass = fake_hass
    pb._STATE.last_briefing_time = 1000
    monkeypatch.setattr(pb.time, "time", lambda: 1001)
    await pb._trigger_briefing("scheduled")
    assert pb._STATE.last_briefing_time == 1000


@pytest.mark.asyncio
async def test_trigger_away_builds_context_and_pushes(pb, fake_hass, monkeypatch):
    provider = _Provider()
    announcements = _install_trigger_dependencies(pb, monkeypatch, provider=provider)
    pushes = []

    async def push(*args):
        pushes.append(args)

    monkeypatch.setattr(pb, "_push_to_phone", push)
    pb.record_snapshot("Front", "camera.front", "Person at gate", "person")
    pb._STATE.hass = fake_hass
    pb._STATE.config = {"honorific": "captain"}
    await pb._trigger_briefing("scheduled")

    context = provider.messages[1]["content"]
    assert "Weather: clear." in context
    assert "Open/unlocked: garage." in context
    assert "Recent events: door opened." in context
    assert "This is a scheduled briefing." in context
    assert "Person at gate" in context
    assert pushes == [(fake_hass, pb._STATE.config, "Briefing text", "scheduled")]
    assert announcements == []


@pytest.mark.asyncio
async def test_trigger_home_awake_announces_and_pushes(pb, fake_hass, monkeypatch):
    provider = _Provider("Hello")
    announcements = _install_trigger_dependencies(pb, monkeypatch, provider=provider)
    pushes = []

    async def push(*args):
        pushes.append(args)

    monkeypatch.setattr(pb, "_push_to_phone", push)
    fake_hass.states.set("person.sam", "home")
    pb._STATE.hass = fake_hass
    pb._STATE.config = {"tts_engine": "piper", "broadcast_group": "downstairs"}
    await pb._trigger_briefing("arrival", person_name="Sam")

    assert len(announcements) == 1
    args, kwargs = announcements[0]
    assert args[1:4] == ("Hello", "tts.house", ["media_player.kitchen"])
    assert kwargs == {"context": "briefing"}
    assert len(pushes) == 1


@pytest.mark.asyncio
async def test_trigger_sleeping_only_pushes_and_empty_output_stops(pb, fake_hass, monkeypatch):
    provider = _Provider("Briefing")
    announcements = _install_trigger_dependencies(
        pb, monkeypatch, provider=provider, sleeping=True,
    )
    pushes = []

    async def push(*args):
        pushes.append(args)

    monkeypatch.setattr(pb, "_push_to_phone", push)
    fake_hass.states.set("person.sam", "home")
    pb._STATE.hass = fake_hass
    await pb._trigger_briefing("security")
    assert announcements == []
    assert len(pushes) == 1

    pb._STATE.last_briefing_time = 0
    provider.text = "  "
    await pb._trigger_briefing("security")
    assert len(pushes) == 1


@pytest.mark.asyncio
async def test_trigger_provider_fallback_and_failure_are_handled(pb, fake_hass, monkeypatch):
    provider = _Provider("Fallback briefing")
    announcements = _install_trigger_dependencies(pb, monkeypatch, provider=provider)
    provider_module = sys.modules["jc.llm_provider"]

    def tier_failure(*_args):
        raise RuntimeError("tier unavailable")

    provider_module.create_tier_provider = tier_failure
    pushes = []

    async def push(*args):
        pushes.append(args)

    monkeypatch.setattr(pb, "_push_to_phone", push)
    pb._STATE.hass = fake_hass
    pb._STATE.config = {"llm_provider": "local", "model": "model-x"}
    await pb._trigger_briefing("camera")
    assert len(pushes) == 1
    assert announcements == []

    pb._STATE.last_briefing_time = 0
    provider_module.create_provider = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
    await pb._trigger_briefing("camera")
    assert len(pushes) == 1


@pytest.mark.asyncio
async def test_push_to_phone_titles_payload_and_failure_paths(pb, fake_hass, monkeypatch):
    driving = types.ModuleType("jc.driving_mode")
    driving.CAT_BRIEFING = "briefing"
    driving.CAT_SECURITY = "security"
    driving.augment_data_for_category = lambda _hass, _config, category: {"category": category}
    monkeypatch.setitem(sys.modules, "jc.driving_mode", driving)

    await pb._push_to_phone(fake_hass, {}, "No service", "arrival")
    await pb._push_to_phone(fake_hass, {"notify_service": "mobile_app.phone"}, "Alert", "security")
    assert fake_hass.service_calls == [(
        "mobile_app", "phone", {
            "message": "Alert",
            "title": "JARVIS — Security Alert",
            "data": {"category": "security"},
        },
    )]

    driving.augment_data_for_category = lambda *_args: (_ for _ in ()).throw(RuntimeError("no driving data"))
    await pb._push_to_phone(fake_hass, {"notify_service": "notify.user"}, "Hello", "unknown")
    assert fake_hass.service_calls[-1] == (
        "notify", "user", {"message": "Hello", "title": "JARVIS — Briefing"},
    )

    await pb._push_to_phone(fake_hass, {"notify_service": "invalid"}, "Hello", "arrival")
    original_call = fake_hass.services.async_call

    async def service_failure(*_args, **_kwargs):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(fake_hass.services, "async_call", service_failure)
    await pb._push_to_phone(fake_hass, {"notify_service": "notify.user"}, "Hello", "arrival")
    monkeypatch.setattr(fake_hass.services, "async_call", original_call)