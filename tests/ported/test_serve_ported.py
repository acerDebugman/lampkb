# Ported from graphify/tests/test_serve.py (query scoring + term handling).
# graphify -> kglib; the canned labels use .md source files instead of .py
# (docs-only cosmetic adaptation — the scoring logic is label-shape-based).
# _communities_from_graph tests are NOT ported: that helper lives in upstream's
# MCP server section and was not vendored.
import pytest
import networkx as nx

from kglib.serve import (
    _score_nodes,
    _compute_idf,
    _EXACT_MATCH_BONUS,
    _SOURCE_MATCH_BONUS,
    _query_terms,
)


def _make_graph() -> nx.Graph:
    G = nx.Graph()
    G.add_node("n1", label="extract", source_file="extract.md", source_location=None, community=0)
    G.add_node("n2", label="cluster", source_file="cluster.md", source_location=None, community=0)
    G.add_node("n3", label="build", source_file="build.md", source_location=None, community=1)
    G.add_node("n4", label="report", source_file="report.md", source_location=None, community=1)
    G.add_node("n5", label="isolated", source_file="other.md", source_location=None, community=2)
    G.add_edge("n1", "n2", relation="归属", confidence="INFERRED")
    G.add_edge("n2", "n3", relation="归属", confidence="EXTRACTED")
    G.add_edge("n3", "n4", relation="uses", confidence="EXTRACTED")
    return G


# --- _score_nodes ---

def test_score_nodes_exact_label_match():
    G = _make_graph()
    scored = _score_nodes(G, ["extract"])
    nids = [nid for _, nid in scored]
    assert "n1" in nids
    assert scored[0][1] == "n1"  # highest score first


def test_score_nodes_no_match():
    G = _make_graph()
    scored = _score_nodes(G, ["xyzzy"])
    assert scored == []


def test_score_nodes_source_file_partial():
    G = _make_graph()
    # "cluster.md" contains "cluster" - should score for source match
    scored = _score_nodes(G, ["cluster"])
    nids = [nid for _, nid in scored]
    assert "n2" in nids


def test_score_nodes_ignores_trailing_punctuation():
    G = _make_graph()
    scored = _score_nodes(G, ["extract?"])
    assert scored[0][1] == "n1"


def test_score_nodes_multiword_exact_label_outranks_superset():
    """A multi-word query equal to a whole label must resolve uniquely.

    Regression for the path "No path found" bug: every node sharing the
    query's token set scored identically (no single token equals a multi-word
    label), the tie broke by arbitrary node-id sort, and a wrong/disconnected
    endpoint was chosen. The full-query tier must make the exact label win.
    """
    G = nx.Graph()

    def _add(nid, label, src):
        G.add_node(nid, label=label, norm_label=label.lower(),
                   source_file=src, community=0)

    _add("exact", "UOCE: Dehumidifier Driver", "uoce_dehumidifier.yaml")
    _add("super", "UOCE: Dehumidifier Driver State Machine", "uoce_dehumidifier.yaml")
    _add("decoy", "Dehumidifier Driver Helper", "uoce_dehumidifier.yaml")

    # CLI resolves endpoints as [t.lower() for t in label.split()].
    scored = _score_nodes(G, [t.lower() for t in "UOCE: Dehumidifier Driver".split()])

    assert scored[0][1] == "exact"
    assert scored[0][0] > scored[1][0], "exact label must strictly outrank superset/token-bag matches"


def test_score_nodes_coverage_full_coverage_query_is_unchanged():
    """Coverage scaling must not touch full-coverage queries: a
    single-term identifier lookup keeps the exact tier's full magnitude."""
    G = _make_graph()
    scored = _score_nodes(G, ["extract"])
    w = _compute_idf(G, ["extract"])["extract"]
    assert scored[0][1] == "n1"
    # Full-query exact tier (10x) + per-term exact tier + source hit
    # ("extract" in "extract.md"), all undampened.
    expected = (_EXACT_MATCH_BONUS * 10 + _EXACT_MATCH_BONUS + _SOURCE_MATCH_BONUS) * w
    assert scored[0][0] == pytest.approx(expected)


# --- _query_terms ---

def test_query_terms_strips_search_punctuation():
    # "what" is a question stopword (dropped); punctuation is still stripped from "extract?".
    assert _query_terms("what calls extract?") == ["calls", "extract"]


def test_query_terms_drops_question_stopwords():
    # Natural-language question words are dropped so content words drive seeding:
    # "how does the frontier cache work" must reduce to the content terms, or it
    # seeds on "how"/"the"/"work" (which prefix-match prose labels) instead.
    assert _query_terms("how does the frontier cache work") == ["frontier", "cache"]


def test_query_terms_all_stopwords_falls_back_to_unfiltered():
    # An all-stopword query keeps its terms rather than seeding on nothing.
    assert _query_terms("how does it work") == ["how", "does", "work"]


def test_query_terms_drops_german_question_stopwords():
    # German full-sentence queries must reduce to the content noun.
    assert _query_terms("Wie funktioniert die Authentifizierung?") == ["authentifizierung"]
