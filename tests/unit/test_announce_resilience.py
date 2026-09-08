"""Broadcast announcements must not be all-or-nothing (v6.78.2).

tts.speak is issued as ONE call carrying the whole speaker list, so a single
bad target (an off TV, a stale Cast entity) failed the entire broadcast — the
briefing died silently while a room-routed reply to one speaker worked fine.
Two defences: unavailable targets are filtered out of the broadcast list, and a
failed batch is retried per speaker so the reachable ones still hear it."""
import pytest


class _State:
    def __init__(self, entity_id, state):
        self.entity_id, self.state = entity_id, state
        self.attributes = {}


class _Hass:
    def __init__(self, players, fail_on=None, fail_batch=False):
        self._players = players
        self._fail_on = set(fail_on or [])
        self._fail_batch = fail_batch
        self.calls = []
        self.services = self
    def async_all(self, domain=None):
        return list(self._players.values())
    class _S: pass
    @property
    def states(self):
        s = _Hass._S()
        s.get = lambda eid: self._players.get(eid)
        s.async_all = lambda domain=None: list(self._players.values())
        return s
    async def async_call(self, domain, service, data, target=None, blocking=False):
        targets = data.get("media_player_entity_id") or []
        self.calls.append(list(targets))
        if self._fail_batch and len(targets) > 1:
            raise RuntimeError("batch rejected")
        for t in targets:
            if t in self._fail_on:
                raise RuntimeError(f"{t} unavailable")


@pytest.fixture
def tts(load):
    return load("tts_helper")


@pytest.fixture
def routing(load, monkeypatch):
    # audio_routing imports area_registry at module level; the synthetic HA
    # stub doesn't provide it, so supply a minimal one for the load.
    import sys, types
    helpers = sys.modules.get("homeassistant.helpers") or types.ModuleType("homeassistant.helpers")
    ar = types.ModuleType("homeassistant.helpers.area_registry")
    ar.async_get = lambda hass: types.SimpleNamespace(
        async_list_areas=lambda: [], async_get_area=lambda i: None)
    er = types.ModuleType("homeassistant.helpers.entity_registry")
    er.async_get = lambda hass: types.SimpleNamespace(
        entities={}, async_get=lambda e: None)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.area_registry", ar)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.entity_registry", er)
    monkeypatch.setattr(helpers, "area_registry", ar, raising=False)
    monkeypatch.setattr(helpers, "entity_registry", er, raising=False)
    return load("audio_routing")


# ── broadcast target: silent-until-configured (v7.83.0) ─────────────────────

def test_broadcast_silent_until_configured(routing, monkeypatch):
    # Fresh install: nothing configured → [] (never blast every device / TV).
    players = {
        "media_player.kitchen": _State("media_player.kitchen", "idle"),
        "media_player.tv": _State("media_player.tv", "on"),
    }
    hass = _Hass(players)
    monkeypatch.setattr(routing, "_entities_by_domain", lambda h, d: list(players))
    assert routing.broadcast_target(hass) == []


def test_broadcast_uses_broadcast_group(routing):
    hass = _Hass({"media_player.home_group": _State("media_player.home_group", "idle")})
    assert routing.broadcast_target(
        hass, broadcast_group="media_player.home_group") == ["media_player.home_group"]


def test_broadcast_group_missing_falls_silent(routing):
    assert routing.broadcast_target(
        _Hass({}), broadcast_group="media_player.gone") == []


def test_broadcast_uses_announcement_speakers(routing):
    hass = _Hass({
        "media_player.kitchen": _State("media_player.kitchen", "idle"),
        "media_player.den": _State("media_player.den", "idle"),
    })
    out = routing.broadcast_target(
        hass, announcement_speakers=["media_player.kitchen", "media_player.den"])
    assert out == ["media_player.kitchen", "media_player.den"]


def test_broadcast_filters_missing_announcement_speakers(routing):
    hass = _Hass({"media_player.kitchen": _State("media_player.kitchen", "idle")})
    out = routing.broadcast_target(
        hass, announcement_speakers=["media_player.kitchen", "media_player.gone"])
    assert out == ["media_player.kitchen"]


def test_broadcast_announcement_speakers_accepts_json_string(routing):
    # panel/config may store the list as a JSON string
    hass = _Hass({"media_player.kitchen": _State("media_player.kitchen", "idle")})
    out = routing.broadcast_target(
        hass, announcement_speakers='["media_player.kitchen"]')
    assert out == ["media_player.kitchen"]


def test_observer_critical_silent_until_configured(routing, monkeypatch):
    # A critical alert broadcasts — but with nothing configured it must degrade
    # to notify_only, never fall back to every speaker in the house.
    players = {
        "media_player.kitchen": _State("media_player.kitchen", "idle"),
        "media_player.tv": _State("media_player.tv", "on"),
    }
    hass = _Hass(players)
    monkeypatch.setattr(routing, "_entities_by_domain", lambda h, d: list(players))
    targets, mode = routing.observer_speak_target(hass, urgency="critical")
    assert targets == [] and mode == "notify_only"


