"""Camera vision recognition hint — per-camera match + home-wide resident fallback.

Per-camera face recognition is incomplete by nature: a subject whose back is to
the kitchen camera still shows a face to the dining-room camera. Without a
home-wide fallback, a recognised resident whose face a given camera can't see
gets narrated as an "unidentified person" and, at the 'important' alert level,
announced as one — even though Frigate recognised them elsewhere seconds ago.
"""
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _unpin_jc_siblings():
    """Exercising _recognition_hint / _reason_about_scene makes the production code
    run `from .presence import …` and `from .entity_filter import …`, which pins
    those submodules as attributes on the synthetic `jc` package and would defeat a
    later test's `monkeypatch.setitem(sys.modules, "jc.<name>", stub)` (see the note
    above `_JC_PER_TEST_RESET` in conftest). Unload them after each test so a
    subsequent `from . import <name>` falls back to that test's stub cleanly."""
    yield
    pkg = sys.modules.get("jc")
    for name in ("presence", "entity_filter"):
        if pkg is not None:
            try:
                delattr(pkg, name)
            except AttributeError:
                pass
        sys.modules.pop(f"jc.{name}", None)


@pytest.fixture
def cam(load, monkeypatch):
    # Stub the heavy third-party/sibling imports camera.py pulls at module load.
    monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    hc = types.ModuleType("homeassistant.components.camera")
    hc.async_get_image = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "homeassistant.components.camera", hc)
    net = types.ModuleType("homeassistant.helpers.network")
    net.get_url = lambda *a, **k: "http://localhost:8123"
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.network", net)
    for sib, fns in {
        "jc.database": {"save_message": lambda *a, **k: None},
        "jc.tts_helper": {"async_announce": lambda *a, **k: None},
        "jc.camera_backends": {"find_backend": lambda *a, **k: None},
    }.items():
        m = types.ModuleType(sib)
        for n, f in fns.items():
            setattr(m, n, f)
        monkeypatch.setitem(sys.modules, sib, m)
    sys.modules.pop("jc.camera", None)
    mod = load("camera")
    monkeypatch.setattr(mod, "_RECOGNITION_AVAILABLE", True, raising=False)
    yield mod
    sys.modules.pop("jc.camera", None)


def _set(cam, monkeypatch, *, last, resident):
    monkeypatch.setattr(cam, "last_seen_at", lambda hass, eid: last, raising=False)
    monkeypatch.setattr(cam, "resident_present", lambda hass: resident, raising=False)


def test_per_camera_match_names_the_person(cam, monkeypatch):
    _set(cam, monkeypatch,
         last={"is_confident": True, "name": "Sam", "confidence": 94, "age_seconds": 5},
         resident="Sam")
    hint = cam._recognition_hint(None, "camera.dining_room")
    assert "identified the person on camera as 'Sam'" in hint
    assert "94%" in hint


def test_home_wide_fallback_when_this_camera_has_no_match(cam, monkeypatch):
    # No fresh match on THIS camera, but Frigate recognised a resident elsewhere.
    _set(cam, monkeypatch, last=None, resident="Sam")
    hint = cam._recognition_hint(None, "camera.kitchen")
    assert "household resident 'Sam'" in hint
    assert "another camera" in hint
    assert "unidentified" in hint        # explicit instruction not to call them that


def test_stale_per_camera_match_falls_back_to_home_wide(cam, monkeypatch):
    # A match on this camera but too old (age >= 60) → not named directly; the
    # home-wide resident signal carries it instead.
    _set(cam, monkeypatch,
         last={"is_confident": True, "name": "Sam", "confidence": 94, "age_seconds": 300},
         resident="Sam")
    assert "household resident 'Sam'" in cam._recognition_hint(None, "camera.kitchen")


def test_low_confidence_per_camera_falls_back_to_home_wide(cam, monkeypatch):
    # below the 60% per-camera threshold → not named directly; home-wide used.
    _set(cam, monkeypatch,
         last={"is_confident": True, "name": "Sam", "confidence": 52, "age_seconds": 5},
         resident="Sam")
    assert "household resident 'Sam'" in cam._recognition_hint(None, "camera.kitchen")


