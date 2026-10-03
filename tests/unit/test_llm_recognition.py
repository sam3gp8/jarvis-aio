"""Tests for best-effort LLM resident recognition (#140 Phase 3).

The guard that matters most is covered in test_recognition_faces.py
(resident_present never reads the guess cache). Here we pin the matcher's own
behaviour: parsing, the opt-in gate, reference requirement, and that a match is
recorded only as a low-confidence guess."""
import pytest


@pytest.fixture
def mods(load, tmp_path, monkeypatch):
    fr = load("face_roster")
    monkeypatch.setattr(fr, "ROSTER_PATH", str(tmp_path / "face_roster.json"))
    fr._loaded = False
    fr._roster = {}

    rec = load("recognition")
    monkeypatch.setattr(rec, "FACE_REF_DIR", str(tmp_path / "faces_ref"))
    rec._LLM_GUESS_CACHE.clear()

    jc = load("jarvis_config")
    llm = load("llm_recognition")
    return type("M", (), {"fr": fr, "rec": rec, "jc": jc, "llm": llm})


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.called = False

    def chat(self, **kwargs):
        self.called = True
        return {"text": self._text}


# ── _parse_result ───────────────────────────────────────────────────────────────

def test_parse_result_matches_enrolled_resident(mods):
    allowed = {"sam": "Sam", "lee": "Lee"}
    out = mods.llm._parse_result('{"name": "Sam", "confidence": 88}', allowed)
    assert out == {"name": "Sam", "confidence": 88.0}


def test_parse_result_unknown_is_none(mods):
    assert mods.llm._parse_result('{"name": "unknown", "confidence": 0}', {"sam": "Sam"}) is None


def test_parse_result_non_resident_name_is_none(mods):
    # The model naming someone who isn't an enrolled resident is rejected.
    assert mods.llm._parse_result('{"name": "Burglar", "confidence": 95}', {"sam": "Sam"}) is None


def test_parse_result_clamps_confidence_and_tolerates_fences(mods):
    out = mods.llm._parse_result('```json\n{"name":"Sam","confidence":250}\n```', {"sam": "Sam"})
    assert out["name"] == "Sam" and out["confidence"] == 100.0


def test_parse_result_garbage_is_none(mods):
    assert mods.llm._parse_result("not json at all", {"sam": "Sam"}) is None


# ── _match_sync (references + a fake vision client) ─────────────────────────────

def test_match_sync_identifies_resident_with_reference(mods):
    mods.fr.add_resident("Sam")
    assert mods.rec.set_face_reference("Sam", b"\xff\xd8\xff\xe0JPEG") is True
    client = _FakeClient('{"name": "Sam", "confidence": 77}')
    out = mods.llm._match_sync(client, "vision-model", "ZnJhbWU=")
    assert client.called is True
    assert out == {"name": "Sam", "confidence": 77.0}


def test_match_sync_no_reference_skips_call(mods):
    # Resident exists but has no enrolled reference → no LLM call, no match.
    mods.fr.add_resident("Sam")
    client = _FakeClient('{"name": "Sam", "confidence": 99}')
    out = mods.llm._match_sync(client, "vision-model", "ZnJhbWU=")
    assert out is None
    assert client.called is False


def test_match_sync_no_residents_skips_call(mods):
    client = _FakeClient('{"name": "Sam", "confidence": 99}')
    assert mods.llm._match_sync(client, "vision-model", "ZnJhbWU=") is None
    assert client.called is False


# ── identify gate + identify_and_store ──────────────────────────────────────────

async def test_identify_disabled_returns_none(mods, fake_hass, monkeypatch):
    # Opt-in: with the flag off, identify never touches the model.
    monkeypatch.setattr(mods.jc, "get", lambda k, d=None: False)
    assert await mods.llm.identify(fake_hass, "ZnJhbWU=", "camera.front") is None


async def test_identify_and_store_records_low_conf_guess(mods, fake_hass, monkeypatch):
    async def _fake_identify(hass, image_b64, camera_entity):
        return {"name": "Sam", "confidence": 72.0}
    monkeypatch.setattr(mods.llm, "identify", _fake_identify)

    out = await mods.llm.identify_and_store(fake_hass, "ZnJhbWU=", "camera.front_door")
    assert out == {"name": "Sam", "confidence": 72.0}
    # Recorded only in the separate guess cache (never the trusted one).
    assert mods.rec._LLM_GUESS_CACHE["camera.front_door"]["name"] == "Sam"
    assert mods.rec._RECOGNITION_CACHE == {}


async def test_identify_and_store_no_match_records_nothing(mods, fake_hass, monkeypatch):
    async def _none(hass, image_b64, camera_entity):
        return None
    monkeypatch.setattr(mods.llm, "identify", _none)
    assert await mods.llm.identify_and_store(fake_hass, "ZnJhbWU=", "camera.front") is None
    assert mods.rec._LLM_GUESS_CACHE == {}
