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


# ── broadcast target filtering ───────────────────────────────────────────────

def test_broadcast_skips_unavailable_players(routing, monkeypatch):
    players = {
        "media_player.kitchen": _State("media_player.kitchen", "idle"),
        "media_player.dead_tv": _State("media_player.dead_tv", "unavailable"),
        "media_player.unknown_one": _State("media_player.unknown_one", "unknown"),
    }
    hass = _Hass(players)
    monkeypatch.setattr(routing, "_entities_by_domain", lambda h, d: list(players))
    out = routing.broadcast_target(hass)
    assert "media_player.kitchen" in out
    assert "media_player.dead_tv" not in out
    assert "media_player.unknown_one" not in out


def test_broadcast_still_excludes_satellites(routing, monkeypatch):
    ids = ["media_player.kitchen", "assist_satellite.basement"]
    players = {i: _State(i, "idle") for i in ids}
    hass = _Hass(players)
    monkeypatch.setattr(routing, "_entities_by_domain", lambda h, d: ids)
    assert routing.broadcast_target(hass) == ["media_player.kitchen"]


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
