# Ported from graphify/tests/test_query_induced_edges.py (query must
# render every edge between visited nodes, not just the traversal tree).
# graphify -> kglib. `_complete_induced_edges` survived vendoring in
# kglib/serve.py (it is part of _bfs/_dfs), so the whole file ports.
# The final end-to-end case goes through kglib.flow.run_query (kg's port of the
# query CLI) instead of graphify.__main__.
from __future__ import annotations

import json

import networkx as nx
from networkx.readwrite import json_graph

from kglib.serve import _bfs, _dfs, _filter_graph_by_context, _query_graph_text
from kglib.flow import run_query

# Hub suppression only kicks in at degree >= 50 (serve.py `hub_threshold`).
_HUB_PADDING = 60


def _add(G, *names):
    for n in names:
        G.add_node(n, label=n, source_file=f"{n}.md", source_location="L1", community=0)


def _link(G, a, b, relation="references", context=None):
    G.add_edge(a, b, relation=relation, confidence="EXTRACTED", context=context)


def _pairs(edges):
    return {frozenset(e) for e in edges}


def _induced(G, visited):
    return {frozenset((u, v)) for u, v in G.edges() if u in visited and v in visited}


# --- the reported case -------------------------------------------------------


def test_bfs_records_edge_between_two_seeds():
    """The reporter's repro: both endpoints are seeds, so neither discovers the other."""
    G = nx.Graph()
    _add(G, "checkout", "discounted_total")
    _link(G, "checkout", "discounted_total")

    visited, edges = _bfs(G, ["checkout", "discounted_total"], depth=1)

    assert visited == {"checkout", "discounted_total"}
    assert _pairs(edges) == {frozenset(("checkout", "discounted_total"))}


def test_bfs_records_cross_edge_in_a_triangle():
    """n2-n3 closes the triangle but discovers nothing, so it was dropped."""
    G = nx.Graph()
    _add(G, "n1", "n2", "n3")
    _link(G, "n1", "n2")
    _link(G, "n1", "n3")
    _link(G, "n2", "n3")

    visited, edges = _bfs(G, ["n1"], depth=2)

    assert visited == {"n1", "n2", "n3"}
    assert _pairs(edges) == _induced(G, visited)


def test_bfs_records_edge_between_two_visited_hubs():
    """A hub is visited but never expanded, so hub-to-hub edges vanished."""
    G = nx.Graph()
    _add(G, "seed", "hub_a", "hub_b")
    _link(G, "seed", "hub_a")
    _link(G, "seed", "hub_b")
    _link(G, "hub_a", "hub_b")
    for i in range(_HUB_PADDING):
        _add(G, f"a{i}", f"b{i}")
        _link(G, "hub_a", f"a{i}")
        _link(G, "hub_b", f"b{i}")

    visited, edges = _bfs(G, ["seed"], depth=1)

    assert visited == {"seed", "hub_a", "hub_b"}
    assert frozenset(("hub_a", "hub_b")) in _pairs(edges)


def test_dfs_records_edge_between_two_visited_hubs():
    """DFS's only induced-edge gap: neither endpoint expands, so neither records it."""
    G = nx.Graph()
    _add(G, "seed", "hub_a", "hub_b")
    _link(G, "seed", "hub_a")
    _link(G, "seed", "hub_b")
    _link(G, "hub_a", "hub_b")
    for i in range(_HUB_PADDING):
        _add(G, f"a{i}", f"b{i}")
        _link(G, "hub_a", f"a{i}")
        _link(G, "hub_b", f"b{i}")

    visited, edges = _dfs(G, ["seed"], depth=1)

    assert visited == {"seed", "hub_a", "hub_b"}
    assert frozenset(("hub_a", "hub_b")) in _pairs(edges)


# --- invariants the completion pass must not break ---------------------------


