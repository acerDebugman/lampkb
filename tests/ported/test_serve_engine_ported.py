# Ported from graphify/tests/test_serve.py — engine-level cases skipped by the
# first pass: _find_node tiers, trigram prefilter, _bfs/_dfs, _subgraph_to_text,
# context filters. graphify -> kglib.
# NOT ported: _load_graph tests (that loader is part of upstream's MCP server
# section and was not vendored) and _communities_from_graph (same).
import json
import unicodedata

import pytest
import networkx as nx
from networkx.readwrite import json_graph

from kglib.serve import (
    _strip_diacritics,
    _score_nodes,
    _score_query,
    _pick_seeds,
    _bfs,
    _dfs,
    _find_node,
    _trigrams,
    _node_search_text,
    _get_trigram_index,
    _trigram_candidates,
    _filter_graph_by_context,
    _infer_context_filters,
    _query_terms,
    _query_graph_text,
    _resolve_context_filters,
    _subgraph_to_text,
    _search_tokens,
)


def _make_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("n1", label="extract", source_file="extract.md", source_location="L10", community=0)
    G.add_node("n2", label="cluster", source_file="cluster.md", source_location="L5", community=0)
    G.add_node("n3", label="build", source_file="build.md", source_location="L1", community=1)
    G.add_node("n4", label="report", source_file="report.md", source_location="L1", community=1)
    G.add_node("n5", label="isolated", source_file="other.md", source_location="L1", community=2)
    G.add_edge("n1", "n2", relation="归属", confidence="INFERRED", context="call")
    G.add_edge("n2", "n3", relation="阐述", confidence="EXTRACTED", context="import")
    G.add_edge("n3", "n4", relation="uses", confidence="EXTRACTED")
    return G


# --- _find_node tiers -----------------------------------------------------------

def test_find_node_ignores_trailing_punctuation():
    G = _make_graph()
    assert _find_node(G, "extract?") == ["n1"]


def test_find_node_matches_full_punctuated_unicode_label():
    G = nx.Graph()
    G.add_node("n1", label="Skill /auditar — Auditoría inquisitiva de enlaces")

    assert _find_node(G, "Skill /auditar — Auditoría inquisitiva de enlaces") == ["n1"]


def test_find_node_matches_punctuated_file_label_exactly():
    # An exactly-typed punctuated file label must resolve through explain,
    # just like it does through path/query.
    G = nx.Graph()
    G.add_node("f1", label="blockStream.md", norm_label="blockstream.md",
               source_file="lib/blockStream.md", source_location="L1")
    G.add_node("f2", label="blockStream.test.md", norm_label="blockstream.test.md",
               source_file="lib/blockStream.test.md", source_location="L1")
    assert _find_node(G, "blockStream.md")[0] == "f1"
    assert _find_node(G, "blockStream.test.md")[0] == "f2"


def test_find_node_resolves_when_label_and_norm_label_diverge():
    # Hardening: when `label` and `norm_label` diverge, only the symmetric
    # `norm_query == norm_label` match resolves it.
    G = nx.Graph()
    G.add_node("n1", label="BlockStream", norm_label="blockstream.md",
               source_file="lib/x.md", source_location="L1")
    assert _find_node(G, "blockStream.md") == ["n1"]


def test_find_node_matches_punctuated_node_id_exactly():
    # Only the symmetric `norm_query == nid_norm` match resolves an
    # exactly-typed node id carrying punctuation.
    G = nx.Graph()
    G.add_node("concept:domain:widget", label="Widget", norm_label="widget",
               source_file="docs/domain.md", source_location="L1")
    G.add_node("plain_node_id", label="Plain", norm_label="plain",
               source_file="docs/plain.md", source_location="L1")
    assert _find_node(G, "concept:domain:widget") == ["concept:domain:widget"]
    assert _find_node(G, "plain_node_id") == ["plain_node_id"]   # unpunctuated ids as before
    assert _find_node(G, "Widget") == ["concept:domain:widget"]  # label lookup as before


