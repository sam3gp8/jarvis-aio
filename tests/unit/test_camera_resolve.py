"""Spoken-reference → camera entity resolution (#541).

A small on-device model (and a voice/TTS layer that spells digits as words)
rarely emits a valid camera entity_id: it guesses ``camera.five`` for "cam 5"
or ``camera.garage`` for "the garage camera", entities that do not exist, and
the look fails. ``resolve_camera`` maps a loose reference to exactly one real
camera, or ``None`` when it is ambiguous / unknown so the caller lists the real
choices instead of guessing again.

The matcher is pure (no hass), so the rules are tested directly; a couple of
wrapper tests cover the hass-facing ``resolve_camera_entity`` pass-through and
the "no match → available list" contract.
"""
import sys
import types

import pytest


@pytest.fixture
def cam(load, monkeypatch):
    # camera.py imports third-party + heavy siblings at module load that the
    # pure resolver doesn't need — stub so the import resolves in isolation
    # (mirrors tests/unit/test_camera_coverage_prompt.py).
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


_CAMS = [
    {"entity_id": "camera.kamera_1", "name": "Kamera 1", "area": "Front Yard"},
    {"entity_id": "camera.kamera_4", "name": "Kamera 4", "area": "Garage"},
    {"entity_id": "camera.workshop", "name": "Workshop", "area": "Workshop"},
]


# ── exact id ─────────────────────────────────────────────────────────────────
def test_exact_entity_id(cam):
    assert cam.resolve_camera("camera.workshop", _CAMS) == "camera.workshop"


def test_exact_entity_id_without_domain(cam):
    assert cam.resolve_camera("workshop", _CAMS) == "camera.workshop"


# ── numbers: the core "cam 5" → "cam five" report ────────────────────────────
def test_bare_digit(cam):
    assert cam.resolve_camera("4", _CAMS) == "camera.kamera_4"


def test_number_word_english(cam):
    assert cam.resolve_camera("four", _CAMS) == "camera.kamera_4"


def test_number_word_german(cam):
    # The reporter speaks German; "vier" must land on camera 4.
    assert cam.resolve_camera("kamera vier", _CAMS) == "camera.kamera_4"


def test_cam_number_phrase(cam):
    assert cam.resolve_camera("cam 4", _CAMS) == "camera.kamera_4"
    assert cam.resolve_camera("camera 1", _CAMS) == "camera.kamera_1"
    assert cam.resolve_camera("check cam four", _CAMS) == "camera.kamera_4"


def test_missing_number_is_none_not_a_guess(cam):
    # "cam five" with no camera 5 → None, never a wrong camera.
    assert cam.resolve_camera("cam five", _CAMS) is None
    assert cam.resolve_camera("5", _CAMS) is None


def test_number_word_boundary_no_crosstalk(cam):
    cams = [
        {"entity_id": "camera.kamera_1", "name": "Kamera 1", "area": ""},
        {"entity_id": "camera.kamera_10", "name": "Kamera 10", "area": ""},
    ]
    assert cam.resolve_camera("cam 1", cams) == "camera.kamera_1"
    assert cam.resolve_camera("cam 10", cams) == "camera.kamera_10"


# ── room / name references: the "garage" report ──────────────────────────────
def test_area_reference(cam):
    assert cam.resolve_camera("garage", _CAMS) == "camera.kamera_4"


def test_area_reference_with_filler(cam):
    assert cam.resolve_camera("the garage camera", _CAMS) == "camera.kamera_4"
    assert cam.resolve_camera("camera in the garage", _CAMS) == "camera.kamera_4"


def test_name_reference(cam):
    assert cam.resolve_camera("workshop", _CAMS) == "camera.workshop"


# ── honesty: ambiguous / unknown → None (ask, don't guess) ───────────────────
def test_ambiguous_area_returns_none(cam):
    cams = [
        {"entity_id": "camera.garage_left", "name": "Garage Left", "area": "Garage"},
        {"entity_id": "camera.garage_right", "name": "Garage Right", "area": "Garage"},
    ]
    assert cam.resolve_camera("garage", cams) is None


def test_unknown_reference_returns_none(cam):
    assert cam.resolve_camera("spaceship", _CAMS) is None


def test_empty_inputs(cam):
    assert cam.resolve_camera("", _CAMS) is None
    assert cam.resolve_camera("workshop", []) is None
    assert cam.resolve_camera(None, _CAMS) is None


# ── hass-facing wrapper ──────────────────────────────────────────────────────
class _St:
    def __init__(self, entity_id, friendly_name):
        self.entity_id = entity_id
        self.attributes = {"friendly_name": friendly_name}


class _States:
    def __init__(self, states):
        self._d = {s.entity_id: s for s in states}

    def get(self, eid):
        return self._d.get(eid)

    def async_all(self, domain=None):
        vals = list(self._d.values())
        if domain is None:
            return vals
        return [s for s in vals if s.entity_id.split(".")[0] == domain]


class _Hass:
    def __init__(self, states):
        self.states = _States(states)


def test_wrapper_real_entity_passthrough(cam):
    hass = _Hass([_St("camera.workshop", "Workshop")])
    eid, available = cam.resolve_camera_entity(hass, "camera.workshop")
    assert eid == "camera.workshop"
    assert available is None


def test_wrapper_resolves_guessed_number_word(cam):
    # The model guessed camera.four (nonexistent); resolve to the real camera 4.
    hass = _Hass([_St("camera.kamera_4", "Kamera 4"),
                  _St("camera.kamera_1", "Kamera 1")])
    eid, available = cam.resolve_camera_entity(hass, "camera.four")
    assert eid == "camera.kamera_4"
    assert available is None


def test_wrapper_unknown_lists_available(cam):
    hass = _Hass([_St("camera.kamera_4", "Kamera 4")])
    eid, available = cam.resolve_camera_entity(hass, "camera.does_not_exist")
    assert eid is None
    assert "Kamera 4" in available