def test_no_recognition_anywhere_gives_empty_hint(cam, monkeypatch):
    _set(cam, monkeypatch, last=None, resident=None)
    # No face match, no presence placement/home signal → genuinely nothing to add.
    monkeypatch.setattr(cam, "_camera_is_indoor", lambda h, e: None, raising=False)
    monkeypatch.setattr(cam, "_armed_away", lambda h: False, raising=False)
    monkeypatch.setattr(cam, "_recent_person_change", lambda h, s=120.0: None, raising=False)
    _inject_presence(cam, monkeypatch, home_count=0, home_names=())
    assert cam._recognition_hint(None, "camera.kitchen") == ""


# ── §1A/§2A: Tier 1 fires off the LIVE cache, no mocked is_confident ──────────
def test_tier1_fires_without_is_confident_key(cam, monkeypatch):
    # Regression: last_seen_at() historically never set "is_confident", so Tier 1
    # never fired in production. The hint must now default the gate from
    # confidence, so a real last_seen_at() dict (no is_confident key) still names.
    _set(cam, monkeypatch,
         last={"name": "Sam", "confidence": 88, "age_seconds": 4},  # no is_confident
         resident="Sam")
    hint = cam._recognition_hint(None, "camera.dining_room")
    assert "identified the person on camera as 'Sam'" in hint
    assert "88%" in hint


def test_tier1_honours_explicit_is_confident_false(cam, monkeypatch):
    # An explicit is_confident=False (a sub-threshold reading) is respected even if
    # a stray confidence number is high → fall through to the home-wide signal.
    _set(cam, monkeypatch,
         last={"is_confident": False, "name": "Sam", "confidence": 90, "age_seconds": 4},
         resident="Sam")
    hint = cam._recognition_hint(None, "camera.dining_room")
    assert "identified the person on camera as 'Sam'" not in hint
    assert "household resident 'Sam'" in hint   # Tier 1b carries it


def test_tier1_rejects_unknown_variants(cam, monkeypatch):
    # A confident reading whose NAME is an unknown variant must never be named —
    # the bare != "unknown" check would have leaked "unknown person" once Tier 1
    # actually fires off the live cache.
    for variant in ("unknown", "unknown person", "unknown_face", "unrecognized"):
        _set(cam, monkeypatch,
             last={"is_confident": True, "name": variant, "confidence": 85,
                   "age_seconds": 3},
             resident="Sam")
        hint = cam._recognition_hint(None, "camera.dining_room")
        assert "as '" + variant + "'" not in hint
        assert "household resident 'Sam'" in hint   # fell through to Tier 1b


# ── §2A: Tier 3 presence guard (no face match anywhere) ──────────────────────
def _inject_presence(cam, monkeypatch, *, home_count, home_names):
    pres = types.ModuleType("jc.presence")
    people = [{"name": n, "state": "home"} for n in home_names]
    pres.get_presence_summary = lambda hass: {"home_count": home_count, "people": people}
    pres.presence_context_string = lambda hass: ", ".join(home_names)
    monkeypatch.setitem(sys.modules, "jc.presence", pres)


def _tier3(cam, monkeypatch, *, indoor, armed_away=False, home_count=0,
           home_names=(), moved=None):
    # No Tier-1/Tier-1b face match → the presence guard decides.
    _set(cam, monkeypatch, last=None, resident=None)
    monkeypatch.setattr(cam, "_camera_is_indoor", lambda h, e: indoor, raising=False)
    monkeypatch.setattr(cam, "_armed_away", lambda h: armed_away, raising=False)
    monkeypatch.setattr(cam, "_recent_person_change", lambda h, s=120.0: moved, raising=False)
    _inject_presence(cam, monkeypatch, home_count=home_count, home_names=home_names)


def test_tier3_indoor_residents_home_softens_stranger(cam, monkeypatch):
    _tier3(cam, monkeypatch, indoor=True, home_count=1, home_names=("Sam",))
    hint = cam._recognition_hint(None, "camera.living_room")
    assert "indoor camera" in hint
    assert "Sam are currently home" in hint
    assert "Do NOT describe a person here as an unidentified stranger" in hint


def test_tier3_indoor_but_armed_away_keeps_vigilance(cam, monkeypatch):
    # Declared-empty home: a person indoors is exactly what must stay flaggable.
    _tier3(cam, monkeypatch, indoor=True, armed_away=True, home_count=1,
           home_names=("Sam",))
    assert cam._recognition_hint(None, "camera.living_room") == ""


def test_tier3_indoor_but_nobody_home_is_silent(cam, monkeypatch):
    _tier3(cam, monkeypatch, indoor=True, home_count=0, home_names=())
    assert cam._recognition_hint(None, "camera.living_room") == ""