def test_traversal_edges_keep_discovery_order_and_come_first():
    G = nx.Graph()
    _add(G, "n1", "n2", "n3")
    _link(G, "n1", "n2")
    _link(G, "n1", "n3")
    _link(G, "n2", "n3")

    _, edges = _bfs(G, ["n1"], depth=2)

    assert edges[:2] == [("n1", "n2"), ("n1", "n3")]


def test_no_duplicate_edges_are_returned():
    G = nx.Graph()
    _add(G, "n1", "n2", "n3", "n4")
    for a, b in [("n1", "n2"), ("n1", "n3"), ("n2", "n3"), ("n2", "n4"), ("n3", "n4")]:
        _link(G, a, b)

    for traverse in (_bfs, _dfs):
        _, edges = traverse(G, ["n1"], depth=3)
        assert len(edges) == len(_pairs(edges)), traverse.__name__


def test_completion_respects_the_context_filter():
    """The completion pass must scan the filtered graph, never the raw one."""
    G = nx.Graph()
    _add(G, "n1", "n2", "n3")
    _link(G, "n1", "n2", relation="references", context="call")
    _link(G, "n1", "n3", relation="cites", context="import")
    _link(G, "n2", "n3", relation="cites", context="import")

    filtered = _filter_graph_by_context(G, ["call"])
    _, edges = _bfs(filtered, ["n1", "n2", "n3"], depth=1)

    assert _pairs(edges) == {frozenset(("n1", "n2"))}, "a cites edge came back"


def test_self_loops_are_not_introduced():
    """A self-loop is never recorded by either traversal; the completion pass
    skips them (surfacing them is a separate output change)."""
    G = nx.Graph()
    _add(G, "recurse", "caller")
    _link(G, "recurse", "recurse")
    _link(G, "caller", "recurse")

    _, edges = _bfs(G, ["caller", "recurse"], depth=1)

    assert _pairs(edges) == {frozenset(("caller", "recurse"))}


def test_directed_graph_keeps_both_directions_of_a_mutual_edge():
    """u->v and v->u are distinct on a DiGraph; unordered dedup here would
    silently drop a real edge."""
    G = nx.DiGraph()
    _add(G, "ping", "pong")
    _link(G, "ping", "pong")
    _link(G, "pong", "ping")

    _, edges = _bfs(G, ["ping", "pong"], depth=1)

    assert set(edges) == {("ping", "pong"), ("pong", "ping")}


def test_directed_graph_renders_the_seed_to_seed_edge():
    G = nx.DiGraph()
    _add(G, "checkout", "discounted_total")
    _link(G, "checkout", "discounted_total")

    text = _query_graph_text(G, "checkout discounted_total", depth=1)

    assert "EDGE checkout --references" in text
    assert "discounted_total" in text


# --- end to end through the kg query flow -------------------------------------


def _write_two_seed_graph(tmp_path):
    """The reporter's shape: one edge whose endpoints are both seeds."""
    G = nx.Graph()
    G.add_node(
        "app_checkout",
        label="checkout",
        source_file="app.md",
        source_location="L4",
        community=0,
    )
    G.add_node(
        "pricing_discounted_total",
        label="discounted_total",
        source_file="pricing.md",
        source_location="L1",
        community=0,
    )
    G.add_edge(
        "app_checkout",
        "pricing_discounted_total",
        relation="references",
        confidence="EXTRACTED",
        source_file="app.md",
        source_location="L5",
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(G, edges="links")))
    return graph_path


def test_query_flow_renders_the_edge_between_two_seeds(tmp_path, capsys):
    graph_path = _write_two_seed_graph(tmp_path)
    rc = run_query("checkout discounted_total", graph_path=str(graph_path))
    assert rc == 0
    out = capsys.readouterr().out

    assert "NODE checkout" in out
    assert "NODE discounted_total" in out
    assert "EDGE checkout --references" in out
    assert "discounted_total" in out.split("EDGE checkout --references", 1)[1]
