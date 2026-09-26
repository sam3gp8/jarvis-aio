"""Behavioral tests for the Observer's filtering and announcement pipeline."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def observer(load, monkeypatch):
    module = load("observer")
    module._STATE.reset()
    module._STATE.hass = None
    module._STATE.config = {}
    module._STATE.classifier_provider = None
    module._STATE.reasoning_provider = None
    module._STATE.review_provider = None
    module._GROUP_LAST.clear()
    monkeypatch.setattr(module, "_STATE", module._STATE)
    yield module
    module._STATE.reset()


def _event(entity_id="binary_sensor.front_door", old="off", new="on", **attrs):
    return types.SimpleNamespace(data={
        "entity_id": entity_id,
        "old_state": types.SimpleNamespace(state=old, attributes={}),
        "new_state": types.SimpleNamespace(state=new, attributes=attrs),
    })


def _changed_event(entity_id="binary_sensor.front_door", old="off", new="on", **attrs):
    now = datetime.now(timezone.utc)
    return types.SimpleNamespace(data={
        "entity_id": entity_id,
        "old_state": types.SimpleNamespace(
            state=old, attributes={}, last_changed=now - timedelta(seconds=20)),
        "new_state": types.SimpleNamespace(
            state=new, attributes=attrs, last_changed=now),
    })


def test_entity_noise_filter_uses_suffix_and_substrings(observer):
    assert observer._entity_id_looks_noisy("sensor.panel_500W") is False
    assert observer._entity_id_looks_noisy("sensor.panel_w") is True
    assert observer._entity_id_looks_noisy("sensor.battery_voltage") is True
    assert observer._entity_id_looks_noisy("sensor.basement_window") is False
    assert observer._entity_id_looks_noisy("sensor.meter_total_kwh") is True


def test_review_tier_accepts_local_and_configured_custom_providers(observer, monkeypatch):
    from jc import const

    monkeypatch.setattr(
        const, "resolve_provider_base_url",
        lambda config, provider: config.get("custom_base_url") if provider == "custom" else None,
    )
    assert observer._review_tier_is_configured({"review_provider": "ollama"})
    assert observer._review_tier_is_configured({
        "review_provider": "custom", "custom_base_url": "http://llm.local",
    })


@pytest.mark.parametrize("entity_id", ["", "light.kitchen", "sensor.temperature", "binary_sensor.front_door"])
def test_prefilter_drops_missing_ignored_numeric_and_invalid_events(observer, entity_id):
    if entity_id == "binary_sensor.front_door":
        event = _event(entity_id, old="on", new="on")
    else:
        event = _event(entity_id)
    assert observer._should_pre_filter(event)


@pytest.mark.parametrize("device_class", ["moisture", "smoke", "gas", "carbon_monoxide", "problem", "safety", "tamper", "running"])
def test_prefilter_admits_interesting_sensor_classes(observer, device_class):
    assert not observer._should_pre_filter(_event(
        "sensor.utility", device_class=device_class,
    ))


def test_prefilter_handles_unavailable_startup_flapping_and_exclusions(observer, fake_hass, monkeypatch):
    observer._STATE.hass = fake_hass
    assert observer._should_pre_filter(_event(new="unavailable"))
    assert observer._should_pre_filter(_event(old="unknown"))

    now = datetime.now(timezone.utc)
    flapping = types.SimpleNamespace(data={
        "entity_id": "binary_sensor.door",
        "old_state": types.SimpleNamespace(state="off", last_changed=now - timedelta(seconds=2)),
        "new_state": types.SimpleNamespace(state="on", attributes={}, last_changed=now),
    })
    assert observer._should_pre_filter(flapping)

    entity_filter = types.ModuleType("jc.entity_filter")
    entity_filter.is_excluded = lambda *_args: True
    monkeypatch.setitem(sys.modules, "jc.entity_filter", entity_filter)
    monkeypatch.setattr(sys.modules["jc"], "entity_filter", entity_filter, raising=False)
    assert observer._should_pre_filter(_changed_event())


def test_prefilter_fails_open_when_exclusion_lookup_errors(observer, fake_hass, monkeypatch):
    observer._STATE.hass = fake_hass
    entity_filter = types.ModuleType("jc.entity_filter")

    def broken(*_args):
        raise RuntimeError("registry unavailable")

    entity_filter.is_excluded = broken
    monkeypatch.setitem(sys.modules, "jc.entity_filter", entity_filter)
    monkeypatch.setattr(sys.modules["jc"], "entity_filter", entity_filter, raising=False)
    assert not observer._should_pre_filter(_changed_event())


def test_prefilter_handles_missing_states_and_unreadable_timestamps(observer):
    assert observer._should_pre_filter(types.SimpleNamespace(data={
        "entity_id": "sensor.smoke", "new_state": None,
    }))
    assert observer._should_pre_filter(types.SimpleNamespace(data={
        "entity_id": "binary_sensor.door", "new_state": _event().data["new_state"],
    }))
    assert observer._should_pre_filter(types.SimpleNamespace(data={
        "entity_id": "binary_sensor.door",
        "old_state": types.SimpleNamespace(state="off"),
        "new_state": types.SimpleNamespace(state="on", attributes={}),
    })) is False


def test_runtime_config_invalid_values_fall_back_to_entry_settings(observer, fake_hass):
    from jc.const import DOMAIN

    observer._STATE.hass = fake_hass
    observer._STATE.config = {
        "classifier_rate_limit": "4", "observer_group_debounce": "bad",
    }
    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {
        "classifier_rate_limit": "bad", "observer_group_debounce": "also-bad",
    }}}
    assert observer._effective_rate_limit() == 4
    assert observer._group_debounce_s() == observer.GROUP_DEBOUNCE_S
    observer._STATE.hass = None
    assert observer._effective_rate_limit() == 4


def test_group_debounce_runtime_and_entry_precedence(observer, fake_hass):
    from jc.const import DOMAIN

    observer._STATE.hass = fake_hass
    observer._STATE.config = {"observer_group_debounce": 25}
    assert observer._group_debounce_s() == 25
    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"observer_group_debounce": "12.5"}}}
    assert observer._group_debounce_s() == 12.5


def test_rate_limit_config_precedence_expiry_and_unlimited(observer, fake_hass, monkeypatch):
    from jc.const import DOMAIN

    observer._STATE.hass = fake_hass
    observer._STATE.config = {"classifier_rate_limit": "bad"}
    assert observer._effective_rate_limit() == observer.GLOBAL_CLASSIFIER_RATE_LIMIT_PER_HOUR
    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"classifier_rate_limit": "2"}}}
    assert observer._effective_rate_limit() == 2

    monkeypatch.setattr(observer.time, "time", lambda: 10_000)
    observer._STATE.classifier_timestamps.extend([6_399, 6_500])
    assert not observer._classifier_rate_limited()
    observer._STATE.classifier_timestamps.extend([9_000])
    assert observer._classifier_rate_limited()
    fake_hass.data[DOMAIN]["entry"]["runtime_config"]["classifier_rate_limit"] = 0
    assert not observer._classifier_rate_limited()


def test_debounce_and_recent_context_include_camera_records(observer, monkeypatch):
    now = 20_000
    monkeypatch.setattr(observer.time, "time", lambda: now)
    assert not observer._debounced("door", 60)
    assert observer._debounced("door", 60)
    observer._STATE.recent_events.clear()
    observer.record_camera_event("Front", "person at gate", "person", True)
    assert "Camera Front: person at gate" in observer.get_recent_context()
    assert "⚠" in observer.get_recent_context()
    now += 700
    assert observer.get_recent_context(300) == "quiet — no notable recent activity"


def test_recent_context_formats_state_age_and_limits_output(observer, monkeypatch):
    now = [30_000]
    monkeypatch.setattr(observer.time, "time", lambda: now[0])
    observer._STATE.recent_events.clear()
    observer._STATE.recent_events.append({
        "ts": now[0] - 125, "fname": "Front Door", "old": "closed",
        "new": "open", "area": "entry",
    })
    assert "2m ago" in observer.get_recent_context()
    assert "[entry] Front Door: closed → open" in observer.get_recent_context()
    for index in range(16):
        observer._STATE.recent_events.append({
            "ts": now[0], "kind": "camera", "camera": f"Camera {index}",
            "summary": "motion", "notable": False,
        })
    context = observer.get_recent_context()
    assert "[entry] Front Door: closed → open" not in context
    assert "Camera 0" not in context
    assert "Camera 1" in context


def test_record_camera_event_uses_defaults_and_boolean_notable(observer):
    observer.record_camera_event("Drive", "vehicle", notable=1)
    item = observer._STATE.recent_events[-1]
    assert item["category"] == "other"
    assert item["notable"] is True
    assert item["area"] is None


def test_record_for_context_skips_missing_and_keeps_area(observer, fake_hass, monkeypatch):
    observer._STATE.hass = fake_hass
    monkeypatch.setattr(observer.audio_routing, "entity_area", lambda *_args: "kitchen")
    observer._record_for_context(types.SimpleNamespace(data={"new_state": None}))
    observer._record_for_context(_event())
    context = observer._STATE.recent_events[-1]
    assert context["area"] == "kitchen"
    assert context["old"] == "off" and context["new"] == "on"


def test_cognition_config_runtime_precedence_and_invalid_values(observer, fake_hass):
    from jc.const import DOMAIN

    observer._STATE.hass = fake_hass
    observer._STATE.config = {"cognition_enabled": False, "cognition_threshold": "bad"}
    assert not observer._cognition_enabled()
    assert observer._cognition_threshold() == 0.6
    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {
        "cognition_enabled": True, "cognition_threshold": "0.9",
    }}}
    assert observer._cognition_enabled()
    assert observer._cognition_threshold() == 0.9


def test_announcement_and_speaker_config_helpers(observer, fake_hass):
    from jc.const import DOMAIN

    observer._STATE.hass = fake_hass
    assert observer._is_announcements_enabled() is True
    observer._STATE.config = {"announcements_enabled": False}
    assert observer._is_announcements_enabled() is False
    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {
        "announcements_enabled": True,
        "announcement_speakers": '["media_player.office"]',
    }}}
    assert observer._is_announcements_enabled() is True
    assert observer._get_announcement_speakers() == ["media_player.office"]
    fake_hass.data[DOMAIN]["entry"]["runtime_config"]["announcement_speakers"] = "invalid"
    assert observer._get_announcement_speakers() is None


@pytest.mark.asyncio
async def test_state_handler_respects_running_filter_and_schedules_valid_event(
    observer, fake_hass, monkeypatch,
):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"cognition_enabled": False}}}
    observer._STATE.hass = fake_hass
    observer._STATE.running = True
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: False)
    monkeypatch.setattr(observer, "_group_debounce_s", lambda: 0)
    monkeypatch.setattr(observer, "_group_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_classifier_rate_limited", lambda: False)
    event = _changed_event()
    observer._on_state_changed(event)
    assert len(fake_hass._tasks) == 1
    assert len(observer._STATE.classifier_timestamps) == 1
    fake_hass.close_pending()


@pytest.mark.asyncio
async def test_state_handler_drops_ignored_and_rate_limited_events(observer, fake_hass, monkeypatch):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"cognition_enabled": False}}}
    observer._STATE.hass = fake_hass
    observer._STATE.running = True
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: False)
    monkeypatch.setattr(observer, "_group_debounce_s", lambda: 0)
    monkeypatch.setattr(observer, "_group_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_classifier_rate_limited", lambda: True)
    core = types.ModuleType("jc.cognitive_core")
    core.is_ignored = lambda _entity: True
    monkeypatch.setitem(sys.modules, "jc.cognitive_core", core)
    monkeypatch.setattr(sys.modules["jc"], "cognitive_core", core, raising=False)
    observer._on_state_changed(_changed_event())
    assert fake_hass._tasks == []


@pytest.mark.asyncio
async def test_state_handler_cognition_escalates_filtered_event(observer, fake_hass, monkeypatch):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {
        "cognition_enabled": True, "cognition_threshold": 0.8,
    }}}
    observer._STATE.hass = fake_hass
    observer._STATE.running = True
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: True)
    monkeypatch.setattr(observer, "_group_debounce_s", lambda: 0)
    monkeypatch.setattr(observer, "_group_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_classifier_rate_limited", lambda: False)
    monkeypatch.setattr(observer, "_record_for_context", lambda _event: None)
    decisions = []
    cognition = types.ModuleType("jc.cognition")
    cognition.process = lambda event, threshold: decisions.append(threshold) or types.SimpleNamespace(
        escalate=True, reason="unexpected anomaly",
    )
    monkeypatch.setitem(sys.modules, "jc.cognition", cognition)
    monkeypatch.setattr(sys.modules["jc"], "cognition", cognition, raising=False)
    core = types.ModuleType("jc.cognitive_core")
    core.is_ignored = lambda _entity: False
    monkeypatch.setitem(sys.modules, "jc.cognitive_core", core)
    monkeypatch.setattr(sys.modules["jc"], "cognitive_core", core, raising=False)
    observer._on_state_changed(_changed_event("light.decorative"))
    assert decisions == [0.8]
    assert len(fake_hass._tasks) == 1
    fake_hass.close_pending()


@pytest.mark.asyncio
async def test_state_handler_ignores_cognition_failures_and_ignored_entities(
    observer, fake_hass, monkeypatch,
):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"cognition_enabled": True}}}
    observer._STATE.hass = fake_hass
    observer._STATE.running = True
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: True)
    cognition = types.ModuleType("jc.cognition")
    cognition.process = lambda *_args: (_ for _ in ()).throw(RuntimeError("cognition unavailable"))
    monkeypatch.setitem(sys.modules, "jc.cognition", cognition)
    monkeypatch.setattr(sys.modules["jc"], "cognition", cognition, raising=False)
    observer._on_state_changed(_changed_event("light.decorative"))
    assert fake_hass._tasks == []


@pytest.mark.asyncio
async def test_state_handler_warns_once_at_rate_limit_then_clears(observer, fake_hass, monkeypatch, caplog):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"cognition_enabled": False}}}
    observer._STATE.hass = fake_hass
    observer._STATE.running = True
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: False)
    monkeypatch.setattr(observer, "_group_debounce_s", lambda: 0)
    monkeypatch.setattr(observer, "_group_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_debounced", lambda *_args: False)
    limited = [True]
    monkeypatch.setattr(observer, "_classifier_rate_limited", lambda: limited[0])
    monkeypatch.setattr(observer, "_effective_rate_limit", lambda: 3)
    observer._on_state_changed(_changed_event())
    observer._on_state_changed(_changed_event("binary_sensor.back_door"))
    assert sum("hit rate limit" in record.message for record in caplog.records) == 1
    limited[0] = False
    observer._on_state_changed(_changed_event("binary_sensor.garage"))
    assert observer._STATE.rate_limit_warn_logged is False
    fake_hass.close_pending()


@pytest.mark.asyncio
async def test_state_handler_exits_for_static_filter_group_and_entity_debounce(
    observer, fake_hass, monkeypatch,
):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"cognition_enabled": False}}}
    observer._STATE.hass = fake_hass
    observer._STATE.running = True
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: True)
    observer._on_state_changed(_changed_event())
    monkeypatch.setattr(observer, "_should_pre_filter", lambda _event: False)
    monkeypatch.setattr(observer, "_group_debounce_s", lambda: 90)
    monkeypatch.setattr(observer, "_group_debounced", lambda *_args: True)
    observer._on_state_changed(_changed_event("binary_sensor.alarm_zone_1"))
    monkeypatch.setattr(observer, "_group_debounced", lambda *_args: False)
    monkeypatch.setattr(observer, "_debounced", lambda _entity, interval: interval == observer.DEBOUNCE_MOTION_S)
    observer._on_state_changed(_changed_event(device_class="motion"))
    assert fake_hass._tasks == []


def _pipeline(observer, monkeypatch, *, worth=True, speak=True, sleeping=False,
              mode="broadcast", targets=None, announcements=True, urgency="medium",
              reserve=True, review_provider=None):
    recorded = []
    notifications = []
    activity = []
    monkeypatch.setattr(observer.classifier, "classify", _async_result({
        "worth_considering": worth, "urgency": "high", "category": "security",
    }))
    monkeypatch.setattr(observer.reasoning_loop, "decide", _async_result({
        "speak": speak, "reason": "test", "message": "Door opened", "urgency": urgency,
    }))
    monkeypatch.setattr(observer.reasoning_loop, "review_decision", _async_result(True))
    monkeypatch.setattr(observer.output_gate, "recent_announcements", lambda *_args: [])
    monkeypatch.setattr(observer.output_gate, "async_reserve_announcement", _async_result(
        (reserve, "blocked", "reservation")))
    monkeypatch.setattr(observer.output_gate, "async_record_announcement", _async_append(recorded))
    monkeypatch.setattr(observer.output_gate, "release_reservation", _async_append([]))
    monkeypatch.setattr(observer.sleep_detection, "is_sleeping", lambda *_a, **_k: (sleeping, "bedroom"))
    monkeypatch.setattr(observer.sleep_detection, "_in_quiet_hours", lambda *_args: False)
    monkeypatch.setattr(observer.audio_routing, "currently_occupied_areas", lambda *_args: ["kitchen"])
    monkeypatch.setattr(observer.audio_routing, "entity_area", lambda *_args: "hall")
    monkeypatch.setattr(observer.audio_routing, "observer_speak_target", lambda *_a, **_k: (targets if targets is not None else ["media_player.kitchen"], mode))
    monkeypatch.setattr(observer, "_get_announcement_speakers", lambda: None)
    monkeypatch.setattr(observer, "_is_announcements_enabled", lambda: announcements)
    monkeypatch.setattr(observer, "_speak", _async_append([]))
    monkeypatch.setattr(observer, "_send_notification", _async_notification(notifications))
    monkeypatch.setattr(observer, "get_recent_context", lambda *_args: "recent")

    db = types.ModuleType("jc.database")
    db.save_activity = lambda **kwargs: activity.append(kwargs)
    monkeypatch.setitem(sys.modules, "jc.database", db)
    monkeypatch.setattr(sys.modules["jc"], "database", db, raising=False)
    observer._STATE.review_provider = review_provider
    return recorded, notifications, activity


def _async_result(value):
    async def result(*_args, **_kwargs):
        return value
    return result


def _async_append(sink):
    async def append(*args, **kwargs):
        sink.append((args, kwargs))
    return append


def _async_notification(sink):
    async def notify(message, *, urgency):
        sink.append((message, urgency))
    return notify


@pytest.mark.asyncio
async def test_process_event_logs_unworthy_and_returns(observer, fake_hass, monkeypatch):
    _, _, activity = _pipeline(observer, monkeypatch, worth=False)
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert activity[0]["category"] == "classified"
    assert "not worth considering" in activity[0]["message"]


@pytest.mark.asyncio
async def test_process_event_ignores_incomplete_event_and_activity_store_errors(
    observer, fake_hass, monkeypatch,
):
    observer._STATE.hass = fake_hass
    await observer._process_event(types.SimpleNamespace(data={"new_state": None}))
    _pipeline(observer, monkeypatch)
    db = sys.modules["jc.database"]
    db.save_activity = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable"))
    await observer._process_event(_event())
    assert observer._STATE.review_count == 1


@pytest.mark.asyncio
async def test_process_event_silent_decision_does_not_reserve(observer, fake_hass, monkeypatch):
    _, _, activity = _pipeline(observer, monkeypatch, speak=False)
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert activity[0]["category"] == "security"


@pytest.mark.asyncio
async def test_process_event_sleep_suppresses_noncritical(observer, fake_hass, monkeypatch):
    recorded, notifications, _ = _pipeline(observer, monkeypatch, sleeping=True)
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert recorded[0][1]["was_spoken"] is False
    assert notifications == []


@pytest.mark.asyncio
async def test_process_event_gate_denial_records_without_routing(observer, fake_hass, monkeypatch):
    recorded, _, _ = _pipeline(observer, monkeypatch, reserve=False)
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert recorded[0][1]["was_spoken"] is False
    assert "reservation_id" not in recorded[0][1]


@pytest.mark.asyncio
async def test_process_event_notify_only_and_suppressed_routes(observer, fake_hass, monkeypatch):
    recorded, notifications, _ = _pipeline(observer, monkeypatch, mode="notify_only", urgency="high")
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert notifications == [("Door opened", "high")]
    assert recorded[0][1]["was_spoken"] is False

    recorded, notifications, _ = _pipeline(observer, monkeypatch, mode="suppressed")
    await observer._process_event(_event())
    assert len(recorded) == 1
    assert notifications == []


@pytest.mark.asyncio
async def test_process_event_speaks_and_notifies_high(observer, fake_hass, monkeypatch):
    recorded, notifications, _ = _pipeline(observer, monkeypatch, urgency="high")
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert recorded[0][1]["was_spoken"] is True
    assert notifications == [("Door opened", "high")]


@pytest.mark.asyncio
async def test_process_event_releases_reservation_when_targets_missing(observer, fake_hass, monkeypatch):
    released = []
    _pipeline(observer, monkeypatch, targets=[])
    monkeypatch.setattr(observer.output_gate, "release_reservation", _async_append(released))
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert released


@pytest.mark.asyncio
async def test_process_event_review_veto_and_critical_downgrade(observer, fake_hass, monkeypatch):
    _, _, _ = _pipeline(observer, monkeypatch, speak=True, review_provider=object())
    observer._STATE.review_count = 9
    review = observer.reasoning_loop
    monkeypatch.setattr(review, "review_decision", _async_result(False))
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert observer._STATE.review_count == 10

    recorded, _, _ = _pipeline(observer, monkeypatch, urgency="critical")
    observer._STATE.review_provider = None
    monkeypatch.setattr(observer.audio_routing, "observer_speak_target", lambda *_a, **_k: ([], "suppressed"))
    await observer._process_event(_event())
    assert recorded[0][1]["urgency"] == "high"


@pytest.mark.asyncio
async def test_process_event_uses_home_presence_and_quiet_hours_routing(observer, fake_hass, monkeypatch):
    recorded, notifications, _ = _pipeline(observer, monkeypatch, urgency="high")
    fake_hass.states.set("device_tracker.phone", "home")
    observer._STATE.hass = fake_hass
    monkeypatch.setattr(observer.sleep_detection, "_in_quiet_hours", lambda *_args: True)
    routed = []
    monkeypatch.setattr(observer.audio_routing, "observer_speak_target", lambda *_a, **kwargs: (
        routed.append(kwargs) or ([], "notify_only")))
    await observer._process_event(_event())
    assert routed[0]["is_sleeping"] is True
    assert notifications == [("Door opened", "high")]
    assert recorded[0][1]["was_spoken"] is False


@pytest.mark.asyncio
async def test_process_event_allows_classifier_critical_through_sleep(observer, fake_hass, monkeypatch):
    recorded, _, _ = _pipeline(observer, monkeypatch, sleeping=True)
    monkeypatch.setattr(observer.classifier, "classify", _async_result({
        "worth_considering": True, "urgency": "critical", "category": "safety",
    }))
    monkeypatch.setattr(observer.reasoning_loop, "decide", _async_result({
        "speak": True, "message": "Smoke detected", "urgency": "critical",
    }))
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert recorded[0][1]["urgency"] == "critical"


@pytest.mark.asyncio
async def test_process_event_disabled_announcements_records_and_notifies(observer, fake_hass, monkeypatch):
    recorded, notifications, _ = _pipeline(observer, monkeypatch, announcements=False, urgency="critical")
    observer._STATE.hass = fake_hass
    await observer._process_event(_event())
    assert recorded[0][1]["was_spoken"] is False
    assert notifications == [("Door opened", "high")]


@pytest.mark.asyncio
async def test_speak_filters_targets_resolves_tts_and_handles_errors(observer, fake_hass, monkeypatch):
    observer._STATE.hass = fake_hass
    observer._STATE.config = {"tts_engine": "custom"}
    routing = types.ModuleType("jc.audio_routing")
    routing.drop_display_targets = lambda _hass, targets, _source: targets[1:]
    monkeypatch.setitem(sys.modules, "jc.audio_routing", routing)
    monkeypatch.setattr(sys.modules["jc"], "audio_routing", routing, raising=False)
    tts = types.ModuleType("jc.tts_helper")
    tts.resolve_tts_for_context = lambda *_args: "tts.custom"
    monkeypatch.setitem(sys.modules, "jc.tts_helper", tts)
    monkeypatch.setattr(sys.modules["jc"], "tts_helper", tts, raising=False)
    await observer._speak("hello", targets=["media_player.tv", "media_player.kitchen"])
    assert fake_hass.service_calls[0][2]["entity_id"] == "tts.custom"
    assert fake_hass.service_calls[0][2]["media_player_entity_id"] == "media_player.kitchen"

    observer._STATE.config = {}
    tts.resolve_tts_for_context = lambda *_args: (_ for _ in ()).throw(RuntimeError("tts unavailable"))
    await observer._speak("quiet", targets=[])
    assert len(fake_hass.service_calls) == 1


@pytest.mark.asyncio
async def test_send_notification_runtime_config_payload_and_invalid_service(observer, fake_hass, monkeypatch):
    from jc.const import DOMAIN

    observer._STATE.hass = fake_hass
    observer._STATE.config = {"notify_service": "notify.fallback"}
    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {"notify_service": "mobile_app.phone"}}}
    driving = types.ModuleType("jc.driving_mode")
    driving.CAT_SECURITY = "security"
    driving.augment_data_for_category = lambda *_args: {"tag": "urgent"}
    monkeypatch.setitem(sys.modules, "jc.driving_mode", driving)
    monkeypatch.setattr(sys.modules["jc"], "driving_mode", driving, raising=False)
    await observer._send_notification("help", urgency="critical")
    assert fake_hass.service_calls[-1][0:2] == ("mobile_app", "phone")
    assert fake_hass.service_calls[-1][2] == {
        "title": "⚠ JARVIS URGENT", "message": "help", "data": {"tag": "urgent"},
    }
    fake_hass.data[DOMAIN]["entry"]["runtime_config"].clear()
    await observer._send_notification("hello", urgency="low")
    assert fake_hass.service_calls[-1][0:2] == ("notify", "fallback")

    fake_hass.data[DOMAIN]["entry"]["runtime_config"].clear()
    observer._STATE.config = {}
    before = len(fake_hass.service_calls)
    await observer._send_notification("no destination", urgency="low")
    assert len(fake_hass.service_calls) == before


@pytest.mark.asyncio
async def test_send_notification_invalid_service_and_service_error_are_contained(
    observer, fake_hass, monkeypatch, caplog,
):
    observer._STATE.hass = fake_hass
    observer._STATE.config = {"notify_service": "not-a-service"}
    await observer._send_notification("hello", urgency="low")
    assert "notification failed" in caplog.text

    observer._STATE.config["notify_service"] = "notify.phone"

    async def fail(*_args, **_kwargs):
        raise RuntimeError("notify failed")

    monkeypatch.setattr(fake_hass.services, "async_call", fail)
    await observer._send_notification("hello", urgency="critical")
    assert "notification failed" in caplog.text


@pytest.mark.asyncio
async def test_start_merges_runtime_models_starts_optional_modules_and_stop_resets(
    observer, fake_hass, monkeypatch,
):
    from jc.const import DOMAIN

    fake_hass.data[DOMAIN] = {"entry": {"runtime_config": {
        "llm_base_url": "http://gpu:11434", "classifier_model": "live-model",
        "unrelated_setting": "ignored",
    }}}
    refreshed = []
    monkeypatch.setattr(observer, "refresh_tier_providers", _async_append(refreshed))

    started = []
    stopped = []
    for name in ("appliance_monitor", "proactive_briefing", "cognitive_core"):
        sibling = types.ModuleType(f"jc.{name}")
        sibling.start = lambda _hass, config, sibling_name=name: started.append((sibling_name, dict(config)))
        sibling.stop = lambda sibling_name=name: stopped.append(sibling_name)
        monkeypatch.setitem(sys.modules, f"jc.{name}", sibling)
        monkeypatch.setattr(sys.modules["jc"], name, sibling, raising=False)

    await observer.start(fake_hass, {"classifier_model": "old-model", "base": True})
    assert observer._STATE.running
    assert observer._STATE.config["classifier_model"] == "live-model"
    assert observer._STATE.config["llm_base_url"] == "http://gpu:11434"
    assert "unrelated_setting" not in observer._STATE.config
    assert [name for name, _ in started] == [
        "appliance_monitor", "proactive_briefing", "cognitive_core",
    ]
    assert refreshed[0][0][0] is fake_hass
    await observer.stop()
    assert len(stopped) == 3
    assert observer._STATE.running is False


@pytest.mark.asyncio
async def test_start_aborts_when_provider_refresh_fails(observer, fake_hass, monkeypatch):
    async def fail(*_args):
        raise RuntimeError("provider configuration invalid")

    monkeypatch.setattr(observer, "refresh_tier_providers", fail)
    await observer.start(fake_hass, {})
    assert observer._STATE.running is False


@pytest.mark.asyncio
async def test_speak_uses_fallback_and_survives_service_and_filter_errors(observer, fake_hass, monkeypatch):
    observer._STATE.hass = fake_hass
    routing = types.ModuleType("jc.audio_routing")
    routing.drop_display_targets = lambda *_args: (_ for _ in ()).throw(RuntimeError("filter failed"))
    monkeypatch.setitem(sys.modules, "jc.audio_routing", routing)
    monkeypatch.setattr(sys.modules["jc"], "audio_routing", routing, raising=False)
    tts = types.ModuleType("jc.tts_helper")
    tts.resolve_tts_for_context = lambda *_args: None
    monkeypatch.setitem(sys.modules, "jc.tts_helper", tts)
    monkeypatch.setattr(sys.modules["jc"], "tts_helper", tts, raising=False)

    async def failed_call(*_args, **_kwargs):
        raise RuntimeError("service failed")

    monkeypatch.setattr(fake_hass.services, "async_call", failed_call)
    await observer._speak("hello", targets=["media_player.kitchen"])