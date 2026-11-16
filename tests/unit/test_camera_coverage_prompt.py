"""Camera floor-plan coverage → analysis prompt (v7.91.0).

When a camera's floor-plan coverage lists more than the room it's named after
(the Dining Room camera also frames the Living Room), the analysis prompt must
carry every covered area so the model reports on all of them. These test the
pure parsing/hinting that carries that correctness, from the real
floor_plan_cameras shape (dict or JSON string).
"""
import sys
import types

import pytest

# Real shape (trimmed) from a live floor_plan_cameras value.
_FPC = {
    "1f": [
        {"id": "a", "entity": "camera.dining_room", "angle": 45, "fov": 170, "range": 175,
         "coverage": {"covered": ["Dining Room", "Living Room"], "reason": "…", "source": "llm"}},
        {"id": "b", "entity": "camera.kitchen", "angle": 225, "fov": 170, "range": 75,
         "coverage": {"covered": ["Kitchen"], "reason": "…", "source": "llm"}},
        {"id": "c", "entity": "camera.no_coverage_yet"},
    ]
}


@pytest.fixture
def cam(load, monkeypatch):
    # Third-party + heavy siblings camera.py imports at module load but that the
    # pure coverage helpers don't need — stub so the import resolves in isolation.
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
    yield mod
    sys.modules.pop("jc.camera", None)


def test_parse_coverage_from_dict(cam):
    assert cam._parse_coverage_rooms(_FPC, "camera.dining_room") == ["Dining Room", "Living Room"]
    assert cam._parse_coverage_rooms(_FPC, "camera.kitchen") == ["Kitchen"]


def test_parse_coverage_from_json_string(cam):
    import json
    raw = json.dumps(_FPC)
    assert cam._parse_coverage_rooms(raw, "camera.dining_room") == ["Dining Room", "Living Room"]


def test_parse_coverage_unknown_or_missing(cam):
    assert cam._parse_coverage_rooms(_FPC, "camera.does_not_exist") == []
    assert cam._parse_coverage_rooms(_FPC, "camera.no_coverage_yet") == []
    assert cam._parse_coverage_rooms("", "camera.dining_room") == []
    assert cam._parse_coverage_rooms("not json", "camera.dining_room") == []
    assert cam._parse_coverage_rooms({"1f": "bogus"}, "camera.dining_room") == []


def test_coverage_hint_wording(cam):
    assert cam._coverage_hint([]) == ""
    single = cam._coverage_hint(["Kitchen"])
    assert "Kitchen" in single
    multi = cam._coverage_hint(["Dining Room", "Living Room"])
    assert "Dining Room" in multi and "Living Room" in multi
    assert "EACH" in multi  # instructs the model to report on every covered area


# ── Issue A: Frigate grounding + low-light hardening (v7.92.0) ────────────────
def test_frigate_detections_reads_occupancy(cam, fake_hass):
    fake_hass.states.set("binary_sensor.front_yard_person", "on")
    fake_hass.states.set("binary_sensor.front_yard_car", "off")
    dets = cam._frigate_detections(fake_hass, "camera.front_yard")
    assert dets == {"person": True, "car": False}


def test_frigate_detections_count_sensor(cam, fake_hass):
    fake_hass.states.set("sensor.driveway_car", "2")
    fake_hass.states.set("binary_sensor.driveway_person", "off")
    dets = cam._frigate_detections(fake_hass, "camera.driveway")
    assert dets["car"] is True and dets["person"] is False


def test_frigate_detections_none_when_absent(cam, fake_hass):
    # No Frigate object entities for this camera → None (grounding skipped, never
    # a false "empty scene").
    assert cam._frigate_detections(fake_hass, "camera.nonexistent") is None


def test_frigate_ground_hint_person_absent(cam):
    assert cam._frigate_ground_hint(None) == ""
    hint = cam._frigate_ground_hint({"person": False, "car": True})
    assert "NO person" in hint and "car" in hint
    assert "person" in cam._frigate_ground_hint({"person": True})
    assert "car" in cam._frigate_ground_hint({"car": True})


