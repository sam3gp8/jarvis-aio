"""Kernel Phase O — agency orchestration (hierarchical delegation), pure primitive.

Pins the no-escalation invariant (a child's capabilities only ever narrow the
parent's), the delegation-depth bound, and the bounded/irreversible lifecycle —
the governance the owner-gated enforce rung will depend on."""
import pytest


@pytest.fixture
def agency(load):
    return load("kernel.agency")


def _root(agency, caps=("*",)):
    return agency.root("jarvis", caps)


# ── spawning narrows, never escalates ─────────────────────────────────────────

def test_root_is_active_and_holds_its_caps(agency):
    r = agency.root("jarvis", ("*",))
    assert r.state == agency.ACTIVE and r.depth == 0 and r.parent is None
    assert r.permits("light.turn_on") is True   # '*' grants everything


def test_spawn_narrows_to_requested_subset(agency):
    r = _root(agency)
    child = agency.spawn(r, "friday", ["light.turn_on", "media_player.play"],
                         objective="movie night")
    assert child.state == agency.PENDING
    assert child.depth == 1 and child.parent == "jarvis"
    assert child.permits("light.turn_on") is True
    assert child.permits("lock.unlock") is False   # never delegated


def test_child_cannot_exceed_parent(agency):
    # a mid-chain parent that only holds lights may not delegate a lock capability.
    r = _root(agency)
    mid = agency.spawn(r, "friday", ["light.turn_on"])
    mid = agency.transition(mid, agency.ACTIVE)
    grandchild = agency.spawn(mid, "sub", ["light.turn_on", "lock.unlock"])
    # lock.unlock is intersected away — the grandchild never holds it.
    assert grandchild.permits("light.turn_on") is True
    assert grandchild.permits("lock.unlock") is False
    assert grandchild.capabilities == frozenset({"light.turn_on"})


# ── can_spawn verdicts ────────────────────────────────────────────────────────

def test_can_spawn_reports_dropped_capabilities(agency):
    mid = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    mid = agency.transition(mid, agency.ACTIVE)
    chk = agency.can_spawn(mid, ["light.turn_on", "lock.unlock"])
    assert chk.ok is True and bool(chk) is True
    assert chk.granted == frozenset({"light.turn_on"})
    assert chk.dropped == frozenset({"lock.unlock"})


def test_can_spawn_refuses_when_nothing_granted(agency):
    mid = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    mid = agency.transition(mid, agency.ACTIVE)
    chk = agency.can_spawn(mid, ["lock.unlock"])
    assert not chk and "no requested capability" in chk.reason


def test_can_spawn_enforces_depth_bound(agency):
    # walk the chain to the max depth, each a live parent.
    node = _root(agency)
    for _ in range(agency.DEFAULT_MAX_DEPTH):
        node = agency.transition(agency.spawn(node, "d", ["*"]), agency.ACTIVE)
    chk = agency.can_spawn(node, ["*"])   # would be depth MAX+1
    assert not chk and "depth" in chk.reason


def test_can_spawn_refuses_terminal_parent(agency):
    child = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    settled = agency.transition(agency.transition(child, agency.ACTIVE), agency.SETTLED)
    chk = agency.can_spawn(settled, ["light.turn_on"])
    assert not chk and "settled" in chk.reason


# ── lifecycle is bounded and irreversible ─────────────────────────────────────

def test_legal_lifecycle_path(agency):
    child = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    active = agency.transition(child, agency.ACTIVE)
    settled = agency.transition(active, agency.SETTLED)
    assert settled.state == agency.SETTLED and settled.terminal is True
    # identity + token preserved across transitions
    assert settled.agency_id == child.agency_id
    assert settled.capabilities == child.capabilities


def test_cannot_revive_a_settled_agency(agency):
    child = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    settled = agency.transition(agency.transition(child, agency.ACTIVE), agency.SETTLED)
    with pytest.raises(ValueError):
        agency.transition(settled, agency.ACTIVE)


def test_cannot_skip_from_pending_to_settled(agency):
    child = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    assert agency.can_transition(agency.PENDING, agency.SETTLED) is False
    with pytest.raises(ValueError):
        agency.transition(child, agency.SETTLED)


def test_pending_may_fail_directly(agency):
    child = agency.spawn(_root(agency), "friday", ["light.turn_on"])
    failed = agency.transition(child, agency.FAILED)
    assert failed.state == agency.FAILED and failed.terminal is True