def test_find_node_matches_namespaced_node_id():
    # A "<repo>::"-namespaced id (upstream's merge-graphs prefixer) must
    # resolve by exact id — the engine-side tiering is what matters here.
    G = nx.Graph()
    G.add_node("backend::docs_server_router", label="Router",
               norm_label="router", source_file="docs/server/router.md",
               source_location="L12")
    assert _find_node(G, "backend::docs_server_router") == ["backend::docs_server_router"]


# --- trigram candidate prefilter ------------------------------------------------

def _force_full_scan(monkeypatch):
    """Disable the prefilter so a call exercises the original full-node scan."""
    monkeypatch.setattr("kglib.serve._trigram_candidates", lambda *a, **k: None)


def _make_big_graph(n: int = 150) -> nx.Graph:
    """A graph large enough that the selectivity guard lets the fast-path fire for
    rare terms and fall back for common ones."""
    G = nx.Graph()
    for i in range(n):
        G.add_node(f"id{i}", label=f"item node {i}", source_file=f"pkg/item_{i}.md")
    G.add_node("rareA", label="ZebraQuokkaWidget", source_file="zoo/zqw.md")
    G.add_node("rareB", label="MarmosetGadget handler", source_file="zoo/marmoset.md")
    G.add_node("punct", label="Foo.Bar:Baz", source_file="pkg/foobar.md")
    return G


def _make_non_ascii_id_graph(n: int = 40) -> nx.Graph:
    """A graph whose ids carry Hangul, large enough that the prefilter really runs."""
    G = nx.Graph()
    for i in range(n):
        G.add_node(f"id{i}", label=f"item node {i}", source_file=f"pkg/item_{i}.md")
    G.add_node("concept:domain:한글", label="Hangul domain",
               source_file="docs/한글.md", source_location="L1")
    G.add_node("문서_목록", label="DocumentList",
               source_file="docs/문서_목록.md", source_location="L1")
    return G


def test_trigrams_basic():
    assert _trigrams("foobar") == {"foo", "oob", "oba", "bar"}
    assert _trigrams("ab") == {"ab"}        # <3 chars -> whole string is the key
    assert _trigrams("") == set()


def test_node_search_text_includes_all_matched_fields():
    G = _make_big_graph()
    text = _node_search_text(G.nodes["punct"], "punct")
    # norm_label, tokenized label, nid, raw source, and tokenized source are all
    # present, NUL-separated so trigrams can't span fields.
    parts = text.split("\x00")
    assert parts[0] == "foo.bar:baz"          # norm_label (punctuation kept)
    assert parts[1] == "foo bar baz"          # label_tokens (tokenized)
    assert parts[2] == "punct"                # nid
    assert parts[3] == "pkg/foobar.md"        # source_file
    assert parts[4] == "pkg foobar md"        # source_file tokens
    assert len(parts) == 5                    # no folded-id field for an ASCII id


def test_node_search_text_appends_folded_non_ascii_node_id():
    # For a Hangul id the raw and folded forms differ; the index has to
    # carry the folded form too, appended so the other field positions do not move.
    G = _make_non_ascii_id_graph()
    nid = "concept:domain:한글"
    parts = _node_search_text(G.nodes[nid], nid).split("\x00")
    assert parts[2] == nid
    assert parts[5] == _strip_diacritics(nid).lower()
    assert parts[5] != parts[2]


def test_trigram_candidates_fast_path_fires_for_rare_term():
    G = _make_big_graph()
    cand = _trigram_candidates(G, ["zebraquokkawidget"])
    assert cand is not None                   # selective -> fast-path used
    assert "rareA" in cand
    assert len(cand) < G.number_of_nodes()    # a real shrink, not the whole graph


