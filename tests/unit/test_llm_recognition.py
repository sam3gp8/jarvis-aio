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


# ── §7A: thinking=False, multi-sample lighting refs, Tier-1 short-circuit ────────

class _ThinkingClient:
    def __init__(self, text):
        self._text = text
        self.thinking = "unset"

    def chat(self, *, thinking=None, **kwargs):
        self.thinking = thinking
        return {"text": self._text}


class _NoThinkingClient:
    """A minimal stub whose chat() rejects a `thinking` kwarg (raises TypeError)."""
    def __init__(self, text):
        self._text = text
        self.called = False

    def chat(self, **kwargs):
        if "thinking" in kwargs:
            raise TypeError("unexpected keyword argument 'thinking'")
        self.called = True
        return {"text": self._text}


class _CountClient:
    def __init__(self, text):
        self._text = text
        self.images = 0

    def chat(self, *, messages, **kwargs):
        content = messages[-1]["content"]
        self.images = sum(1 for p in content if p.get("type") == "image_url")
        return {"text": self._text}


def test_match_sync_passes_thinking_false(mods):
    mods.fr.add_resident("Sam")
    mods.rec.set_face_reference("Sam", b"\xff\xd8ref")
    client = _ThinkingClient('{"name":"Sam","confidence":80}')
    out = mods.llm._match_sync(client, "m", "ZnJhbWU=")
    assert client.thinking is False
    assert out["name"] == "Sam"


def test_match_sync_falls_back_when_thinking_unsupported(mods):
    mods.fr.add_resident("Sam")
    mods.rec.set_face_reference("Sam", b"\xff\xd8ref")
    client = _NoThinkingClient('{"name":"Sam","confidence":80}')
    out = mods.llm._match_sync(client, "m", "ZnJhbWU=")
    assert client.called is True          # retried without the kwarg
    assert out["name"] == "Sam"


def test_match_sync_sends_multiple_lighting_refs(mods, monkeypatch):
    import jc.recognition as _r
    mods.fr.add_resident("Sam")
    mods.rec.set_face_reference("Sam", b"\xff\xd8manual")
    t = [5_000_000.0]
    monkeypatch.setattr(_r._time, "time", lambda: t[0])
    mods.rec._auto_enroll_reference_sync("Sam", b"\xff\xd8g1", bucket="day", camera="camera.a")
    client = _CountClient('{"name":"Sam","confidence":80}')
    out = mods.llm._match_sync(client, "m", "ZnJhbWU=")
    # Up to 2 reference frames for Sam (manual + 1 gallery) + the current frame.
    assert client.images == 3
    assert out["name"] == "Sam"


async def test_identify_and_store_skips_when_tier1_fresh(mods, fake_hass, monkeypatch):
    from datetime import datetime, timezone
    mods.fr.add_resident("Sam")
    monkeypatch.setitem(mods.rec._RECOGNITION_CACHE, "camera.front", {
        "name": "Sam", "confidence": 95.0,
        "ts": datetime.now(timezone.utc).replace(tzinfo=None), "unknown_count": 0})
    called = {"id": False}

    async def _spy(hass, image_b64, camera_entity, **k):
        called["id"] = True
        return {"name": "Sam", "confidence": 80.0}
    monkeypatch.setattr(mods.llm, "identify", _spy)
    assert await mods.llm.identify_and_store(fake_hass, "ZnJhbWU=", "camera.front") is None
    assert called["id"] is False          # Tier 1 leads → no vision tokens
    assert mods.rec._LLM_GUESS_CACHE == {}


# ── §7B: shadow calibration ─────────────────────────────────────────────────────

def _calib_env(mods, tmp_path, monkeypatch, *, flags=True):
    mods.llm._last_shadow_cal.clear()
    monkeypatch.setattr(mods.rec, "CALIBRATION_PATH", str(tmp_path / "cal.json"))
    mods.rec._CALIBRATION = {}
    mods.rec._calibration_loaded = False
    monkeypatch.setattr(mods.jc, "get", lambda k, d=None: flags)


async def test_shadow_calibrate_agreed(mods, fake_hass, tmp_path, monkeypatch):
    mods.fr.add_resident("Sam")
    _calib_env(mods, tmp_path, monkeypatch)

    async def _m(hass, b64, cam, **k):
        return {"name": "Sam", "confidence": 88.0}
    monkeypatch.setattr(mods.llm, "identify", _m)
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 90.0, b"img")
    recs = mods.rec._calibration_records("Sam")
    assert len(recs) == 1 and recs[0]["outcome"] == "agreed"


async def test_shadow_calibrate_wrong_person(mods, fake_hass, tmp_path, monkeypatch):
    mods.fr.add_resident("Sam")
    mods.fr.add_resident("Lee")
    _calib_env(mods, tmp_path, monkeypatch)

    async def _m(hass, b64, cam, **k):
        return {"name": "Lee", "confidence": 70.0}   # named the wrong resident
    monkeypatch.setattr(mods.llm, "identify", _m)
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 90.0, b"img")
    assert mods.rec._calibration_records("Sam")[0]["outcome"] == "wrong_person"


async def test_shadow_calibrate_abstained_when_no_match(mods, fake_hass, tmp_path, monkeypatch):
    mods.fr.add_resident("Sam")
    _calib_env(mods, tmp_path, monkeypatch)

    async def _none(hass, b64, cam, **k):
        return None
    monkeypatch.setattr(mods.llm, "identify", _none)
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 90.0, b"img")
    assert mods.rec._calibration_records("Sam")[0]["outcome"] == "abstained"


async def test_shadow_calibrate_gated_and_throttled(mods, fake_hass, tmp_path, monkeypatch):
    mods.fr.add_resident("Sam")

    async def _m(hass, b64, cam, **k):
        return {"name": "Sam", "confidence": 88.0}
    monkeypatch.setattr(mods.llm, "identify", _m)

    # Autotrain off → nothing recorded.
    _calib_env(mods, tmp_path, monkeypatch, flags=False)
    monkeypatch.setattr(mods.jc, "get",
                        lambda k, d=None: k == "llm_face_recognition")  # recog on, autotrain off
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 90.0, b"img")
    assert mods.rec._calibration_records("Sam") == []

    # Both flags on: low backend confidence (<85) → nothing.
    monkeypatch.setattr(mods.jc, "get", lambda k, d=None: True)
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 70.0, b"img")
    assert mods.rec._calibration_records("Sam") == []

    # Non-resident expected name → nothing.
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Quentin", 90.0, b"img")
    assert mods.rec._calibration_records("Quentin") == []

    # A valid call records once; a second within the throttle window does not.
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 90.0, b"img")
    await mods.llm.shadow_calibrate(fake_hass, "camera.front", "Sam", 90.0, b"img")
    assert len(mods.rec._calibration_records("Sam")) == 1
