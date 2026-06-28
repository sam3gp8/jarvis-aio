"""Regression tests for the ProsodyController vocal-profile rules.

ProsodyController is stdlib-only (no Home Assistant), so it is loaded standalone
rather than through the jc harness. These pin the exact spec values for every
rule branch plus the quiet-hours wrap and junk-telemetry robustness.
"""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # registered before exec so @dataclass resolves
    spec.loader.exec_module(mod)
    return mod


prosody = _load_standalone("jarvis_prosody", "audio/prosody.py")


@pytest.fixture
def pc():
    return prosody.ProsodyController(quiet_hours_start=22, quiet_hours_end=7)


def test_critical_overrides_everything(pc):
    # Even at 3am in a dark, silent room, critical is authoritative + full volume.
    out = pc.calculate_vocal_profile(
        {"critical_alert": True, "hour": 3, "ambient_lux": 1.0, "ambient_db": 20.0}
    )
    assert out == {
        "volume": 1.0, "speech_rate": 1.15, "style": "authoritative",
        "whisper_mode": False, "duck_media": True,
    }


def test_deep_quiet_is_whisper(pc):
    out = pc.calculate_vocal_profile({"hour": 23, "ambient_lux": 2.0, "ambient_db": 30.0})
    assert out["style"] == "whisper"
    assert out["whisper_mode"] is True
    assert out["volume"] == 0.25
    assert out["speech_rate"] == 0.95


def test_quiet_hours_alone_is_subdued(pc):
    # Quiet hours but a bright room → subdued, not whisper.
    out = pc.calculate_vocal_profile({"hour": 23, "ambient_lux": 200, "ambient_db": 30.0})
    assert out["style"] == "subdued"
    assert out["volume"] == 0.4
    assert out["whisper_mode"] is False


def test_subdued_ducks_media_for_audibility(pc):
    out = pc.calculate_vocal_profile(
        {"hour": 23, "ambient_lux": 200, "ambient_db": 30.0, "media_playing": True}
    )
    assert out["style"] == "subdued"
    assert out["duck_media"] is True  # soft voice still ducks media so it's heard


def test_loud_room_projects(pc):
    out = pc.calculate_vocal_profile({"hour": 14, "ambient_db": 70.0})
    assert out["style"] == "projected"
    assert out["volume"] == 0.9
    assert out["duck_media"] is True


def test_media_playing_projects_daytime(pc):
    out = pc.calculate_vocal_profile({"hour": 14, "ambient_db": 30.0, "media_playing": True})
    assert out["style"] == "projected"


def test_default_is_neutral(pc):
    out = pc.calculate_vocal_profile({"hour": 14, "ambient_lux": 300, "ambient_db": 40.0})
    assert out["style"] == "neutral"
    assert out["volume"] == 0.6


def test_quiet_hours_wraps_midnight(pc):
    assert pc.in_quiet_hours(23) is True
    assert pc.in_quiet_hours(3) is True
    assert pc.in_quiet_hours(7) is False   # end is exclusive
    assert pc.in_quiet_hours(12) is False


def test_non_wrapping_quiet_window():
    pc = prosody.ProsodyController(quiet_hours_start=1, quiet_hours_end=6)
    assert pc.in_quiet_hours(3) is True
    assert pc.in_quiet_hours(0) is False
    assert pc.in_quiet_hours(6) is False


@pytest.mark.parametrize("junk", [
    {},
    {"ambient_lux": "unavailable", "ambient_db": None},
    {"ambient_db": "NaN", "ambient_lux": "unknown"},
    {"critical_alert": "yes"},  # truthy non-bool
])
def test_junk_telemetry_never_crashes(pc, junk):
    out = pc.calculate_vocal_profile(junk)
    assert set(out) == {"volume", "speech_rate", "style", "whisper_mode", "duck_media"}


def test_skip_preamble_eases_speech_rate(pc):
    base = pc.calculate_vocal_profile({"critical_alert": True})
    eased = pc.calculate_vocal_profile({"critical_alert": True, "skip_preamble": True})
    assert eased["speech_rate"] == round(base["speech_rate"] - 0.05, 2)  # 1.15 → 1.10
    assert eased["style"] == "authoritative"  # only the rate changes


def test_skip_preamble_applies_to_whisper(pc):
    eased = pc.calculate_vocal_profile(
        {"hour": 2, "ambient_lux": 1.0, "ambient_db": 20.0, "skip_preamble": True}
    )
    assert eased["speech_rate"] == 0.90  # whisper 0.95 − 0.05
    assert eased["style"] == "whisper"


def test_skip_preamble_false_leaves_rate(pc):
    assert pc.calculate_vocal_profile({"critical_alert": True})["speech_rate"] == 1.15


def test_media_active_alias_projects(pc):
    # media_active (spec key) behaves like the legacy media_playing key
    out = pc.calculate_vocal_profile({"hour": 14, "ambient_db": 30.0, "media_active": True})
    assert out["style"] == "projected"