def test_trigram_candidates_falls_back_on_common_term():
    G = _make_big_graph()
    # 'item' is in the label of every one of the 150 'item node N' nodes -> the
    # rarest trigram is still common -> guard returns None (full-scan fallback).
    assert _trigram_candidates(G, ["item"]) is None


def test_trigram_candidates_falls_back_on_short_token():
    G = _make_big_graph()
    assert _trigram_candidates(G, ["ab"]) is None   # <3 chars -> can't trigram-filter


def test_score_nodes_prefilter_is_identical_to_full_scan(monkeypatch):
    G = _make_big_graph()
    queries = ["zebraquokkawidget", "marmosetgadget handler", "foo bar baz",
               "item", "node 42", "nonexistentxyz"]
    for q in queries:
        terms = _query_terms(q)
        fast = _score_nodes(G, terms)
        _force_full_scan(monkeypatch)
        full = _score_nodes(G, terms)
        monkeypatch.undo()
        assert fast == full, f"prefilter diverged from full scan for {q!r}"


def test_find_node_prefilter_is_identical_to_full_scan(monkeypatch):
    G = _make_big_graph()
    # includes the punctuated label, exercised via its tokenized (label_tokens) form
    for label in ["ZebraQuokkaWidget", "MarmosetGadget handler", "Foo Bar Baz",
                  "item node 7", "missing"]:
        fast = _find_node(G, label)
        _force_full_scan(monkeypatch)
        full = _find_node(G, label)
        monkeypatch.undo()
        assert fast == full, f"_find_node prefilter diverged (order!) for {label!r}"


def test_find_node_matches_non_ascii_node_id_through_prefilter():
    # Queries fold through `_strip_diacritics`; the index must carry the
    # folded id form or the node is dropped before any predicate runs.
    G = _make_non_ascii_id_graph()
    for nid in ("concept:domain:한글", "문서_목록"):
        assert unicodedata.normalize("NFKD", nid) != nid   # fixture must stay NFKD-sensitive
        needles = [" ".join(_search_tokens(nid)), _strip_diacritics(nid).lower()]
        candidates = _trigram_candidates(G, needles)
        assert candidates is not None                      # index path, not the full-scan fallback
        assert nid in candidates
        assert _find_node(G, nid) == [nid]


def test_find_node_node_id_prefilter_is_identical_to_full_scan(monkeypatch):
    # An id must resolve the same way whether the candidates came from the
    # trigram index or from the full scan.
    G = _make_non_ascii_id_graph()
    for label in ["concept:domain:한글", "문서_목록", "id7", "item node 7",
                  "DocumentList", "missing"]:
        fast = _find_node(G, label)
        _force_full_scan(monkeypatch)
        full = _find_node(G, label)
        monkeypatch.undo()
        assert fast == full, f"_find_node prefilter diverged (order!) for {label!r}"


def test_find_node_label_tokens_branch_covered_by_index():
    # "foo bar baz" matches label "Foo.Bar:Baz" only via the tokenized label_tokens
    # form. The index must surface this node as a candidate.
    G = _make_big_graph()
    assert _find_node(G, "Foo Bar Baz") == ["punct"]


def test_find_node_source_file_path_prefers_file_level_node():
    G = _make_big_graph()
    source_file = "app/api/example/route.md"
    # Insert the member node first to prove source-file lookup reorders the
    # file-level node ahead of other nodes from the same file.
    G.add_node(
        "example_route_get",
        label="GET()",
        source_file=source_file,
        source_location="L42",
    )
    G.add_node(
        "example_route",
        label="route.md",
        source_file=source_file,
        source_location="L1",
    )

    matches = _find_node(G, source_file)

    assert matches[0] == "example_route"
    assert "example_route_get" in matches


def test_trigram_index_cached_and_rebuilt_per_graph():
    G = _make_big_graph()
    idx1 = _get_trigram_index(G)
    assert idx1 is _get_trigram_index(G)            # cached on the same graph object
    assert G.graph["_trigram_index"] is idx1
    G2 = _make_big_graph()
    assert _get_trigram_index(G2) is not idx1       # a fresh graph rebuilds (reload safety)


