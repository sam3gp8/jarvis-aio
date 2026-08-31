"""Adaptive suggestion threshold (opt-in) — the calibration→threshold loop.

The suggestion confidence bar is nudged by how welcome recent suggestions were:
off by default, needs a minimum sample, bounded delta, clamped result, and it
reads ONLY 'suggestion' outcomes (never security/intrusion).
"""
import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


def _flag(monkeypatch, load, on):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: on if k == "adaptive_suggestion_threshold" else d)


def _rate(monkeypatch, load, judged, unwelcome):
    dr = load("decision_record")
    monkeypatch.setattr(dr, "outcome_rate",
                        lambda kind, window_s=None, db_path=None: {
                            "kind": kind, "judged": judged, "unwelcome_rate": unwelcome})


def test_delta_off_by_default(pa, load, monkeypatch):
    _flag(monkeypatch, load, False)
    pa._ADAPT_CACHE["ts"] = 0.0
    assert pa._learned_threshold_delta() == 0.0


def test_delta_zero_without_enough_evidence(pa, load, monkeypatch):
    _flag(monkeypatch, load, True)
    _rate(monkeypatch, load, judged=3, unwelcome=0.9)   # below min sample
    pa._ADAPT_CACHE["ts"] = 0.0
    assert pa._learned_threshold_delta() == 0.0


def test_delta_raises_when_mostly_unwelcome(pa, load, monkeypatch):
    _flag(monkeypatch, load, True)
    _rate(monkeypatch, load, judged=10, unwelcome=0.6)
    pa._ADAPT_CACHE["ts"] = 0.0
    assert pa._learned_threshold_delta() == 0.15


def test_delta_moderate_when_somewhat_unwelcome(pa, load, monkeypatch):
    _flag(monkeypatch, load, True)
    _rate(monkeypatch, load, judged=10, unwelcome=0.35)
    pa._ADAPT_CACHE["ts"] = 0.0
    assert pa._learned_threshold_delta() == 0.07


def test_delta_lowers_when_almost_all_welcome(pa, load, monkeypatch):
    _flag(monkeypatch, load, True)
    _rate(monkeypatch, load, judged=10, unwelcome=0.05)
    pa._ADAPT_CACHE["ts"] = 0.0
    assert pa._learned_threshold_delta() == -0.07


def test_effective_threshold_clamped_high(pa, load, monkeypatch):
    _flag(monkeypatch, load, True)
    _rate(monkeypatch, load, judged=10, unwelcome=0.6)   # +0.15
    pa._ADAPT_CACHE["ts"] = 0.0
    orig = pa.CONFIDENCE_THRESHOLD
    try:
        pa.CONFIDENCE_THRESHOLD = 0.9                     # 0.9 + 0.15 → clamp 0.95
        assert pa._effective_threshold() == 0.95
    finally:
        pa.CONFIDENCE_THRESHOLD = orig


def test_effective_threshold_clamped_floor(pa, load, monkeypatch):
    _flag(monkeypatch, load, True)
    _rate(monkeypatch, load, judged=10, unwelcome=0.05)  # -0.07
    pa._ADAPT_CACHE["ts"] = 0.0
    orig = pa.CONFIDENCE_THRESHOLD
    try:
        pa.CONFIDENCE_THRESHOLD = 0.32                    # 0.32 - 0.07 → clamp 0.30
        assert pa._effective_threshold() == 0.3
    finally:
        pa.CONFIDENCE_THRESHOLD = orig
