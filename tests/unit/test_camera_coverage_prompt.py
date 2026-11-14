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
