# Vendored-test layer 1: deterministic equivalence between upstream graphify
# and the vendored kglib (documents-only slim).
#
# Upstream is imported from the checkout at ../../graphify (same interpreter,
# same dependency versions resolved by uv), so any divergence between the two
# is a vendoring bug, not an environment difference.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import FIXTURES_DIR, UPSTREAM_DIR

if str(UPSTREAM_DIR) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_DIR))

graphify_build = pytest.importorskip("graphify.build", reason="upstream graphify checkout not importable")
graphify_cluster = pytest.importorskip("graphify.cluster")
graphify_analyze = pytest.importorskip("graphify.analyze")
graphify_serve = pytest.importorskip("graphify.serve")
graphify_export = pytest.importorskip("graphify.export")

import networkx as nx
from networkx.readwrite import json_graph

from kglib import analyze as kg_analyze
from kglib import build as kg_build
from kglib import cluster as kg_cluster
from kglib import export as kg_export
from kglib import serve as kg_serve


@pytest.fixture(scope="module")
def extraction() -> dict:
    return json.loads((FIXTURES_DIR / "extraction_docs.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def root() -> str:
    # A stable root for source_file relativization; the fixture's source_file
    # values are already relative, so this only needs to be identical on both sides.
    return "/tmp/kg-equivalence-root"


def _communities_as_sets(communities: dict) -> set:
    return {frozenset(members) for members in communities.values()}


# --- build + cluster + analyze equivalence ---------------------------------

def test_build_from_json_equivalent(extraction, root):
    G_up = graphify_build.build_from_json(dict(extraction), root=root, directed=False)
    G_kg = kg_build.build_from_json(dict(extraction), root=root, directed=False)
    assert G_up.number_of_nodes() == G_kg.number_of_nodes()
    assert G_up.number_of_edges() == G_kg.number_of_edges()
    assert set(G_up.nodes()) == set(G_kg.nodes())
    # Same edges with same relations/confidences (order-insensitive).
    def _edge_key(G, u, v):
        d = graphify_build.edge_data(G, u, v)
        return (frozenset((u, v)), d.get("relation"), d.get("confidence"))
    up_edges = {_edge_key(G_up, u, v) for u, v in G_up.edges()}
    kg_edges = {_edge_key(G_kg, u, v) for u, v in G_kg.edges()}
    assert up_edges == kg_edges
    # Hyperedges survive identically.
    up_he = sorted(json.dumps(h, sort_keys=True) for h in G_up.graph.get("hyperedges", []))
    kg_he = sorted(json.dumps(h, sort_keys=True) for h in G_kg.graph.get("hyperedges", []))
    assert up_he == kg_he


def test_cluster_partition_equivalent(extraction, root):
    G_up = graphify_build.build_from_json(dict(extraction), root=root, directed=False)
    G_kg = kg_build.build_from_json(dict(extraction), root=root, directed=False)
    c_up = graphify_cluster.cluster(G_up)
    c_kg = kg_cluster.cluster(G_kg)
    assert _communities_as_sets(c_up) == _communities_as_sets(c_kg)


def test_analyze_equivalent(extraction, root):
    G_up = graphify_build.build_from_json(dict(extraction), root=root, directed=False)
    G_kg = kg_build.build_from_json(dict(extraction), root=root, directed=False)
    c_up = graphify_cluster.cluster(G_up)
    c_kg = kg_cluster.cluster(G_kg)

    gods_up = {g["id"] for g in graphify_analyze.god_nodes(G_up)}
    gods_kg = {g["id"] for g in kg_analyze.god_nodes(G_kg)}
    assert gods_up == gods_kg

    def _surprise_key(s):
        return (s["source"], s["target"], s.get("relation"), s.get("confidence"))
    sc_up = {_surprise_key(s) for s in graphify_analyze.surprising_connections(G_up, c_up)}
    sc_kg = {_surprise_key(s) for s in kg_analyze.surprising_connections(G_kg, c_kg)}
    assert sc_up == sc_kg


# --- query engine parity ---------------------------------------------------

def _write_graph_json(export_mod, G, communities, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wrote = export_mod.to_json(G, communities, str(path))
    assert wrote


def _load_for_query(path: Path) -> nx.Graph:
    """Mirror the kg/cli query loader (undirected, _src/_tgt backfilled)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    raw = dict(raw, links=[
        {**link, "_src": link.get("_src", link.get("source")),
         "_tgt": link.get("_tgt", link.get("target"))}
        for link in raw.get("links", [])
    ])
    try:
        return json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        return json_graph.node_link_graph(raw)


def _parse_query_output(text: str) -> tuple[list[str], set[str], set[str]]:
    """Structural projection of a query result: (start labels, node labels, edge lines)."""
    import ast
    lines = text.splitlines()
    header = lines[0]
    start = []
    for part in header.split(" | "):
        if part.startswith("Start: "):
            start = ast.literal_eval(part[len("Start: "):])
    nodes = {l[len("NODE "):].split(" [", 1)[0] for l in lines if l.startswith("NODE ")}
    edges = {l for l in lines if l.startswith("EDGE ")}
    return start, nodes, edges


@pytest.mark.parametrize("question", [
    "self attention multi head",
    "training pipeline learning rate",
    "transformer embedding",
])
def test_query_engine_parity(extraction, root, tmp_path, question):
    # Build + export once per side (each side's own to_json), then load each
    # graph.json identically and run each side's query engine.
    G_up = graphify_build.build_from_json(dict(extraction), root=root, directed=False)
    c_up = graphify_cluster.cluster(G_up)
    G_kg = kg_build.build_from_json(dict(extraction), root=root, directed=False)
    c_kg = kg_cluster.cluster(G_kg)

    up_path = tmp_path / "up" / "graph.json"
    kg_path = tmp_path / "kg" / "graph.json"
    _write_graph_json(graphify_export, G_up, c_up, up_path)
    _write_graph_json(kg_export, G_kg, c_kg, kg_path)

    Gq_up = _load_for_query(up_path)
    Gq_kg = _load_for_query(kg_path)
    out_up = graphify_serve._query_graph_text(Gq_up, question, mode="bfs", depth=2, token_budget=2000)
    out_kg = kg_serve._query_graph_text(Gq_kg, question, mode="bfs", depth=2, token_budget=2000)

    start_up, nodes_up, edges_up = _parse_query_output(out_up)
    start_kg, nodes_kg, edges_kg = _parse_query_output(out_kg)
    assert start_up == start_kg, f"start nodes diverged for {question!r}"
    assert nodes_up == nodes_kg, f"visited node sets diverged for {question!r}"
    assert edges_up == edges_kg, f"rendered edges diverged for {question!r}"


# --- shrink-guard parity (#479) ---------------------------------------------

def _mk_graph(n: int) -> nx.Graph:
    G = nx.Graph()
    for i in range(n):
        G.add_node(f"n{i}", label=f"n{i}", community=0)
    return G


def test_to_json_shrink_guard_parity(tmp_path):
    # Upstream side.
    up_graph = tmp_path / "up" / "graph.json"
    up_graph.parent.mkdir(parents=True)
    json.dump({"nodes": [{"id": f"n{i}"} for i in range(5)]}, up_graph.open("w"))
    assert graphify_export.to_json(_mk_graph(2), {}, str(up_graph), force=False) is False
    assert graphify_export.to_json(_mk_graph(2), {}, str(up_graph), force=True) is True

    # kglib side must behave identically.
    kg_graph = tmp_path / "kg" / "graph.json"
    kg_graph.parent.mkdir(parents=True)
    json.dump({"nodes": [{"id": f"n{i}"} for i in range(5)]}, kg_graph.open("w"))
    assert kg_export.to_json(_mk_graph(2), {}, str(kg_graph), force=False) is False
    assert kg_export.to_json(_mk_graph(2), {}, str(kg_graph), force=True) is True


def test_to_json_corrupt_existing_fail_closed_parity(tmp_path):
    for name, mod in (("up", graphify_export), ("kg", kg_export)):
        p = tmp_path / name / "graph.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not valid json but not empty")
        assert mod.to_json(_mk_graph(10), {}, str(p), force=False) is False
        assert mod.to_json(_mk_graph(10), {}, str(p), force=True) is True