def test_tier3_outdoor_residents_home_keeps_vigilance(cam, monkeypatch):
    # Residents being home does NOT suppress a stranger on an outdoor camera.
    _tier3(cam, monkeypatch, indoor=False, home_count=2, home_names=("Sam", "Alex"))
    assert cam._recognition_hint(None, "camera.front_yard") == ""


def test_tier3_outdoor_recent_arrival_softens(cam, monkeypatch):
    # …except just after an arrival/departure, when the person is likely them.
    _tier3(cam, monkeypatch, indoor=False, home_count=1, home_names=("Sam",),
           moved="Sam")
    hint = cam._recognition_hint(None, "camera.front_door")
    assert "Sam" in hint and "arrived or left moments ago" in hint


def test_tier1_wins_over_tier3(cam, monkeypatch):
    # A live confident on-camera match is named directly; the presence guard and
    # its helpers are never consulted.
    def _boom(*a, **k):
        raise AssertionError("Tier 3 reached though Tier 1 matched")
    _set(cam, monkeypatch,
         last={"name": "Sam", "confidence": 92, "age_seconds": 3}, resident=None)
    monkeypatch.setattr(cam, "_armed_away", _boom, raising=False)
    monkeypatch.setattr(cam, "_camera_is_indoor", _boom, raising=False)
    assert "as 'Sam'" in cam._recognition_hint(None, "camera.kitchen")


# ── §2A helpers: _armed_away and _recent_person_change ───────────────────────
from datetime import datetime, timedelta, timezone


class _St:
    def __init__(self, entity_id, state, age_secs=10, friendly=None):
        self.entity_id = entity_id
        self.state = state
        self.last_changed = datetime.now(timezone.utc) - timedelta(seconds=age_secs)
        self.attributes = {"friendly_name": friendly} if friendly else {}


class _Hass:
    def __init__(self, by_domain):
        self._by = by_domain

    class _S:
        def __init__(self, by):
            self._by = by

        def async_all(self, domain=None):
            return self._by.get(domain, [])

    @property
    def states(self):
        return _Hass._S(self._by)


def test_armed_away_detects_away_postures(cam, monkeypatch):
    monkeypatch.setitem(sys.modules, "jc.entity_filter",
                        _fake_entity_filter())
    assert cam._armed_away(_Hass({"alarm_control_panel": [
        _St("alarm_control_panel.home", "armed_away")]})) is True
    assert cam._armed_away(_Hass({"alarm_control_panel": [
        _St("alarm_control_panel.home", "armed_vacation")]})) is True
    # A HOME arming posture is not "away" — residents are in.
    assert cam._armed_away(_Hass({"alarm_control_panel": [
        _St("alarm_control_panel.home", "armed_home")]})) is False
    assert cam._armed_away(_Hass({"alarm_control_panel": [
        _St("alarm_control_panel.home", "disarmed")]})) is False
    assert cam._armed_away(_Hass({})) is False


def _fake_entity_filter():
    ef = types.ModuleType("jc.entity_filter")
    ef.is_excluded = lambda hass, eid: False
    return ef


def test_recent_person_change_prefers_arrival(cam, monkeypatch):
    monkeypatch.setitem(sys.modules, "jc.entity_filter", _fake_entity_filter())
    hass = _Hass({"person": [
        _St("person.alex", "not_home", age_secs=30, friendly="Alex"),
        _St("person.sam", "home", age_secs=10, friendly="Sam"),
    ]})
    assert cam._recent_person_change(hass, 120.0) == "Sam"   # prefers the arrival


def test_recent_person_change_ignores_stale(cam, monkeypatch):
    monkeypatch.setitem(sys.modules, "jc.entity_filter", _fake_entity_filter())
    hass = _Hass({"person": [
        _St("person.sam", "home", age_secs=99999, friendly="Sam")]})
    assert cam._recent_person_change(hass, 120.0) is None


def test_recent_person_change_none_without_timestamp(cam, monkeypatch):
    # FakeState has no last_changed → the scan skips it without raising.
    monkeypatch.setitem(sys.modules, "jc.entity_filter", _fake_entity_filter())

    class _Bare:
        entity_id, state, attributes = "person.x", "home", {}

    hass = _Hass({"person": [_Bare()]})
    assert cam._recent_person_change(hass, 120.0) is None