def test_observer_critical_uses_announcement_speakers(routing):
    hass = _Hass({"media_player.kitchen": _State("media_player.kitchen", "idle")})
    targets, mode = routing.observer_speak_target(
        hass, urgency="critical", announcement_speakers=["media_player.kitchen"])
    assert targets == ["media_player.kitchen"] and mode == "broadcast"


def test_speakers_in_area_excludes_tv_and_movie_player(routing, monkeypatch):
    # A living room with a real speaker, a TV (device_class tv), and a projector
    # designated as the movie player. TTS routing must return only the speaker.
    spk = _State("media_player.living_room_speaker", "idle")
    tv = _State("media_player.samsung_tv", "on"); tv.attributes = {"device_class": "tv"}
    proj = _State("media_player.projector", "idle")   # movie_media_player, no dc
    players = {s.entity_id: s for s in (spk, tv, proj)}
    hass = _Hass(players)
    monkeypatch.setattr(routing, "_entities_by_domain", lambda h, d: list(players))
    monkeypatch.setattr(routing, "entity_area", lambda h, e: "living_room")
    import sys, types
    pkg = routing.__name__.rsplit(".", 1)[0]
    jc = types.SimpleNamespace(
        get=lambda k, d=None: "media_player.projector" if k == "movie_media_player" else d)
    monkeypatch.setitem(sys.modules, f"{pkg}.jarvis_config", jc)
    assert routing.speakers_in_area(hass, "living_room") == ["media_player.living_room_speaker"]


# ── per-speaker fallback ─────────────────────────────────────────────────────

async def test_batch_success_makes_one_call(tts):
    hass = _Hass({})
    await tts.async_announce(hass, "hello", "tts.piper",
                             ["media_player.a", "media_player.b"])
    assert len(hass.calls) == 1                      # single batch call
    assert hass.calls[0] == ["media_player.a", "media_player.b"]


async def test_batch_failure_retries_per_speaker(tts):
    # the batch fails; each speaker is then tried individually
    hass = _Hass({}, fail_batch=True)
    await tts.async_announce(hass, "hello", "tts.piper",
                             ["media_player.a", "media_player.b"])
    assert hass.calls[0] == ["media_player.a", "media_player.b"]   # batch attempt
    assert ["media_player.a"] in hass.calls
    assert ["media_player.b"] in hass.calls


async def test_one_bad_speaker_does_not_silence_the_rest(tts):
    # the real bug: one dead target used to kill the whole broadcast
    hass = _Hass({}, fail_on={"media_player.dead"}, fail_batch=True)
    await tts.async_announce(hass, "briefing", "tts.piper",
                             ["media_player.dead", "media_player.good"])
    assert ["media_player.good"] in hass.calls, "the working speaker must still play"


async def test_no_speakers_is_a_noop(tts):
    hass = _Hass({})
    await tts.async_announce(hass, "hello", "tts.piper", [])
    assert hass.calls == []


async def test_no_tts_entity_is_a_noop(tts):
    hass = _Hass({})
    await tts.async_announce(hass, "hello", None, ["media_player.a"])
    assert hass.calls == []


def test_drop_display_targets_filters_tv_and_movie(routing, load):
    # The output choke point strips TVs + the movie player from ANY target list,
    # regardless of how they were resolved — the safety net for TV takeovers.
    # movie_media_player is read from runtime_config (hass.data), not jarvis_config.
    const = load("const")
    spk = _State("media_player.living_room_speaker", "idle")
    tv = _State("media_player.samsung_tv", "on"); tv.attributes = {"device_class": "tv"}
    movie = _State("media_player.projector", "idle")
    players = {s.entity_id: s for s in (spk, tv, movie)}
    hass = _Hass(players)
    hass.data = {const.DOMAIN: {"e1": {"runtime_config": {
        "movie_media_player": "media_player.projector"}}}}
    kept = routing.drop_display_targets(
        hass,
        ["media_player.living_room_speaker", "media_player.samsung_tv", "media_player.projector"],
        "unit-test")
    assert kept == ["media_player.living_room_speaker"]


def test_drop_display_targets_keeps_plain_speakers(routing, load):
    const = load("const")
    hass = _Hass({"media_player.kitchen": _State("media_player.kitchen", "idle"),
                  "media_player.den": _State("media_player.den", "idle")})
    hass.data = {const.DOMAIN: {}}
    kept = routing.drop_display_targets(
        hass, ["media_player.kitchen", "media_player.den"], "unit-test")
    assert kept == ["media_player.kitchen", "media_player.den"]