def test_lowlight_hint_present(cam):
    assert "infrared" in cam._LOWLIGHT_HINT and "shadows" in cam._LOWLIGHT_HINT
    # a hazy frame must not yield a confident vehicle either (v9.130.0)
    assert "vehicles and objects" in cam._LOWLIGHT_HINT
    assert "say that the view is unclear" in cam._LOWLIGHT_HINT



# ── Announce gating: severity + alert level (v7.94.0) ────────────────────────
def _j(severity, speak="Someone unknown is at the front door.", notable=None):
    if notable is None:
        notable = severity in ("urgent", "notable")
    return {"severity": severity, "notable": notable, "category": "person",
            "speak": speak, "summary": "x"}


def test_urgent_level_announces_only_urgent(cam):
    assert cam._announce_decision(_j("urgent"), "d", gate_announce=True, level="urgent") \
        == "Someone unknown is at the front door."
    assert cam._announce_decision(_j("notable", speak="A package was dropped off."),
                                  "d", gate_announce=True, level="urgent") is None
    assert cam._announce_decision(_j("routine", speak=""), "d",
                                  gate_announce=True, level="urgent") is None


def test_important_level_adds_notable(cam):
    assert cam._announce_decision(_j("notable", speak="A package was dropped off."),
                                  "d", gate_announce=True, level="important") == "A package was dropped off."
    assert cam._announce_decision(_j("urgent"), "d", gate_announce=True, level="important") \
        == "Someone unknown is at the front door."


def test_off_level_is_silent(cam):
    assert cam._announce_decision(_j("urgent"), "d", gate_announce=True, level="off") is None


def test_all_level_narrates_with_summary(cam):
    j = {"severity": "notable", "notable": True, "speak": "", "summary": "A car in the drive."}
    assert cam._announce_decision(j, "d", gate_announce=True, level="all") == "A car in the drive."


def test_manual_always_reports(cam):
    # Manual analyze (gate_announce False) reports regardless of level/severity.
    assert cam._announce_decision(_j("routine", speak=""), "full analysis",
                                  gate_announce=False, level="urgent") == "full analysis"
    assert cam._announce_decision(_j("urgent"), "full analysis",
                                  gate_announce=False, level="off") == "Someone unknown is at the front door."


def test_failed_or_empty_judgment_is_silent_on_auto(cam):
    # The fail-quiet fallback shape must never announce on an auto review.
    fb = {"notable": False, "severity": "routine", "category": "motion", "summary": "", "speak": None}
    for lvl in ("urgent", "important", "all"):
        assert cam._announce_decision(fb, "d", gate_announce=True, level=lvl) is None


def test_missing_severity_backcompat(cam):
    # No severity field: notable→rank 1 (announced at important/all, not urgent).
    j = {"notable": True, "speak": "thing", "summary": "s"}
    assert cam._announce_decision(j, "d", gate_announce=True, level="urgent") is None
    assert cam._announce_decision(j, "d", gate_announce=True, level="important") == "thing"


def test_level_rank_ordering(cam):
    assert cam._LEVEL_RANK["urgent"] == 2
    assert cam._LEVEL_RANK["off"] > cam._LEVEL_RANK["important"] >= cam._LEVEL_RANK["all"]
    assert cam._SEV_RANK["urgent"] > cam._SEV_RANK["notable"] > cam._SEV_RANK["routine"]


# ── Scene-severity rubric: stop announcing ordinary passers-by (v9.78.0) ──────
def test_rubric_declares_unrecognised_is_not_notable(cam):
    r = cam._SCENE_SEVERITY_RUBRIC
    # the headline policy: being unrecognised is not, by itself, worth announcing
    assert "being unrecognised is NOT by itself notable" in r
    assert "Do NOT announce ordinary passers-by." in r
    # public-space / passing traffic is explicitly routine
    assert "passing by on a street" in r
    assert "recognised or not" in r
    # notable is reserved for coming ONTO the property; urgent stays for threats
    assert "ONTO the property" in r
    assert "prowler" in r


