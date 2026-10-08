"""Pure knowledge-graph primitive (kernel.graph — roadmap Phase T).

The graph is built from plain knowledge.py-shaped fact / relation rows and
offers a small, pure, case-insensitive query surface. PURE: nothing live
consumes it yet, so these tests pin the structure and queries directly.
"""
import pytest


@pytest.fixture
def graph(load):
    return load("kernel.graph")


# ── construction ─────────────────────────────────────────────────────────────

def test_from_rows_builds_entities_and_relations(graph):
    g = graph.KnowledgeGraph.from_rows(
        facts=[
            {"subject": "Front Door", "key": "material", "value": "oak",
             "confidence": 0.9, "source": "user"},
            {"subject": "Front Door", "key": "color", "value": "red"},
            {"subject": "Garage", "key": "capacity", "value": "2 cars"},
        ],
        relations=[
            {"subject": "Front Door", "predicate": "leads_to", "object": "Hallway",
             "confidence": 0.8},
        ],
    )
    # entities: Front Door, Garage, Hallway (Hallway implied by the relation)
    assert [e.name for e in g.entities] == ["Front Door", "Garage", "Hallway"]
    fd = g.entity("front door")        # case-insensitive lookup
    assert fd is not None
    # attributes sorted by key: color, material
    assert [(a.key, a.value) for a in fd.attributes] == [
        ("color", "red"), ("material", "oak")]
    assert fd.get("MATERIAL") == "oak" and fd.get("missing") is None
    assert len(g.relations) == 1 and g.relations[0].predicate == "leads_to"


def test_from_rows_is_total_on_messy_input(graph):
    g = graph.KnowledgeGraph.from_rows(
        facts=[
            {"subject": "", "key": "x", "value": "1"},      # no subject → skip
            {"subject": "A", "key": "", "value": "1"},      # no key → skip
            "not a mapping",                                 # junk → skip
            {"subject": "A", "key": "k", "value": "v", "confidence": "bad"},  # bad conf → default
        ],
        relations=[
            {"subject": "A", "predicate": "p"},             # no object → skip
            {"subject": "A", "predicate": "rel", "object": "B"},
            {"subject": "A", "predicate": "rel", "object": "B"},  # dup → one edge
        ],
    )
    a = g.entity("A")
    assert a is not None and a.get("k") == "v"
    assert a.attributes[0].confidence == 1.0        # bad confidence → default 1.0
    assert len(g.relate("A", predicate="rel")) == 1   # de-duped


def test_empty_graph(graph):
    g = graph.KnowledgeGraph.from_rows()
    assert g.is_empty() and g.entity("anything") is None
    assert g.relate("x") == [] and g.neighbors("x") == []


# ── queries ──────────────────────────────────────────────────────────────────

@pytest.fixture
def home(graph):
    return graph.KnowledgeGraph.from_rows(
        facts=[{"subject": "Sam", "key": "role", "value": "resident"}],
        relations=[
            {"subject": "Sam", "predicate": "owns", "object": "Car"},
            {"subject": "Sam", "predicate": "owns", "object": "Bike"},
            {"subject": "Sam", "predicate": "lives_in", "object": "House"},
            {"subject": "Alex", "predicate": "owns", "object": "Car"},
        ],
    )


def test_relate_filters_each_slot(home):
    assert {r.object for r in home.relate("Sam", predicate="owns")} == {"Car", "Bike"}
    assert {r.subject for r in home.relate(object="Car")} == {"Sam", "Alex"}
    assert {r.predicate for r in home.relate("Sam")} == {"owns", "lives_in"}
    assert home.relate("Nobody") == []


def test_neighbors(home):
    assert home.neighbors("sam") == ["Car", "Bike", "House"]          # out-edges, first-seen order
    assert home.neighbors("Sam", predicate="owns") == ["Car", "Bike"]
    assert home.neighbors("Car") == []                               # no out-edges


def test_frozen_and_hashable(graph):
    g = graph.KnowledgeGraph.from_rows(
        facts=[{"subject": "A", "key": "k", "value": "v"}])
    # frozen dataclasses → immutable + hashable
    with pytest.raises(Exception):
        g.entities[0].attributes[0].key = "x"
    assert hash(g.entity("A")) == hash(g.entity("A"))
