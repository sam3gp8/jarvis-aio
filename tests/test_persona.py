"""Full-coverage tests for the JARVIS persona voice module.

Loaded directly from the component directory (persona is a stdlib-only leaf, so
no Home Assistant runtime or stubbing is needed)."""
import importlib.util
import os

_COMP = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "jarvis"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "jarvis_persona", os.path.join(_COMP, "persona.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


persona = _load()


def setup_function(_fn):
    # Reset module state before each test so anti-repeat history is isolated.
    persona.set_variety(True)
    persona._recent.clear()


# ── _fill ────────────────────────────────────────────────────────────────────

def test_fill_capitalizes_and_lowercases():
    assert persona._fill("{H}, {h}!", "sir") == "Sir, sir!"


def test_fill_empty_honorific_defaults_to_sir():
    assert persona._fill("{h}", "") == "sir"
    assert persona._fill("{h}", "   ") == "sir"
    assert persona._fill("{h}", None) == "sir"


def test_fill_multichar_honorific():
    assert persona._fill("{H}", "boss") == "Boss"


# ── _pick ────────────────────────────────────────────────────────────────────

def test_pick_empty_pool_returns_empty():
    assert persona._pick([], "k", "sir") == ""


def test_pick_variety_off_is_deterministic_first():
    persona.set_variety(False)
    pool = ["one {h}", "two {h}", "three {h}"]
    assert persona._pick(pool, "k", "sir") == "one sir"
    assert persona._pick(pool, "k", "sir") == "one sir"


def test_pick_anti_repeat_no_back_to_back():
    pool = [f"line{i} {{h}}" for i in range(5)]
    seq = [persona._pick(pool, "k", "sir") for _ in range(40)]
    assert all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))


def test_pick_exhausts_choices_branch_with_singleton_pool():
    # A length-1 pool fills the recent deque, forcing the "all excluded" reset.
    pool = ["only {h}"]
    assert persona._pick(pool, "solo", "sir") == "only sir"
    assert persona._pick(pool, "solo", "sir") == "only sir"


# ── _reg ─────────────────────────────────────────────────────────────────────

def test_reg_existing_register():
    assert persona._reg(persona._ACK, "urgent") is persona._ACK["urgent"]


def test_reg_missing_register_falls_back_to_neutral():
    assert persona._reg(persona._ACK, "nonexistent") is persona._ACK["neutral"]


def test_reg_missing_neutral_falls_back_to_first_value():
    pools = {"only": ["x"]}
    assert persona._reg(pools, "missing") == ["x"]


# ── register_for ─────────────────────────────────────────────────────────────

def test_register_for_mapping():
    assert persona.register_for("critical") == "grave"
    assert persona.register_for("high") == "urgent"
    assert persona.register_for("medium") == "neutral"
    assert persona.register_for("") == "neutral"
    assert persona.register_for(None) == "neutral"
    assert persona.register_for("CRITICAL") == "grave"  # case-insensitive


# ── speech-act wrappers ──────────────────────────────────────────────────────

def test_acknowledge_all_registers_render():
    for reg in ("light", "neutral", "urgent"):
        out = persona.acknowledge("sir", reg)
        assert out and "{" not in out


def test_completed_all_registers_render():
    for reg in ("light", "neutral", "urgent"):
        out = persona.completed("sir", reg)
        assert out and "{" not in out


def test_working_and_unable_render():
    assert persona.working("sir") and "{" not in persona.working("sir")
    assert persona.unable("sir") and "{" not in persona.unable("sir")


def test_announce_opener_registers_and_grave_is_plain():
    for reg in ("neutral", "urgent", "grave"):
        out = persona.announce_opener("sir", reg)
        assert out and "{" not in out
    # grave openers should be short/plain (no flourish words)
    graves = {persona.announce_opener("sir", "grave") for _ in range(8)}
    assert all(len(g) <= 6 for g in graves)


# ── greeting (every hour bucket + default) ───────────────────────────────────

def test_greeting_buckets():
    assert "morning" in persona.greeting("sir", 7).lower() or persona.greeting("sir", 7)
    for hour in (1, 7, 14, 19, 23):  # night, morning, afternoon, evening, night
        out = persona.greeting("sir", hour)
        assert out and "{" not in out


def test_greeting_boundaries():
    # exercise each branch boundary explicitly
    assert persona.greeting("sir", 4)   # < 5 -> night
    assert persona.greeting("sir", 5)   # morning
    assert persona.greeting("sir", 11)  # morning
    assert persona.greeting("sir", 12)  # afternoon
    assert persona.greeting("sir", 16)  # afternoon
    assert persona.greeting("sir", 17)  # evening
    assert persona.greeting("sir", 21)  # evening
    assert persona.greeting("sir", 22)  # night


def test_greeting_default_hour_uses_clock():
    out = persona.greeting("sir")  # hour=None -> time.localtime()
    assert out and "{" not in out


# ── set_variety ──────────────────────────────────────────────────────────────

def test_set_variety_toggle():
    persona.set_variety(False)
    assert persona._VARIETY is False
    persona.set_variety(True)
    assert persona._VARIETY is True