async def test_rubric_reaches_the_reasoning_model(cam, monkeypatch):
    # A street passer-by must carry the recalibrated rubric to the model, and the
    # model's 'routine' verdict must pass through so nothing is announced.
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"] = messages[0]["content"]
        return {"text": ('{"severity":"routine","notable":false,'
                         '"category":"person","summary":"A person walks along the '
                         'street.","speak":""}')}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "front_yard",
        "A person is walking along the sidewalk out on the street.", "motion")
    assert "Do NOT announce ordinary passers-by." in captured["system"]
    assert "being unrecognised is NOT by itself notable" in captured["system"]
    assert out["severity"] == "routine"
    assert out["speak"] is None          # routine → nothing to say


# ── Property-only scope: watch only inside the property line (v9.80.0) ────────
def test_property_scope_directive_and_default(cam):
    d = cam._PROPERTY_SCOPE_DIRECTIVE
    assert "PROPERTY ONLY" in d
    assert "out of scope" in d and "street" in d and "sidewalk" in d
    assert "Do NOT describe, flag, or raise" in d
    # default is ON: confined to the property unless told otherwise
    assert cam._property_scope_only(None) is True


async def test_property_scope_on_for_auto_review(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"] = messages[0]["content"]
        return {"text": '{"severity":"routine","notable":false,"category":"empty","summary":"x","speak":""}'}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    await cam._reason_about_scene(
        None, object(), "", "front_yard", "A car drives past on the street.",
        "motion", property_only=True)
    assert "SCOPE — PROPERTY ONLY" in captured["system"]


async def test_property_scope_off_for_manual_look(cam, monkeypatch):
    # A manual analyze ("specifically told otherwise") must see the whole frame.
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"] = messages[0]["content"]
        return {"text": '{"severity":"routine","notable":false,"category":"other","summary":"x","speak":""}'}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    await cam._reason_about_scene(
        None, object(), "", "front_yard", "A person on the sidewalk.",
        "motion", property_only=False)
    assert "SCOPE — PROPERTY ONLY" not in captured["system"]
    # the severity rubric is still present either way
    assert "being unrecognised is NOT by itself notable" in captured["system"]


# ── Property lines grounded in the floor plan (v9.81.0) ──────────────────────
_FLOOR_PLAN = {
    "1f": {"rooms": [
        {"name": "Living Room", "type": "room"},
        {"name": "Front Yard", "type": "outdoor"},
        {"name": "Driveway", "type": "outdoor"},
        {"name": "Street", "type": "outdoor"},
        {"name": "Easement", "type": "outdoor"},
    ]}
}


def _with_floor_plan(cam, monkeypatch, fp):
    import json
    monkeypatch.setattr(
        cam, "_cfg_opt",
        lambda hass, key, default=None: (json.dumps(fp) if key == "floor_plan_rooms" else default),
        raising=False)


def test_public_area_names_from_floor_plan(cam, monkeypatch):
    _with_floor_plan(cam, monkeypatch, _FLOOR_PLAN)
    pub = cam._public_area_names(None)
    # the street is off-property …
    assert "street" in pub
    # … the property's own areas and interior rooms are not; an EASEMENT is the
    # owner's land, so it is on-property by default (v9.83.0)
    assert "easement" not in pub
    assert "front yard" not in pub
    assert "driveway" not in pub
    assert "living room" not in pub


def test_easement_public_only_when_tagged(cam, monkeypatch):
    # Default: on-property. Explicit scope:public opts it out.
    _with_floor_plan(cam, monkeypatch, {"1f": {"rooms": [
        {"name": "Easement", "type": "outdoor"},
    ]}})
    assert "easement" not in cam._public_area_names(None)
    _with_floor_plan(cam, monkeypatch, {"1f": {"rooms": [
        {"name": "Easement", "type": "outdoor", "scope": "public"},
    ]}})
    assert "easement" in cam._public_area_names(None)


def test_public_areas_in_view_subsets_coverage(cam, monkeypatch):
    _with_floor_plan(cam, monkeypatch, _FLOOR_PLAN)
    assert cam._public_areas_in_view(None, ["Front Yard", "Driveway", "Street"]) == ["Street"]
    assert cam._public_areas_in_view(None, ["Living Room"]) == []
    # no floor plan → empty (generic directive still applies)
    monkeypatch.setattr(cam, "_cfg_opt", lambda *a, **k: "{}", raising=False)
    assert cam._public_areas_in_view(None, ["Street"]) == []


def test_public_area_explicit_tag_overrides(cam, monkeypatch):
    fp = {"1f": {"rooms": [
        {"name": "Side Lot", "type": "outdoor", "scope": "public"},   # force public
        {"name": "Street", "type": "outdoor", "public": False},       # force on-property
    ]}}
    _with_floor_plan(cam, monkeypatch, fp)
    pub = cam._public_area_names(None)
    assert "side lot" in pub
    assert "street" not in pub       # explicit private override beats the name


async def test_scope_block_names_the_public_area(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return {"text": '{"severity":"routine","notable":false,"category":"empty","summary":"x","speak":""}'}

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    await cam._reason_about_scene(
        None, object(), "", "front_yard", "A car passes on the street.", "motion",
        covered=["Front Yard", "Driveway", "Street"], property_only=True,
        public_areas=["Street"])
    # the floor-plan-grounded callout names the public area in the system prompt
    assert "NOT part of the property" in captured["system"]
    assert "Street" in captured["system"]
    # and the areas line marks it so the model reads the boundary from the plan
    assert "Street [public — NOT the property]" in captured["user"]


def test_strip_think(cam):
    assert cam._strip_think("<think>reasoning</think>Hello") == "Hello"
    assert cam._strip_think("before<think>x</think>after") == "beforeafter"
    assert cam._strip_think("<think>only thinking</think>") == ""
    assert cam._strip_think("plain text") == "plain text"
    assert cam._strip_think("") == ""
    # An unterminated (budget-truncated) think block strips to nothing — the
    # exact #514 symptom a reasoning vision model produces.
    assert cam._strip_think("<think>reasoning that never clo") == ""


def test_no_think_suffix(cam):
    # #514: Qwen reasons by default on the OpenAI-compat Ollama path, exhausting
    # the vision budget. The suffix tells it (and any reasoner) to answer
    # directly; Qwen also gets its /no_think control token.
    q = cam._no_think_suffix("qwen358q4k4bfix:latest")
    assert "/no_think" in q
    assert "reasoning" in q.lower() and "<think>" in q
    # A Qwen3-VL variant is covered by the substring match too.
    assert "/no_think" in cam._no_think_suffix("qwen3-vl:8b")
    # Non-reasoning vision models get the plain instruction but NOT the Qwen
    # control token (harmless, but keep it model-specific).
    g = cam._no_think_suffix("gemma3:12b")
    assert "/no_think" not in g
    assert "directly" in g.lower()
    assert cam._no_think_suffix("") and "/no_think" not in cam._no_think_suffix("")


# ── Property line read from the floor-plan GEOMETRY (v9.130.0) ───────────────
# Real report: a Front Yard camera logged "a vehicle is parked on the driveway"
# for a car on the neighbour's driveway. The frame was described from the saved
# ≥70% "confirmable" list (so the camera "covered the Driveway"), the property
# polygon was never read, and the vision step was told nothing about scope.
_GEO_ROOMS = {"1f": {"rooms": [
    {"name": "House", "x": 0, "y": 0, "w": 300, "h": 100, "type": "room"},
    {"name": "Kitchen", "x": 0, "y": 0, "w": 100, "h": 100, "type": "room"},
    {"name": "Front Yard", "type": "outdoor", "x": -100, "y": -150, "w": 500, "h": 150},
    {"name": "Driveway", "type": "outdoor", "x": 200, "y": -150, "w": 100, "h": 150},
    {"name": "Street", "type": "outdoor", "x": -500, "y": -220, "w": 1300, "h": 70},
]}}
_GEO_PROPERTY = {"points": [[-100, -150], [400, -150], [400, 300], [-100, 300]]}
_GEO_CAMS = {"1f": [
    {"entity": "camera.front", "x": 5, "y": -5, "angle": 315, "fov": 90,
     "range": 900, "indoor": False,
     "coverage": {"covered": ["Driveway"], "source": "llm"}},
    {"entity": "camera.kitchen", "x": 50, "y": 50, "angle": 0, "fov": 120,
     "range": 60, "indoor": True,
     "coverage": {"covered": ["Kitchen"], "source": "geometry"}},
]}


def _with_geometry(cam, monkeypatch):
    import json
    vals = {"floor_plan_rooms": json.dumps(_GEO_ROOMS),
            "floor_plan_cameras": json.dumps(_GEO_CAMS),
            "floor_plan_property": json.dumps(_GEO_PROPERTY)}
    monkeypatch.setattr(
        cam, "_cfg_opt",
        lambda hass, key, default=None: vals.get(key, default), raising=False)


def test_frame_context_outdoor_camera_is_read_from_the_plan(cam, monkeypatch):
    _with_geometry(cam, monkeypatch)
    fr = cam._frame_context(None, "camera.front", True)
    # what is actually in frame — not the saved "confirmable" list (["Driveway"])
    assert fr["areas"] == ["Front Yard", "Driveway", "Street"]
    assert "This camera's view covers the Driveway" not in fr["hint"]
    # the part of the view that is not the property is named
    assert fr["public"] == ["Street"]
    assert fr["offsite"] == ["a neighbouring property"]
    assert fr["beyond_property"] is True
    assert "a neighbouring property — not this home" in fr["boundary"]
    # and the VISION step is told to keep to the property and to place things
    assert "Describe what is happening ON this home's property." in fr["hint"]
    assert "never describe it as being in this home's driveway or yard" in fr["hint"]


def test_frame_context_manual_look_places_but_does_not_confine(cam, monkeypatch):
    _with_geometry(cam, monkeypatch)
    fr = cam._frame_context(None, "camera.front", False)
    assert "Describe what is happening ON this home's property." not in fr["hint"]
    assert "say where it is" in fr["hint"]          # still must not misplace a car
    assert fr["boundary"]


def test_frame_context_indoor_camera_is_unchanged(cam, monkeypatch):
    _with_geometry(cam, monkeypatch)
    assert cam._frame_context(None, "camera.kitchen", True) == {
        "areas": ["Kitchen"], "hint": " This camera's view covers the Kitchen.",
        "boundary": "", "public": [], "offsite": [], "beyond_property": False}


def test_frame_context_camera_not_on_the_plan_gets_the_generic_scope(cam, monkeypatch):
    _with_geometry(cam, monkeypatch)
    assert cam._camera_is_indoor(None, "camera.doorbell") is None
    assert cam._frame_context(None, "camera.doorbell", True)["hint"] == cam._VISION_SCOPE_GENERIC
    # a manual look is not confined
    assert cam._frame_context(None, "camera.doorbell", False)["hint"] == ""


def test_frigate_hint_says_a_whole_frame_detection_does_not_locate(cam):
    assert "does not by itself place" not in cam._frigate_ground_hint({"car": True})
    scoped = cam._frigate_ground_hint({"car": True}, beyond_property=True)
    assert "car" in scoped and "does not by itself place them on the property" in scoped
    absent = cam._frigate_ground_hint({"person": False, "car": True}, beyond_property=True)
    assert "NO person" in absent and "does not by itself place" in absent
    # nothing detected → there is nothing to caveat
    assert "does not by itself place" not in cam._frigate_ground_hint(
        {"person": False}, beyond_property=True)


def _reply(**fields):
    import json
    base = {"severity": "routine", "notable": False, "category": "vehicle",
            "summary": "A car is parked on the neighbouring property.", "speak": ""}
    base.update(fields)
    return {"text": json.dumps(base)}


async def test_reasoning_step_gets_the_boundary_and_marks_what_is_off_property(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"], captured["user"] = messages[0]["content"], messages[1]["content"]
        return _reply(on_property=False)

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    await cam._reason_about_scene(
        None, object(), "", "Front Yard", "A car is parked next door.", "motion",
        covered=["Front Yard", "Driveway", "Street"], frigate_dets={"car": True},
        property_only=True, public_areas=["Street"],
        offsite_areas=["a neighbouring property"],
        boundary="BOUNDARY-TEXT", beyond_property=True)
    assert "CAMERA VIEW (from the home's floor plan): BOUNDARY-TEXT" in captured["system"]
    assert "Street, a neighbouring property" in captured["system"]   # the callout
    assert '"on_property": true|false' in captured["system"]
    assert "Street [public — NOT the property]" in captured["user"]
    assert "a neighbouring property [NOT the property]" in captured["user"]
    assert "anywhere in the frame, including beyond the property line" in captured["user"]


async def test_off_property_only_scene_is_quiet_and_filed_as_empty(cam, monkeypatch):
    # The model flags it notable and wants to speak, but says nothing described is
    # on the property → the code, not the prose, keeps it off this home's record.
    async def _fake_chat(hass, client, *, messages, **kw):
        return _reply(severity="notable", notable=True, on_property=False,
                      speak="Sir, a vehicle is parked nearby.")

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "Front Yard", "A car is parked next door.", "motion",
        property_only=True, beyond_property=True)
    assert out["on_property"] is False
    assert out["severity"] == "routine" and out["notable"] is False
    assert out["speak"] is None
    assert out["category"] == "empty"      # not learned/remembered as a vehicle here
    assert out["summary"] == "A car is parked on the neighbouring property."


async def test_urgent_is_never_downgraded_by_the_location_verdict(cam, monkeypatch):
    async def _fake_chat(hass, client, *, messages, **kw):
        return _reply(severity="urgent", notable=True, on_property=False,
                      category="person", speak="Sir, someone is at the fence.")

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "Front Yard", "x", "motion", property_only=True)
    assert out["severity"] == "urgent" and out["category"] == "person"
    assert out["speak"] == "Sir, someone is at the fence."


async def test_manual_look_is_not_overridden(cam, monkeypatch):
    async def _fake_chat(hass, client, *, messages, **kw):
        return _reply(severity="notable", notable=True, on_property=False, speak="A car next door.")

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "Front Yard", "x", "motion",
        property_only=False, boundary="BOUNDARY-TEXT")
    assert out["category"] == "vehicle" and out["speak"] == "A car next door."
    assert out["on_property"] is False     # reported, just not acted on


