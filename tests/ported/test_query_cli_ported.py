# Ported from graphify/tests/test_query_cli.py. Upstream drives graphify.__main__
# (cli.py glue); kg maps the same behaviors onto kglib.flow.run_query (the vendored
# port of the `query` command). monkeypatched argv plumbing becomes direct calls.
# graphify -> kglib; canned source_file names adapted .py -> .md.
import json

import networkx as nx
import pytest
from networkx.readwrite import json_graph

from kglib.flow import run_query


def _write_graph(tmp_path):
    G = nx.Graph()
    G.add_node("n1", label="extract", source_file="extract.md", source_location="L10", community=0)
    G.add_node("n2", label="cluster", source_file="cluster.md", source_location="L5", community=0)
    G.add_node("n3", label="build", source_file="build.md", source_location="L1", community=1)
    G.add_edge("n1", "n2", relation="归属", confidence="EXTRACTED", context="call")
    G.add_edge("n2", "n3", relation="阐述", confidence="EXTRACTED", context="import")
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(G, edges="links")))
    return graph_path


def test_query_explicit_context_filter(tmp_path, capsys):
    graph_path = _write_graph(tmp_path)
    rc = run_query("extract", context_filters=["call"], graph_path=str(graph_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Context: call (explicit)" in out
    assert "cluster" in out
    assert "build" not in out


def test_query_heuristic_context_filter(tmp_path, capsys):
    graph_path = _write_graph(tmp_path)
    rc = run_query("who calls extract", graph_path=str(graph_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Context: call (heuristic)" in out
    assert "cluster" in out
    assert "build" not in out


def _write_calls_graph(tmp_path):
    """A single directional edge on an (on-disk) undirected graph.json,
    the standard kg build output shape (`"directed": false`, direction implied
    only by each link's source/target)."""
    G = nx.Graph()
    G.add_node("caller", label="caller_doc", source_file="a.md", source_location="L1", community=0)
    G.add_node("callee", label="callee_doc", source_file="b.md", source_location="L1", community=1)
    G.add_edge("caller", "callee", relation="归属", confidence="EXTRACTED", context="call")
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(G, edges="links")))
    return graph_path


def test_query_preserves_edge_direction_when_seeded_on_callee(tmp_path, capsys):
    """`kg query` must render edges source->target regardless of which endpoint
    the query term matches first (the loaded graph is undirected, so BFS visit
    order is not direction truth)."""
    graph_path = _write_calls_graph(tmp_path)
    rc = run_query("callee_doc", graph_path=str(graph_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "caller_doc --归属" in out
    assert "callee_doc --归属" not in out


def test_query_preserves_edge_direction_when_seeded_on_caller(tmp_path, capsys):
    """Same edge, seeded from the source side — must stay correct too."""
    graph_path = _write_calls_graph(tmp_path)
    rc = run_query("caller_doc", graph_path=str(graph_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "caller_doc --归属" in out
    assert "callee_doc --归属" not in out


def test_query_rejects_oversized_graph(monkeypatch, tmp_path, capsys):
    """#F4: query must refuse to parse a graph.json that exceeds the cap."""
    graph_path = _write_graph(tmp_path)
    monkeypatch.setattr("kglib.security._MAX_GRAPH_FILE_BYTES", 16)
    with pytest.raises(SystemExit):
        run_query("extract", graph_path=str(graph_path))
    err = capsys.readouterr().err
    assert "exceeds" in err
    assert "byte cap" in err