# ── §2B: presence/recognition/placement reach the reasoning step ─────────────
async def test_reasoning_prompt_carries_presence_and_recognition(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return {"text": '{"severity":"routine","notable":false,"category":"known_resident","summary":"Sam in the kitchen.","speak":""}'}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "Kitchen", "A person stands at the counter.", "motion",
        property_only=False, indoor=True,
        presence_line="Sam is home.",
        recognition_line="Sam (resident) seen at kitchen (just now)")
    assert "Camera location: indoor" in captured["user"]
    assert "Household presence: Sam is home." in captured["user"]
    assert "Face recognition: Sam (resident) seen at kitchen (just now)" in captured["user"]
    # the clarifying rubric line reaches the model too
    assert "an unlabelled person is routine unless" in captured["system"]
    assert out["severity"] == "routine"


async def test_reasoning_prompt_marks_outdoor_and_omits_blank_context(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["user"] = messages[1]["content"]
        return {"text": '{"severity":"routine","notable":false,"category":"empty","summary":"x","speak":""}'}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    await cam._reason_about_scene(
        None, object(), "", "Front Yard", "Empty driveway.", "motion",
        property_only=False, indoor=False, presence_line="", recognition_line="   ")
    assert "Camera location: outdoor" in captured["user"]
    # empty / whitespace-only context lines are omitted entirely
    assert "Household presence:" not in captured["user"]
    assert "Face recognition:" not in captured["user"]


async def test_reasoning_prompt_omits_location_when_unknown(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["user"] = messages[1]["content"]
        return {"text": '{"severity":"routine","notable":false,"category":"empty","summary":"x","speak":""}'}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    await cam._reason_about_scene(
        None, object(), "", "Doorbell", "x", "motion",
        property_only=False, indoor=None)
    assert "Camera location:" not in captured["user"]


def test_rubric_has_indoor_resident_clause(cam):
    r = cam._SCENE_SEVERITY_RUBRIC
    assert "an unlabelled person is routine unless" in r
    assert "no alarm/breach is active" in r


# ── §2C: Frigate cropped snapshots (?crop=1) with uncropped fallback ─────────
_BIG = b"\xff\xd8\xff\xe0" + b"X" * 5000   # > MIN_IMAGE_SIZE


def _fake_frigate_session(cam, monkeypatch, status_for):
    """Wire _fetch_frigate_image's deps to a recording fake session. ``status_for``
    maps a URL to the HTTP status to return (200 bodies are ``_BIG``)."""
    requested: list = []
    monkeypatch.setattr(cam.aiohttp, "ClientTimeout", lambda **k: None, raising=False)
    monkeypatch.setattr(cam, "get_url", lambda *a, **k: "http://ha", raising=False)

    class _Resp:
        def __init__(self, url):
            self.status = status_for(url)

        async def read(self):
            return _BIG

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def get(self, url, **k):
            requested.append(url)
            return _Resp(url)

    monkeypatch.setattr(cam, "async_get_clientsession", lambda hass: _Session(), raising=False)
    monkeypatch.setitem(cam._EVENT_CACHE, "camera.front", {
        "source": "frigate", "event_id": "evt1",
        "ts": datetime.now(timezone.utc).replace(tzinfo=None)})
    return requested


async def test_fetch_frigate_image_prefers_crop(cam, monkeypatch):
    requested = _fake_frigate_session(cam, monkeypatch, status_for=lambda u: 200)
    out = await cam._fetch_frigate_image(None, "camera.front", crop=True)
    assert out == _BIG
    assert len(requested) == 1
    assert requested[0].endswith("/snapshot.jpg?crop=1")   # cropped tried first


async def test_fetch_frigate_image_crop_falls_back_on_non_200(cam, monkeypatch):
    requested = _fake_frigate_session(
        cam, monkeypatch, status_for=lambda u: 404 if "crop=1" in u else 200)
    out = await cam._fetch_frigate_image(None, "camera.front", crop=True)
    assert out == _BIG
    assert requested[0].endswith("?crop=1")           # cropped attempted
    assert requested[1].endswith("/snapshot.jpg")      # then uncropped fallback
    assert "crop=1" not in requested[1]


async def test_fetch_frigate_image_no_crop_requests_plain_only(cam, monkeypatch):
    requested = _fake_frigate_session(cam, monkeypatch, status_for=lambda u: 200)
    out = await cam._fetch_frigate_image(None, "camera.front")   # crop defaults False
    assert out == _BIG
    assert requested == ["http://ha/api/frigate/notifications/evt1/snapshot.jpg"]
