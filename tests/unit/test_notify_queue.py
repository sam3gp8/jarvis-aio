"""Deferred "notify X next time you see or hear them" (#540, increment 2).

Hold a message for a household member and deliver it the next time JARVIS detects
that person (arrival / camera recognition). The store is persistent and the text
shaping is pure; the delivery path announces on the speakers (held message for the
person) or pushes to the owner (a sighting alert), both stubbed here.
"""
import sqlite3
import sys
import types

import pytest


@pytest.fixture
def nq(load):
    return load("notify_queue")


def _stub_sibling(monkeypatch, name: str, **attrs):
    """Replace ``jc.<name>`` both in sys.modules AND as an attribute on the
    synthetic ``jc`` package — notify_queue reaches siblings via ``from . import
    <name>``, which binds the PACKAGE ATTRIBUTE, so stubbing only sys.modules leaks
    the real module under full-suite ordering. monkeypatch restores both."""
    mod = types.ModuleType(f"jc.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"jc.{name}", mod)
    jc_pkg = sys.modules.get("jc")
    if jc_pkg is not None:
        monkeypatch.setattr(jc_pkg, name, mod, raising=False)
    return mod


def _norm(s):
    return " ".join(str(s or "").strip().lower().replace("_", " ").split())


@pytest.fixture(autouse=True)
def _iso(nq, monkeypatch):
    """Deterministic normalization + a clean queue around every test."""
    _stub_sibling(monkeypatch, "identity", normalize=_norm)
    nq.cancel_for(None)          # clear any pending in the shared test DB
    nq.reset_for_tests()
    yield
    nq.cancel_for(None)
    nq.reset_for_tests()


# ── is_owner_notify (pure) ───────────────────────────────────────────────────
def test_owner_words(nq):
    for w in ("me", "myself", "I", "owner", "the owner"):
        assert nq.is_owner_notify(w) is True
    for w in ("Sam", "dad", "", None):
        assert nq.is_owner_notify(w) is False


# ── compose_delivery_line (pure) ─────────────────────────────────────────────
def test_compose_held_to_target_no_sender(nq):
    assert nq.compose_delivery_line("Dad", "your tool order is done") \
        == "Dad — a message for you: your tool order is done."


def test_compose_held_to_target_with_sender(nq):
    assert nq.compose_delivery_line("Dad", "dinner's ready", from_person="Sam") \
        == "Dad — Sam asked me to pass on: dinner's ready."


def test_compose_held_no_message_is_a_catch(nq):
    out = nq.compose_delivery_line("Dad", "")
    assert out == "Dad — I was asked to catch you."


def test_compose_owner_alert_home_with_note(nq):
    out = nq.compose_delivery_line("Mum", "ask her about the car",
                                   notify_owner=True, trigger="home")
    assert out == ("You asked to be told when Mum is around — Mum has just "
                   "arrived home. Note: ask her about the car.")


def test_compose_owner_alert_no_message(nq):
    out = nq.compose_delivery_line("Mum", "", notify_owner=True, trigger="camera")
    assert out == ("You asked to be told when Mum is around — Mum has just been "
                   "seen on camera.")


def test_compose_resident_alert(nq):
    out = nq.compose_delivery_line("Mum", "", notify_display="Sam", trigger="home")
    assert out == "Sam — Mum has just arrived home. You wanted to know."


def test_compose_notify_equals_target_is_held(nq):
    # naming the target as the notify is still a held message, not a self-alert
    out = nq.compose_delivery_line("Dad", "hi", notify_display="dad")
    assert out == "Dad — a message for you: hi."


@pytest.mark.parametrize("trigger,phrase", [
    ("home", "has just arrived home"),
    ("camera", "has just been seen on camera"),
    ("voice", "was just heard"),
    ("", "is here now"),
])
def test_trigger_phrases(nq, trigger, phrase):
    out = nq.compose_delivery_line("Mum", "", notify_owner=True, trigger=trigger)
    assert phrase in out


def test_compose_ends_with_punctuation(nq):
    assert nq.compose_delivery_line("Dad", "come eat").endswith(".")


# ── store ────────────────────────────────────────────────────────────────────
def test_enqueue_and_pending_for(nq):
    rid = nq.enqueue("Dad", "your order is in")
    assert isinstance(rid, int)
    rows = nq.pending_for("Dad")
    assert len(rows) == 1
    r = rows[0]
    assert r["target_person"] == "dad"
    assert r["target_display"] == "Dad"
    assert r["message"] == "your order is in"
    assert r["notify_owner"] == 0
    assert r["notify_display"] is None
    assert r["status"] == "pending"


def test_enqueue_owner_notify(nq):
    nq.enqueue("Mum", "tell me", notify_display="me")
    r = nq.pending_for("Mum")[0]
    assert r["notify_owner"] == 1
    assert r["notify_display"] is None            # owner, not a named resident


def test_enqueue_resident_notify(nq):
    nq.enqueue("Mum", "x", notify_display="Sam")
    r = nq.pending_for("Mum")[0]
    assert r["notify_owner"] == 0
    assert r["notify_display"] == "Sam"


def test_pending_for_normalizes_lookup(nq):
    nq.enqueue("Dad", "hi")
    assert len(nq.pending_for("dad")) == 1
    assert len(nq.pending_for("  DAD  ")) == 1
    assert nq.pending_for("mum") == []


def test_oldest_first_order(nq):
    nq.enqueue("Dad", "first")
    nq.enqueue("Dad", "second")
    msgs = [r["message"] for r in nq.pending_for("Dad")]
    assert msgs == ["first", "second"]


def test_mark_delivered_removes_from_pending(nq):
    rid = nq.enqueue("Dad", "hi")
    assert nq.has_pending() is True
    assert nq.mark_delivered(rid, "home") is True
    assert nq.pending_for("Dad") == []
    assert nq.has_pending() is False


def test_cancel_for_target(nq):
    nq.enqueue("Dad", "a")
    nq.enqueue("Mum", "b")
    assert nq.cancel_for("Dad") == 1
    assert nq.pending_for("Dad") == []
    assert len(nq.pending_for("Mum")) == 1


def test_cancel_all(nq):
    nq.enqueue("Dad", "a")
    nq.enqueue("Mum", "b")
    assert nq.cancel_for(None) == 2
    assert nq.list_pending() == []
    assert nq.has_pending() is False


def test_message_capped(nq):
    nq.enqueue("Dad", "x" * 1000)
    assert len(nq.pending_for("Dad")[0]["message"]) == 300


def test_bad_input_returns_none(nq):
    assert nq.enqueue("", "msg") is None
    assert nq.enqueue("Dad", "   ") is None
    assert nq.list_pending() == []


def test_ttl_zero_means_no_expiry(nq):
    nq.enqueue("Dad", "hi", ttl_days=0)
    assert nq.pending_for("Dad")[0]["expires_ts"] is None


def test_expiry_excludes_expired(nq):
    rid = nq.enqueue("Dad", "hi")
    # Age it into the past directly in the store.
    with sqlite3.connect(nq.DB_PATH) as conn:
        conn.execute("UPDATE notify_queue SET expires_ts = 1 WHERE id = ?", (rid,))
        conn.commit()
    assert nq.pending_for("Dad") == []
    assert nq.list_pending() == []


def test_per_target_cap(nq):
    for i in range(nq._MAX_PER_TARGET):
        assert nq.enqueue("Dad", f"m{i}") is not None
    assert nq.enqueue("Dad", "one too many") is None      # capped
    assert nq.enqueue("Mum", "other target ok") is not None  # a different target is fine


def test_has_pending_guard(nq):
    nq.reset_for_tests()
    assert nq.has_pending() is False
    nq.enqueue("Dad", "hi")
    assert nq.has_pending() is True
    nq.cancel_for(None)
    assert nq.has_pending() is False


# ── delivery (async, siblings stubbed) ───────────────────────────────────────
@pytest.fixture
def announce(monkeypatch):
    cap = {}

    async def _announce(hass, text, tts_entity, speakers, **k):
        cap["text"] = text
        cap["speakers"] = list(speakers)

    _stub_sibling(monkeypatch, "audio_routing",
                  broadcast_target=lambda hass, **k: ["media_player.kitchen",
                                                      "assist_satellite.office"])
    _stub_sibling(monkeypatch, "tts_helper", async_announce=_announce)
    _stub_sibling(monkeypatch, "websocket", jarvis_log=lambda *a, **k: None)
    return cap


async def test_async_deliver_held_announces(nq, fake_hass, announce):
    nq.enqueue("Dad", "your order is in")
    n = await nq.async_deliver_for(fake_hass, "Dad", "home")
    assert n == 1
    assert announce["text"] == "Dad — a message for you: your order is in."
    assert announce["speakers"] == ["media_player.kitchen"]   # satellite dropped
    assert nq.pending_for("Dad") == []                        # consumed


async def test_async_deliver_owner_pushes(nq, fake_hass, monkeypatch):
    _stub_sibling(monkeypatch, "jarvis_config",
                  get=lambda k, d=None: "notify.mobile_app_sam"
                  if k == "notify_service" else d)
    _stub_sibling(monkeypatch, "websocket", jarvis_log=lambda *a, **k: None)
    nq.enqueue("Mum", "ask about the car", notify_display="me")
    n = await nq.async_deliver_for(fake_hass, "Mum", "home")
    assert n == 1
    assert len(fake_hass.service_calls) == 1
    domain, service, data = fake_hass.service_calls[0]
    assert (domain, service) == ("notify", "mobile_app_sam")
    assert "Mum has just arrived home" in data["message"]
    assert nq.pending_for("Mum") == []


async def test_async_maybe_deliver_short_circuits(nq, fake_hass, announce):
    nq.enqueue("Dad", "hi")
    nq.reset_for_tests()                 # pretend the flag says "nothing pending"
    n = await nq.async_maybe_deliver(fake_hass, "Dad", "home")
    assert n == 0
    assert "text" not in announce        # never touched the DB or the speakers


async def test_async_deliver_failure_keeps_pending(nq, fake_hass, monkeypatch):
    _stub_sibling(monkeypatch, "audio_routing", broadcast_target=lambda hass, **k: [])
    _stub_sibling(monkeypatch, "websocket", jarvis_log=lambda *a, **k: None)
    nq.enqueue("Dad", "hi")
    n = await nq.async_deliver_for(fake_hass, "Dad", "home")
    assert n == 0
    assert len(nq.pending_for("Dad")) == 1       # not lost — re-tries on next sighting


async def test_async_deliver_nothing_queued(nq, fake_hass, announce):
    assert await nq.async_deliver_for(fake_hass, "Nobody", "home") == 0


async def test_schedule_delivery_runs_when_pending(nq, fake_hass, announce):
    nq.enqueue("Dad", "hi")
    nq.schedule_delivery(fake_hass, "Dad", "camera")
    await fake_hass.drain()                       # run the scheduled coroutine
    assert announce["text"] == "Dad — a message for you: hi."
    assert nq.pending_for("Dad") == []


def test_schedule_delivery_noop_when_empty(nq, fake_hass):
    nq.reset_for_tests()
    nq.schedule_delivery(fake_hass, "Dad", "camera")
    assert fake_hass._tasks == []                 # nothing scheduled
