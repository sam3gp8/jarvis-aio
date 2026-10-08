"""Pure spatial & temporal primitive (kernel.space_time — roadmap Phase Q).

A SpatialGraph is built from a floor-plan adjacency map (residence_graph shape)
and offers pure, case-insensitive space queries; a TemporalFrame resolves an
hour+weekday to a coarse daypart. PURE: nothing live consumes it yet, so these
tests pin the structures and queries directly.
"""
import pytest


@pytest.fixture
def st(load):
    return load("kernel.space_time")


# ── temporal: dayparts ─────────────────────────────────────────────────────────

def test_daypart_of_boundaries(st):
    cases = {
        0: st.NIGHT, 4: st.NIGHT,
        5: st.MORNING, 10: st.MORNING,
        11: st.MIDDAY, 13: st.MIDDAY,
        14: st.AFTERNOON, 17: st.AFTERNOON,
        18: st.EVENING, 21: st.EVENING,
        22: st.LATE_NIGHT, 23: st.LATE_NIGHT,
    }
    for hour, label in cases.items():
        assert st.daypart_of(hour) == label, hour
    # dayparts are declared in chronological order
    assert st.DAYPARTS == (st.NIGHT, st.MORNING, st.MIDDAY, st.AFTERNOON,
                           st.EVENING, st.LATE_NIGHT)


def test_daypart_of_unknown_hour(st):
    for bad in (-1, 24, 99, None, "nope"):
        assert st.daypart_of(bad) == ""


def test_temporal_frame_at(st):
    f = st.TemporalFrame.at(19, weekday=5)  # Saturday evening
    assert f.hour == 19 and f.weekday == 5
    assert f.daypart == st.EVENING
    assert f.is_known and f.is_weekend and not f.is_daytime

    midday_weekday = st.TemporalFrame.at(12, weekday=2)  # Wednesday midday
    assert midday_weekday.daypart == st.MIDDAY
    assert midday_weekday.is_daytime and not midday_weekday.is_weekend


def test_temporal_frame_is_total_on_garbage(st):
    f = st.TemporalFrame.at("nope", weekday=9)
    assert not f.is_known and f.hour == -1 and f.daypart == ""
    assert f.weekday == -1 and not f.is_weekend and not f.is_daytime
    # weekday may be omitted entirely
    assert st.TemporalFrame.at(8).weekday == -1


# ── spatial: construction ──────────────────────────────────────────────────────

@pytest.fixture
def floor(st):
    # kitchen — hall — living — porch ; bed — hall  (a little linear house)
    return st.SpatialGraph.from_adjacency(
        {
            "Kitchen": ["Hall"],
            "Hall": ["Kitchen", "Living Room", "Bedroom"],
            "Living Room": ["Hall", "Porch"],
            "Porch": ["Living Room"],
            "Bedroom": ["Hall"],
        },
        floors={"Kitchen": "ground", "Porch": "ground"},
    )


def test_from_adjacency_builds_undirected_deduped(floor, st):
    assert [a.name for a in floor.areas] == [
        "Bedroom", "Hall", "Kitchen", "Living Room", "Porch"]
    # Kitchen⇄Hall listed from both sides → a single undirected edge
    assert len(floor.edges) == 4
    # floors seeded where provided, blank otherwise
    assert floor.area("kitchen").floor == "ground"
    assert floor.area("Hall").floor == ""


def test_from_adjacency_is_total_on_messy_input(st):
    g = st.SpatialGraph.from_adjacency({
        "": ["X"],              # blank area → skipped
        "A": ["", "A", "B"],    # blank + self-loop dropped, A⇄B kept
        "B": "A",               # scalar neighbor (not iterated char-by-char)
        "C": None,              # no neighbors → C is still a node
    })
    assert [a.name for a in g.areas] == ["A", "B", "C"]
    assert g.adjacent("A", "B") and not g.adjacent("A", "C")
    assert len(g.edges) == 1


def test_empty_graph(st):
    g = st.SpatialGraph.from_adjacency()
    assert g.is_empty() and g.area("anywhere") is None
    assert g.neighbors("x") == [] and g.hops_from("x") == {}
    assert g.distance("a", "b") is None and g.within("x", 3) == []


# ── spatial: queries ───────────────────────────────────────────────────────────

def test_neighbors_and_adjacent_case_insensitive(floor):
    assert floor.neighbors("hall") == ["Bedroom", "Kitchen", "Living Room"]
    assert floor.neighbors("Porch") == ["Living Room"]
    assert floor.adjacent("kitchen", "HALL")
    assert not floor.adjacent("Kitchen", "Porch")
    assert floor.neighbors("Nowhere") == []


def test_hops_and_distance(floor):
    hops = floor.hops_from("Kitchen")
    assert hops == {"Kitchen": 0, "Hall": 1, "Living Room": 2,
                    "Bedroom": 2, "Porch": 3}
    assert floor.distance("Kitchen", "Porch") == 3
    assert floor.distance("Kitchen", "kitchen") == 0   # same area
    assert floor.distance("Kitchen", "Nowhere") is None


def test_within_radius(floor):
    # one hop from the hall: its three direct neighbors
    assert floor.within("Hall", 1) == ["Bedroom", "Kitchen", "Living Room"]
    # two hops adds the porch (hall→living→porch); nearest-first ordering
    assert floor.within("Hall", 2) == [
        "Bedroom", "Kitchen", "Living Room", "Porch"]
    assert floor.within("Hall", 0) == []


def test_frozen_and_hashable(st):
    g = st.SpatialGraph.from_adjacency({"A": ["B"]})
    with pytest.raises(Exception):
        g.areas[0].name = "x"
    assert hash(g.area("A")) == hash(g.area("A"))
    f = st.TemporalFrame.at(9, weekday=1)
    assert hash(f) == hash(st.TemporalFrame.at(9, weekday=1))