# --- German/CJK query handling ----------------------------------------------------

def test_pick_seeds_german_query_seeds_content_node_not_heading_noise():
    """End-to-end: a German question over a graph with German
    heading-noise nodes must seed on the content noun, not on nodes that
    happen to contain 'die'/'wie'/'wird'."""
    G = nx.DiGraph()
    G.add_node("cfg", label="Die Konfiguration", source_file="docs/konfiguration.md")
    G.add_node("sec", label="Wie wird gesichert", source_file="docs/sicherheit.md")
    G.add_node("auth", label="Authentifizierung", source_file="docs/auth.md")
    G.add_node("helper", label="login_helper", source_file="docs/auth.md")
    G.add_edge("helper", "auth")

    q = "Wie funktioniert die Authentifizierung?"
    terms = _query_terms(q)
    # _score_query does combined scoring + per-term singleton winners in
    # one traversal; _pick_seeds consumes best_seed_by_term for the per-term
    # guarantee.
    qs = _score_query(G, terms, collect_per_term_seeds=True)
    seeds = _pick_seeds(qs.ranked, G=G, best_seed_by_term=qs.best_seed_by_term)
    assert "auth" in seeds
    assert "cfg" not in seeds
    assert "sec" not in seeds


def test_query_terms_filters_only_short_english_terms(monkeypatch):
    import kglib.serve as serve_mod

    class FakeJieba:
        def cut(self, text):
            return {
                "前端": ["前端"],
                "依赖": ["依赖"],
                "安装": ["安装"],
                "包管理器": ["包", "管理器"],
                "项目约定": ["项目", "约定"],
                "a前": ["a", "前"],
            }[text]

    monkeypatch.setattr(serve_mod, "_jieba", FakeJieba())
    terms = _query_terms("前端 dependency 依赖 install 安装 to of 包管理器 项目约定 a前")
    assert terms == ["前端", "dependency", "依赖", "install", "安装", "包", "管理器", "包管理器", "项目", "约定", "项目约定", "前", "a前"]


def test_query_graph_text_keeps_short_non_english_terms():
    G = nx.Graph()
    G.add_node("frontend", label="前端", source_file="docs/前端.md", source_location="L1", community=0)
    text = _query_graph_text(G, "前端", mode="bfs", depth=1)
    assert "No matching nodes found." not in text
    assert "NODE 前端" in text


def test_infer_context_filters_for_calls_question():
    assert _infer_context_filters("who calls extract") == ["call"]


def test_resolve_context_filters_explicit_overrides_heuristic():
    filters, source = _resolve_context_filters("who calls extract", ["field"])
    assert filters == ["field"]
    assert source == "explicit"


# --- _bfs / _dfs -----------------------------------------------------------------

def test_bfs_depth_1():
    G = _make_graph()
    visited, edges = _bfs(G, ["n1"], depth=1)
    assert "n1" in visited
    assert "n2" in visited  # direct neighbor
    assert "n3" not in visited  # 2 hops away


def test_bfs_depth_2():
    G = _make_graph()
    visited, edges = _bfs(G, ["n1"], depth=2)
    assert "n3" in visited  # n1 -> n2 -> n3


def test_bfs_disconnected():
    G = _make_graph()
    visited, edges = _bfs(G, ["n5"], depth=3)
    assert visited == {"n5"}  # isolated node


def test_bfs_returns_edges():
    G = _make_graph()
    visited, edges = _bfs(G, ["n1"], depth=1)
    assert len(edges) >= 1
    assert any(u == "n1" or v == "n1" for u, v in edges)


def test_filter_graph_by_context_limits_traversal():
    G = _make_graph()
    filtered = _filter_graph_by_context(G, ["call"])
    visited, edges = _bfs(filtered, ["n1"], depth=2)
    assert "n2" in visited
    assert "n3" not in visited
    assert edges == [("n1", "n2")]


