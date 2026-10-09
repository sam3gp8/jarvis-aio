"""Provenance shadow on the recognition read boundary (#237, Epistemic Fabric).

``recognition.who_is_where()`` returns ``{camera_entity: name}`` for recent
recognitions and, in shadow, also packages each sighting it returns as a
``kernel.provenance.Provenance`` (value=name / source=camera / confidence /
observed-at / cache-window expiry) and logs the authoritative fresh record via
``select_authoritative``. The emission is observe-only: the returned mapping is
identical whether the shadow is on or off, and the log-only path never raises.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def recog(load, monkeypatch):
    """recognition module with an isolated, empty recognition cache."""
    mod = load("recognition")
    mod._RECOGNITION_CACHE.clear()
    yield mod
    mod._RECOGNITION_CACHE.clear()


def _cache(mod, camera_entity, name, confidence, age_seconds):
    mod._RECOGNITION_CACHE[camera_entity] = {
        "name": name,
        "confidence": confidence,
        "ts": _now() - timedelta(seconds=age_seconds),
        "unknown_count": 0,
    }


def test_who_is_where_identical_with_shadow_on_or_off(recog, fake_hass):
    """The core shadow guarantee: the identity read is byte-for-byte the same
    whether provenance emission is enabled or not."""
    _cache(recog, "camera.front_door", "Sam", 98.7, age_seconds=30)
    _cache(recog, "camera.garage", "Alex", 72.0, age_seconds=120)

    recog.PROVENANCE_SHADOW = True
    with_shadow = recog.who_is_where(fake_hass)
    recog.PROVENANCE_SHADOW = False
    try:
        without_shadow = recog.who_is_where(fake_hass)
    finally:
        recog.PROVENANCE_SHADOW = True

    assert with_shadow == without_shadow
    assert with_shadow == {"camera.front_door": "Sam", "camera.garage": "Alex"}


def test_who_is_where_emits_provenance_for_returned_sightings(recog, fake_hass, caplog):
    """Each recognition the read returns produces a provenance record, and the
    highest-confidence fresh one is logged as authoritative."""
    _cache(recog, "camera.front_door", "Sam", 98.7, age_seconds=30)
    _cache(recog, "camera.garage", "Alex", 72.0, age_seconds=120)
    with caplog.at_level(logging.DEBUG):
        out = recog.who_is_where(fake_hass)
    assert out == {"camera.front_door": "Sam", "camera.garage": "Alex"}
    msgs = [r.message for r in caplog.records if "provenance(shadow)" in r.message]
    assert msgs, "expected a provenance(shadow) log line"
    # Two recognitions passed the filter; the stronger, fresher Sam wins.
    assert "2 recognition record(s)" in msgs[-1]
    assert "value='Sam'" in msgs[-1] and "src=camera.front_door" in msgs[-1]


def test_sub_threshold_and_stale_sightings_are_not_in_provenance(recog, fake_hass, caplog):
    """who_is_where() filters out stale and low-confidence entries, so the
    provenance view only covers what the read actually surfaces."""
    _cache(recog, "camera.front_door", "Sam", 98.7, age_seconds=30)
    _cache(recog, "camera.hall", "Low", 40.0, age_seconds=10)          # below threshold
    _cache(recog, "camera.old", "Stale", 90.0, age_seconds=60 * 60 * 3)  # older than 2h
    with caplog.at_level(logging.DEBUG):
        out = recog.who_is_where(fake_hass)
    assert out == {"camera.front_door": "Sam"}
    msgs = [r.message for r in caplog.records if "provenance(shadow)" in r.message]
    assert msgs and "1 recognition record(s)" in msgs[-1]


def test_emit_provenance_shadow_is_defensive(recog):
    """The log-only shadow path never raises on empty or malformed input."""
    recog._emit_provenance_shadow([])
    recog._emit_provenance_shadow(None)
    # A row with a bad timestamp must fall back to 'now', not raise.
    recog._emit_provenance_shadow([("camera.x", "Sam", 90.0, "not-a-datetime")])


def test_who_is_where_empty_cache_emits_nothing(recog, fake_hass, caplog):
    with caplog.at_level(logging.DEBUG):
        assert recog.who_is_where(fake_hass) == {}
    assert not [r for r in caplog.records if "provenance(shadow)" in r.message]
