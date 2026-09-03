"""Config schema migrations (v1 → CURRENT).

Migration bugs bite on upgrade — the worst time — so this pins the full chain,
partial starts, idempotency, legacy promotions, value preservation, and graceful
failure. Previously the migration runner had no tests at all.
"""
import pytest


@pytest.fixture
def m(load):
    return load("migrations")


def test_full_chain_v1_to_current(m):
    data, options, ver = m.migrate_config({}, {}, current_version=1)
    assert ver == m.CURRENT_SCHEMA_VERSION == 7
    assert "tts_premium_engine" in options                         # v2
    assert options["tts_premium_contexts"] == [
        "briefing", "camera", "doorbell", "recognition"]
    assert options["directive_preset"] == "guardian_steward"      # v3
    assert options["llm_provider"] == "groq"                       # v4
    assert options["room_routing"] is True                        # v5
    assert options["voice_satellites"] == []
    assert options["observer_enabled"] is False                   # v6
    assert options["classifier_model"] == "gemini-2.5-flash-lite"
    assert options["bedroom_areas"] == []                         # v7


def test_already_current_is_noop(m):
    data, options, ver = m.migrate_config({"x": 1}, {"y": 2}, current_version=7)
    assert ver == 7 and data == {"x": 1} and options == {"y": 2}


def test_future_version_is_noop(m):
    _, options, ver = m.migrate_config({}, {"keep": 1}, current_version=99)
    assert ver == 99 and options == {"keep": 1}


def test_partial_start_v5_skips_earlier_steps(m):
    _, options, ver = m.migrate_config({}, {}, current_version=5)
    assert ver == 7
    assert "observer_enabled" in options        # 5→6 ran
    assert "bedroom_areas" in options           # 6→7 ran
    assert "tts_premium_engine" not in options  # 1→2 did NOT (started at v5)
    assert "llm_provider" not in options        # 3→4 did NOT


def test_legacy_cast_speakers_promoted_to_broadcast_group(m):
    # v4 install with cast_speakers → v5 promotes to broadcast_speakers → v7 to group
    _, options, ver = m.migrate_config(
        {}, {"cast_speakers": ["media_player.kitchen", "media_player.den"]},
        current_version=4)
    assert ver == 7
    assert options["broadcast_speakers"] == ["media_player.kitchen", "media_player.den"]
    assert options["broadcast_group"] == "media_player.kitchen"


def test_existing_values_are_preserved(m):
    # setdefault must not clobber a value the user already chose
    _, options, _ = m.migrate_config({}, {"llm_provider": "openai"}, current_version=3)
    assert options["llm_provider"] == "openai"


def test_current_schema_version_matches_migration_chain(m):
    # the last migration's target must equal CURRENT_SCHEMA_VERSION, or an
    # install one-below-current would silently never reach current
    assert max(src + 1 for src, _ in m.MIGRATIONS) == m.CURRENT_SCHEMA_VERSION


def test_step_failure_stops_at_last_good_version(m, monkeypatch):
    def boom(data, options):
        raise RuntimeError("bad step")
    monkeypatch.setattr(m, "MIGRATIONS",
                        [(1, m.migrate_1_to_2), (2, m.migrate_2_to_3), (3, boom)])
    _, options, ver = m.migrate_config({}, {}, current_version=1)
    assert ver == 3                            # 1→2, 2→3 ok; 3→boom failed → stuck
    assert "directive_preset" in options       # 2→3 applied before the failure