def test_dfs_depth_1():
    G = _make_graph()
    visited, edges = _dfs(G, ["n1"], depth=1)
    assert "n1" in visited
    assert "n2" in visited
    assert "n3" not in visited


def test_dfs_full_chain():
    G = _make_graph()
    visited, edges = _dfs(G, ["n1"], depth=5)
    assert {"n1", "n2", "n3", "n4"}.issubset(visited)


# --- _subgraph_to_text -------------------------------------------------------------

def test_subgraph_to_text_contains_labels():
    G = _make_graph()
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    assert "extract" in text
    assert "cluster" in text


def test_subgraph_to_text_truncates():
    G = _make_graph()
    # Very small budget forces truncation
    text = _subgraph_to_text(G, {"n1", "n2", "n3", "n4"}, [("n1", "n2")], token_budget=1)
    assert "truncated" in text


def test_subgraph_to_text_edge_included():
    G = _make_graph()
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    assert "EDGE" in text
    assert "归属" in text


def test_subgraph_to_text_includes_edge_context():
    G = _make_graph()
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    assert "context=call" in text


# --- work-memory overlay annotation on NODE lines --------------------------------

def test_subgraph_to_text_annotates_node_with_learning_status():
    """An annotated node gets a `learning=<status>` suffix inside its NODE
    bracket; an un-annotated node gets none."""
    G = _make_graph()
    G.graph["_learning_overlay"] = {
        "n1": {"status": "preferred", "stale": False},
    }
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    lines = {l.split()[1]: l for l in text.splitlines() if l.startswith("NODE ")}
    assert "learning=preferred]" in lines["extract"]
    assert "learning=" not in lines["cluster"]  # un-annotated node


def test_subgraph_to_text_marks_stale_status():
    G = _make_graph()
    G.graph["_learning_overlay"] = {"n1": {"status": "contested", "stale": True}}
    text = _subgraph_to_text(G, {"n1"}, [])
    assert "learning=contested:stale]" in text


def test_subgraph_to_text_learning_suffix_counts_against_budget():
    """The learning= suffix is part of the NODE line BEFORE the budget cut."""
    G = _make_graph()
    bare = _subgraph_to_text(G, {"n1", "n2", "n3"}, [])
    # token_budget chosen so the un-annotated render fits without truncation...
    budget = (len(bare) // 3) + 1
    assert "truncated" not in _subgraph_to_text(G, {"n1", "n2", "n3"}, [],
                                                token_budget=budget)
    # ...but once every node carries a learning= suffix, the same budget overflows.
    G.graph["_learning_overlay"] = {
        n: {"status": "preferred", "stale": False} for n in ("n1", "n2", "n3")
    }
    annotated = _subgraph_to_text(G, {"n1", "n2", "n3"}, [], token_budget=budget)
    assert "learning=preferred" in annotated
    assert "truncated" in annotated


def test_subgraph_to_text_no_overlay_is_unchanged():
    """With no overlay on the graph, NODE lines carry no learning= suffix."""
    G = _make_graph()
    text = _subgraph_to_text(G, {"n1", "n2"}, [("n1", "n2")])
    assert "learning=" not in text


def test_query_graph_text_explicit_context_filter_changes_traversal():
    G = _make_graph()
    text = _query_graph_text(G, "extract", mode="bfs", depth=2, token_budget=2000, context_filters=["call"])
    assert "Context: call (explicit)" in text
    assert "cluster" in text
    assert "build" not in text


def test_query_graph_text_heuristic_context_filter_changes_traversal():
    G = _make_graph()
    text = _query_graph_text(G, "who calls extract", mode="bfs", depth=2, token_budget=2000)
    assert "Context: call (heuristic)" in text
    assert "cluster" in text
    assert "build" not in text
