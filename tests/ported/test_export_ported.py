# Ported from graphify/tests/test_export.py (to_json shrink guard + html null
# handling). graphify -> kglib; obsidian/canvas/cypher/graphml/svg tests dropped
# (exporters not vendored).
import json

from kglib.export import to_json, to_html, existing_graph_node_count, MALFORMED_GRAPH


def _mkG(n):
    import networkx as nx
    G = nx.Graph()
    for i in range(n):
        G.add_node(f"n{i}", label=f"n{i}", community=0)
    return G


def test_to_json_refuses_shrink(tmp_path):
    """Refuse to silently overwrite an existing graph with fewer nodes."""
    p = tmp_path / "graph.json"
    json.dump({"nodes": [{"id": f"n{i}"} for i in range(5)]}, p.open("w"))
    assert to_json(_mkG(2), {}, str(p), force=False) is False
    assert to_json(_mkG(2), {}, str(p), force=True) is True  # force overrides


def test_to_json_fails_safe_on_corrupt_existing(tmp_path):
    """A non-empty but unparseable existing graph.json (corrupt or mid-write)
    must NOT be silently overwritten — we can't verify the new graph isn't a
    partial shrink, so fail safe (refuse) unless force is given."""
    p = tmp_path / "graph.json"
    p.write_text("{ this has content but is not valid json")
    assert to_json(_mkG(10), {}, str(p), force=False) is False
    assert to_json(_mkG(10), {}, str(p), force=True) is True


def test_to_json_proceeds_on_empty_existing(tmp_path):
    """An empty/whitespace existing file has no nodes to lose, so it is not a
    shrink risk — the write proceeds."""
    p = tmp_path / "graph.json"
    p.write_text("")
    assert to_json(_mkG(3), {}, str(p), force=False) is True
    data = json.loads(p.read_text())
    assert len(data["nodes"]) == 3


def test_to_html_handles_null_source_file_and_label(tmp_path):
    """A node with source_file=None or label=None must not crash to_html."""
    import networkx as nx
    G = nx.Graph()
    G.add_node("n1", label="Foo", source_file=None, community=0)
    G.add_node("n2", label=None, source_file="a.md", community=0)
    G.add_node("n3", label=None, source_file=None, community=0)
    out = tmp_path / "graph.html"
    to_html(G, {0: ["n1", "n2", "n3"]}, str(out))
    assert out.exists() and out.stat().st_size > 0


def test_existing_graph_node_count(tmp_path):
    p = tmp_path / "graph.json"
    assert existing_graph_node_count(p) is None            # absent -> nothing to protect
    p.write_text("", encoding="utf-8")
    assert existing_graph_node_count(p) is None            # empty -> nothing to protect
    # Non-empty but unparseable must fail CLOSED (sentinel), matching to_json's
    # shrink guard — a corrupt/mid-write file could be hiding a complete graph.
    p.write_text("{not json", encoding="utf-8")
    assert existing_graph_node_count(p) is MALFORMED_GRAPH  # malformed -> fail closed
    p.write_text('{"nodes": "notalist"}', encoding="utf-8")
    assert existing_graph_node_count(p) is MALFORMED_GRAPH  # structurally wrong -> fail closed
    p.write_text('{"nodes": [{"id": "a"}, {"id": "b"}], "links": []}', encoding="utf-8")
    assert existing_graph_node_count(p) == 2               # valid