async def test_no_verdict_from_the_model_changes_nothing(cam, monkeypatch):
    async def _fake_chat(hass, client, *, messages, **kw):
        return _reply(severity="notable", notable=True, speak="A car in the driveway.")

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "Driveway", "x", "motion", property_only=True)
    assert out["on_property"] is None
    assert out["category"] == "vehicle" and out["severity"] == "notable"
    assert out["speak"] == "A car in the driveway."


async def test_prompt_is_unchanged_when_there_is_nothing_to_locate(cam, monkeypatch):
    captured = {}

    async def _fake_chat(hass, client, *, messages, **kw):
        captured["system"] = messages[0]["content"]
        return _reply()

    monkeypatch.setattr(cam, "_capped_camera_chat", _fake_chat)
    out = await cam._reason_about_scene(
        None, object(), "", "Kitchen", "x", "motion", property_only=False)
    assert "on_property" not in captured["system"]
    assert "CAMERA VIEW" not in captured["system"]
    assert out["on_property"] is None


def test_as_tristate(cam):
    assert cam._as_tristate(True) is True and cam._as_tristate(False) is False
    assert cam._as_tristate("true") is True and cam._as_tristate(" No ") is False
    for junk in (None, "", "maybe", 0, 1, [], {}):
        assert cam._as_tristate(junk) is None
