# Ported from graphify/tests/test_build.py — the generic build_from_json /
# build / edge-data cases. graphify -> kglib.
#
# Dropped as code/AST-only: ghost-merge AST-twin cases (with
# _origin="ast" code nodes), old-stem alias cases (C/C++ include fallback),
# cross-language phantom-edge guards, the two-tier AST/semantic
# build_merge layer tests, MCP node-id tests, and
# test_build_merge_preserves_call_edge_direction (imports the JS extractor).
# The count/attr cases run against tests/fixtures/extraction_docs.json (the
# upstream fixtures/extraction.json is code-flavored; counts adapted).
import json
from pathlib import Path

import networkx as nx
import pytest
from networkx.readwrite import json_graph

from kglib.build import (
    build_from_json, build, build_merge, edge_data, edge_datas,
    dedupe_edges, dedupe_nodes,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_extraction():
    return json.loads((FIXTURES / "extraction_docs.json").read_text())


# --- dedupe helpers ----------------------------------------------------------

def test_dedupe_edges_collapses_exact_parallels():
    edges = [
        {"source": "a", "target": "b", "relation": "references", "source_location": "L1"},
        {"source": "a", "target": "b", "relation": "references", "source_location": "L9"},  # dup
        {"source": "a", "target": "b", "relation": "cites"},  # different relation: kept
        {"source": "b", "target": "c", "relation": "references"},
    ]
    out = dedupe_edges(edges)
    keys = [(e["source"], e["target"], e["relation"]) for e in out]
    assert keys == [("a", "b", "references"), ("a", "b", "cites"), ("b", "c", "references")]
    # first occurrence wins (keeps L1, not L9)
    assert out[0]["source_location"] == "L1"


def test_dedupe_edges_is_idempotent():
    edges = [
        {"source": "a", "target": "b", "relation": "references"},
        {"source": "a", "target": "b", "relation": "references"},
    ]
    once = dedupe_edges(edges)
    twice = dedupe_edges(once + edges)  # simulate a second update re-concatenating
    assert len(once) == 1
    assert len(twice) == 1


def test_dedupe_nodes_collapses_by_id_last_wins():
    # A shared anchor is emitted once per importing file; the raw writer
    # must collapse same-id node dicts.
    nodes = [
        {"id": "guide", "label": "Guide", "type": "module", "source_file": "A.md"},
        {"id": "intro", "label": "Intro", "file_type": "document"},
        {"id": "guide", "label": "Guide", "type": "module", "source_file": "B.md"},
    ]
    out = dedupe_nodes(nodes)
    ids = [n["id"] for n in out]
    assert ids == ["guide", "intro"]  # first-appearance order
    # last writer wins on attributes
    assert next(n for n in out if n["id"] == "guide")["source_file"] == "B.md"


# --- fixture build -----------------------------------------------------------

def test_build_from_json_node_count():
    G = build_from_json(load_extraction())
    assert G.number_of_nodes() == 17


def test_build_from_json_edge_count():
    G = build_from_json(load_extraction())
    assert G.number_of_edges() == 18


def test_nodes_have_label():
    G = build_from_json(load_extraction())
    assert G.nodes["papers_attention_attention_is_all_you_need"]["label"] == "Attention Is All You Need"


def test_edges_have_confidence():
    G = build_from_json(load_extraction())
    data = G.edges["notes_glossary_embedding", "docs_design_embedding_layer"]
    assert data["confidence"] == "INFERRED"


def test_ambiguous_edge_preserved():
    G = build_from_json(load_extraction())
    data = G.edges["notes_research_residual_connection", "papers_attention_positional_encoding"]
    assert data["confidence"] == "AMBIGUOUS"


# --- edge weight normalization -----------------------------------------------

def test_null_weight_edge_builds_and_clusters(tmp_path):
    """An explicit ``"weight": null`` (JSON null -> None) used to survive
    ``.get("weight", 1.0)`` and crash Louvain/Leiden modularity with a TypeError.
    It must now coerce to the 1.0 default, build, and cluster without raising."""
    from kglib.cluster import cluster
    extraction = {
        "nodes": [
            {"id": "a", "label": "A", "file_type": "document", "source_file": "a.md"},
            {"id": "b", "label": "B", "file_type": "document", "source_file": "b.md"},
            {"id": "c", "label": "C", "file_type": "document", "source_file": "c.md"},
        ],
        "edges": [
            {"source": "a", "target": "b", "relation": "references", "weight": None,
             "confidence_score": None},
            {"source": "b", "target": "c", "relation": "references", "weight": 2.5},
        ],
    }
    G = build_from_json(extraction)
    assert G["a"]["b"]["weight"] == 1.0            # null coerced to default
    assert G["a"]["b"]["confidence_score"] == 1.0  # null confidence_score too
    assert G["b"]["c"]["weight"] == 2.5            # a valid weight is preserved
    cluster(G)  # must not raise (Louvain/Leiden modularity)


def test_malformed_weights_normalize():
    """Non-numeric / NaN / inf / negative weights fall back to 1.0 (the backends
    reject them); a missing weight key is left absent (backends default it)."""
    extraction = {
        "nodes": [{"id": f"n{i}", "label": str(i), "file_type": "document",
                   "source_file": f"{i}.md"} for i in range(4)],
        "edges": [
            {"source": "n0", "target": "n1", "relation": "references", "weight": "3.5"},
            {"source": "n1", "target": "n2", "relation": "references", "weight": float("nan")},
            {"source": "n2", "target": "n3", "relation": "references", "weight": -4},
        ],
    }
    G = build_from_json(extraction)
    assert G["n0"]["n1"]["weight"] == 3.5     # numeric string coerces
    assert G["n1"]["n2"]["weight"] == 1.0     # NaN -> default
    assert G["n2"]["n3"]["weight"] == 1.0     # negative -> default


# --- legacy schema canonicalization -------------------------------------------

def test_legacy_node_source_canonicalized():
    """Legacy 'source' key on nodes is renamed to 'source_file' before graph build."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "document", "source": "a.md"}],
           "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert "source_file" in G.nodes["n1"]
    assert G.nodes["n1"]["source_file"] == "a.md"
    assert "source" not in G.nodes["n1"]


def test_legacy_edge_from_to_canonicalized():
    """Legacy 'from'/'to' keys on edges are accepted alongside 'source'/'target'."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "document", "source_file": "a.md"},
                     {"id": "n2", "label": "B", "file_type": "document", "source_file": "b.md"}],
           "edges": [{"from": "n1", "to": "n2", "relation": "references",
                      "confidence": "EXTRACTED", "source_file": "a.md", "weight": 1.0}],
           "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert G.number_of_edges() == 1


def test_legacy_node_name_path_aliases_folded():
    """Nodes carrying `name`/`path` instead of `label`/`source_file` must
    be canonicalized before validation, not enter the graph as label-less
    ghosts. After build the canonicalized dict also passes validation."""
    from kglib.validate import validate_extraction
    ext = {"nodes": [{"id": "n1", "name": "Foo", "path": "a/b.md", "file_type": "concept"}],
           "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    attrs = G.nodes["n1"]
    assert attrs["label"] == "Foo"
    assert attrs["source_file"] == "a/b.md"
    assert "name" not in attrs
    assert "path" not in attrs
    # build_from_json canonicalizes in place; the extraction dict must now be
    # schema-valid (no missing-field errors for the alias node).
    assert not [e for e in validate_extraction(ext) if "missing required field" in e]


def test_legacy_edge_type_confidence_score_aliases_folded():
    """Edges carrying `type`/`confidence_score` instead of
    `relation`/`confidence` fold to canonical fields. Recovery confidence is
    INFERRED (never EXTRACTED — alias recovery is not provenance) and the
    companion confidence_score float is retained, not popped."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "document", "source_file": "a.md"},
                     {"id": "n2", "label": "B", "file_type": "document", "source_file": "b.md"}],
           "edges": [{"source": "n1", "target": "n2", "type": "references",
                      "confidence_score": 0.9, "source_file": "a.md"}],
           "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    data = edge_data(G, "n1", "n2")
    assert data["relation"] == "references"
    assert data["confidence"] == "INFERRED"
    assert data["confidence_score"] == 0.9
    assert "type" not in data


def test_node_alias_canonical_field_wins():
    """When both the canonical field and its alias are present, the
    canonical value wins and the alias key is left untouched."""
    ext = {"nodes": [{"id": "n1", "label": "Real", "name": "Alias",
                      "file_type": "document", "source_file": "a.md"}],
           "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert G.nodes["n1"]["label"] == "Real"
    assert G.nodes["n1"]["name"] == "Alias"  # preserved, not consumed


def test_alias_node_gets_nonempty_norm_label(tmp_path):
    """A recovered alias node must serialize with a non-empty norm_label
    so query/explain can find it."""
    from kglib.export import to_json
    ext = {"nodes": [{"id": "n1", "name": "Foo", "path": "a/b.md", "file_type": "concept"}],
           "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    out = tmp_path / "graph.json"
    assert to_json(G, {}, str(out))
    data = json.loads(out.read_text())
    node = next(n for n in data["nodes"] if n["id"] == "n1")
    assert node["norm_label"] == "foo"


def test_extraction_warning_breakdown_by_cause(capsys):
    """A mixed batch of schema errors must report per-cause counts, not
    just the first error."""
    ext = {"nodes": [
        {"id": "n1", "label": "A", "file_type": "document", "source_file": "a.md"},
        {"id": "n2", "label": "B", "file_type": "document", "source_file": "b.md"},
        # two nodes missing label (and carrying no name alias)
        {"id": "x1", "file_type": "document", "source_file": "x.md"},
        {"id": "x2", "file_type": "document", "source_file": "x.md"},
    ], "edges": [
        # three edges missing relation (and carrying no type alias)
        {"source": "n1", "target": "n2", "confidence": "EXTRACTED", "source_file": "a.md"},
        {"source": "n2", "target": "n1", "confidence": "EXTRACTED", "source_file": "a.md"},
        {"source": "n1", "target": "x1", "confidence": "EXTRACTED", "source_file": "a.md"},
    ], "input_tokens": 0, "output_tokens": 0}
    build_from_json(ext)
    err = capsys.readouterr().err
    assert "2x missing required field 'label'" in err
    assert "3x missing required field 'relation'" in err


# --- semantic id re-key --------------------------------------------------------

def test_absolute_derived_semantic_ids_rekeyed(tmp_path):
    """A semantic fragment whose ids were derived from an ABSOLUTE
    source_file (Windows detect() emits them) must re-key to the canonical
    repo-relative stem instead of ghosting against the existing graph."""
    from kglib.ids import make_id
    (tmp_path / "docs").mkdir()
    abs_sf = str(tmp_path / "docs" / "DATAFLOW.md")
    abs_stem = make_id(str(tmp_path / "docs" / "DATAFLOW"))
    ext = {"nodes": [
        {"id": abs_stem, "label": "DATAFLOW.md", "file_type": "document",
         "source_file": abs_sf},
        {"id": f"{abs_stem}_pipeline", "label": "Pipeline", "file_type": "concept",
         "source_file": abs_sf},
    ], "edges": [
        {"source": abs_stem, "target": f"{abs_stem}_pipeline", "relation": "describes",
         "confidence": "INFERRED", "source_file": abs_sf, "weight": 1.0},
    ], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext, root=tmp_path)
    assert "docs_dataflow" in G.nodes
    assert "docs_dataflow_pipeline" in G.nodes
    assert abs_stem not in G.nodes
    assert G.nodes["docs_dataflow"]["source_file"] == "docs/DATAFLOW.md"
    assert G.has_edge("docs_dataflow", "docs_dataflow_pipeline")


def test_absolute_derived_semantic_ids_rekeyed_backslash(tmp_path):
    """Separator variant: the same absolute-derived-id fragment with
    backslash separators in source_file re-keys identically."""
    from kglib.ids import make_id
    (tmp_path / "docs").mkdir()
    abs_sf = str(tmp_path / "docs" / "DATAFLOW.md").replace("/", "\\")
    abs_stem = make_id(str(tmp_path / "docs" / "DATAFLOW"))
    ext = {"nodes": [
        {"id": f"{abs_stem}_pipeline", "label": "Pipeline", "file_type": "concept",
         "source_file": abs_sf},
    ], "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext, root=tmp_path)
    assert "docs_dataflow_pipeline" in G.nodes
    assert G.nodes["docs_dataflow_pipeline"]["source_file"] == "docs/DATAFLOW.md"


def test_semantic_rekey_relative_vs_absolute_source_file():
    """Re-key contract: a relative source_file is migrated; an absolute one is left
    untouched (it can't be relativized, so its on-disk path must not leak into IDs)."""
    from kglib.build import _semantic_id_remap
    rel = [{"id": "api_readme", "source_file": "docs/v1/api/README.md", "type": "document"}]
    assert _semantic_id_remap(rel, ".") == {"api_readme": "docs_v1_api_readme"}
    # absolute path with no resolvable root → skipped, not remapped to an abs-path id
    ab = [{"id": "api_readme", "source_file": "/abs/docs/v1/api/README.md", "type": "document"}]
    assert _semantic_id_remap(ab, None) == {}


# --- path handling -------------------------------------------------------------

def test_source_file_backslash_normalized():
    """Windows backslash paths and POSIX paths for the same file must produce one node."""
    extraction = {
        "nodes": [
            {"id": "n1", "label": "A", "file_type": "document", "source_file": "src\\middleware\\auth.md"},
            {"id": "n2", "label": "B", "file_type": "document", "source_file": "src/middleware/auth.md"},
        ],
        "edges": [],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(extraction)
    sources = {G.nodes[n]["source_file"] for n in G.nodes()}
    assert sources == {"src/middleware/auth.md"}


def test_edge_missing_source_file_backfilled_from_node():
    """A semantic/LLM edge lacking source_file must inherit it from its
    source node rather than reach graph.json with no file reference."""
    extraction = {
        "nodes": [
            {"id": "n1", "label": "A", "file_type": "concept", "source_file": "docs/a.md"},
            {"id": "n2", "label": "B", "file_type": "concept", "source_file": "docs/b.md"},
        ],
        # No source_file on the edge (as LLM output sometimes omits it).
        "edges": [{"source": "n1", "target": "n2", "relation": "relates_to", "confidence": "INFERRED"}],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(extraction)
    sf = edge_data(G, "n1", "n2").get("source_file")
    assert sf == "docs/a.md"  # backfilled from the source node


def test_build_merges_multiple_extractions():
    ext1 = {"nodes": [{"id": "n1", "label": "A", "file_type": "document", "source_file": "a.md"}],
            "edges": [], "input_tokens": 0, "output_tokens": 0}
    ext2 = {"nodes": [{"id": "n2", "label": "B", "file_type": "document", "source_file": "b.md"}],
            "edges": [{"source": "n1", "target": "n2", "relation": "references",
                       "confidence": "INFERRED", "source_file": "b.md", "weight": 1.0}],
            "input_tokens": 0, "output_tokens": 0}
    G = build([ext1, ext2])
    assert G.number_of_nodes() == 2
    assert G.number_of_edges() == 1


def test_build_from_json_relativizes_absolute_source_file(tmp_path):
    """Semantic subagents emit absolute source_file paths; build_from_json must
    relativize them to root so query traversal works correctly."""
    root = tmp_path / "myproject"
    root.mkdir()
    abs_path = str(root / "docs" / "overview.md")
    extraction = {
        "nodes": [
            {"id": "overview_intro", "label": "Intro", "source_file": abs_path, "file_type": "document"},
        ],
        "edges": [
            {"source": "overview_intro", "target": "overview_intro",
             "relation": "self", "confidence": "EXTRACTED", "confidence_score": 1.0,
             "source_file": abs_path},
        ],
    }
    G = build_from_json(extraction, root=root)
    # The id-stem migration re-keys the old short id to the full-path form.
    sf = G.nodes["docs_overview_intro"]["source_file"]
    assert not sf.startswith("/"), f"source_file still absolute: {sf}"
    assert sf == "docs/overview.md"


def test_build_from_json_relative_source_file_unchanged(tmp_path):
    """Already-relative source_file paths must not be modified."""
    extraction = {
        "nodes": [{"id": "foo_bar", "label": "bar", "source_file": "src/foo.md", "file_type": "document"}],
        "edges": [],
    }
    G = build_from_json(extraction, root=tmp_path)
    # source_file must be untouched; the id is re-keyed to the full-path form.
    assert G.nodes["src_foo_bar"]["source_file"] == "src/foo.md"


# --- entity_type canonicalization -------------------------------------------------

def test_none_entity_type_defaults_to_concept(capsys):
    """entity_type=None is defaulted to 'concept' before validation, so no
    spurious 'invalid entity_type None' warning fires."""
    ext = {
        "nodes": [
            {"id": "n1", "label": "Stub", "entity_type": None, "definition": "", "source_file": "a.md"},
            {"id": "n2", "label": "Real", "entity_type": "document", "definition": "", "source_file": "b.md"},
        ],
        "edges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }
    G = build_from_json(ext)
    err = capsys.readouterr().err
    assert "invalid entity_type" not in err
    assert G.nodes["n1"]["entity_type"] == "concept"
    assert G.nodes["n2"]["entity_type"] == "document"


def test_missing_entity_type_defaults_to_concept(capsys):
    """Nodes missing entity_type entirely are also canonicalized to 'concept'."""
    ext = {
        "nodes": [
            {"id": "n1", "label": "Bare", "definition": "", "source_file": "a.md"},
        ],
        "edges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }
    G = build_from_json(ext)
    err = capsys.readouterr().err
    assert "invalid entity_type" not in err
    assert "missing required field 'entity_type'" not in err
    assert G.nodes["n1"]["entity_type"] == "concept"


def test_invalid_entity_type_reported_not_coerced(capsys):
    """Unknown entity_type values are no longer silently coerced to 'concept':
    the value passes through unchanged and validation reports it."""
    ext = {
        "nodes": [
            {"id": "n1", "label": "Bad", "entity_type": "weird_type", "definition": "", "source_file": "a.md"},
        ],
        "edges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }
    G = build_from_json(ext)
    err = capsys.readouterr().err
    assert "invalid entity_type 'weird_type'" in err
    assert G.nodes["n1"]["entity_type"] == "weird_type"


def test_entity_type_synonym_mapping():
    """Chinese type names from the prompt's type table and near-miss English
    drift forms map to their canonical keys."""
    ext = {
        "nodes": [
            {"id": "n1", "label": "Elm", "entity_type": "概念", "definition": "", "source_file": "a.md"},
            {"id": "n2", "label": "Met", "entity_type": "方法", "definition": "", "source_file": "b.md"},
            {"id": "n3", "label": "Scn", "entity_type": "scenarios", "definition": "", "source_file": "c.md"},
        ],
        "edges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }
    G = build_from_json(ext)
    assert G.nodes["n1"]["entity_type"] == "concept"
    assert G.nodes["n2"]["entity_type"] == "method"
    assert G.nodes["n3"]["entity_type"] == "scenario"


# --- ghost merge, semantic-tier cases (docs-relevant: same-file LLM duplicates) ---

def test_ghost_merge_not_across_directories_same_basename():
    """Two unrelated non-AST nodes with the same basename+label in
    DIFFERENT directories must NOT be merged onto one survivor (the bug: bare
    basename collapsed docs/product_a/index.md and docs/product_b/index.md)."""
    ext = {
        "nodes": [
            {"id": "docs_a_index", "label": "Quickstart", "file_type": "document",
             "source_file": "docs/product_a/index.md", "source_location": "L1"},
            {"id": "docs_b_index", "label": "Quickstart", "file_type": "document",
             "source_file": "docs/product_b/index.md"},
            {"id": "docs_hub", "label": "Docs", "file_type": "concept",
             "source_file": "docs/hub.md", "source_location": "L1"},
        ],
        "edges": [{"source": "docs_hub", "target": "docs_b_index", "relation": "links_to",
                   "confidence": "INFERRED", "source_file": "docs/hub.md"}],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(ext, directed=False)
    # Both docs survive; the edge stays on the file it was authored against.
    assert "docs_a_index" in G.nodes() and "docs_b_index" in G.nodes()
    assert G.has_edge("docs_hub", "docs_b_index")
    assert not G.has_edge("docs_hub", "docs_a_index")


def test_ghost_merge_non_ast_different_files_both_survive():
    """Two NON-AST (semantic) nodes sharing (basename, label) but from
    DIFFERENT files are distinct concepts with no canonical twin. They must
    not be merged into an arbitrary survivor; both survive."""
    ext = {
        "nodes": [
            {"id": "dir_a_update_build_merge", "label": "build_merge() function",
             "file_type": "concept", "source_file": "dir_a/update.md", "source_location": "L10"},
            {"id": "dir_b_update_build_merge", "label": "build_merge() function",
             "file_type": "concept", "source_file": "dir_b/update.md", "source_location": "L12"},
        ],
        "edges": [],
    }
    G = build_from_json(ext, directed=False)
    assert sorted(G.nodes()) == ["dir_a_update_build_merge", "dir_b_update_build_merge"]


def test_ghost_merge_non_ast_same_file_still_merges():
    """A genuine duplicate — two non-AST nodes with the SAME source_file and
    label — is a real ghost and still collapses to one node (deterministically),
    so the ghost-merge fix doesn't leave same-file LLM duplicates behind."""
    ext = {
        "nodes": [
            {"id": "a_foo", "label": "Foo", "file_type": "concept",
             "source_file": "x/doc.md", "source_location": "L1"},
            {"id": "b_foo", "label": "Foo", "file_type": "concept",
             "source_file": "x/doc.md", "source_location": "L2"},
        ],
        "edges": [],
    }
    G = build_from_json(ext, directed=False)
    assert G.number_of_nodes() == 1


# --- build_merge directed-flag inheritance -------------------------------------

def test_build_merge_inherits_directed_flag_from_disk(tmp_path):
    """build_merge with no explicit `directed=` must honor the on-disk graph's
    own `directed` flag instead of silently defaulting to False."""
    ext = {
        "nodes": [{"id": "a", "label": "a", "file_type": "concept",
                   "source_file": "x.md", "source_location": "L1"}],
        "edges": [],
    }
    from kglib.export import to_json

    graph_path = tmp_path / "graph.json"

    # Directed graph on disk -> no directed= kwarg -> stays directed.
    G1 = build([ext], directed=True, dedup=False)
    assert to_json(G1, {}, str(graph_path), force=True)
    G2 = build_merge([], graph_path, dedup=False)
    assert G2.is_directed() is True
    saved = json.loads(graph_path.read_text())
    assert saved.get("directed") is True

    # Undirected graph on disk -> no directed= kwarg -> stays undirected.
    G3 = build([ext], directed=False, dedup=False)
    assert to_json(G3, {}, str(graph_path), force=True)
    G4 = build_merge([], graph_path, dedup=False)
    assert G4.is_directed() is False


def test_build_merge_fresh_graph_defaults_undirected(tmp_path):
    """No existing graph.json + no directed= kwarg -> falls back to False."""
    graph_path = tmp_path / "does_not_exist.json"
    G = build_merge([], graph_path, dedup=False)
    assert G.is_directed() is False


def test_build_merge_explicit_directed_overrides_disk_flag(tmp_path):
    """An explicit directed=True/False from the caller must still win over
    whatever is stored on disk."""
    ext = {
        "nodes": [{"id": "a", "label": "a", "file_type": "concept",
                   "source_file": "x.md", "source_location": "L1"}],
        "edges": [],
    }
    from kglib.export import to_json

    graph_path = tmp_path / "graph.json"

    # Directed on disk, explicit directed=False -> caller wins.
    G1 = build([ext], directed=True, dedup=False)
    assert to_json(G1, {}, str(graph_path), force=True)
    G2 = build_merge([], graph_path, directed=False, dedup=False)
    assert G2.is_directed() is False

    # Undirected on disk, explicit directed=True -> caller wins.
    G3 = build([ext], directed=False, dedup=False)
    assert to_json(G3, {}, str(graph_path), force=True)
    G4 = build_merge([], graph_path, directed=True, dedup=False)
    assert G4.is_directed() is True


def test_build_from_json_preserves_first_direction_on_bidirectional_pair(tmp_path):
    """Regression: two edges between the same pair in opposite
    directions collapse to one undirected edge; the surviving edge must keep
    the FIRST-seen direction, not the lexicographically-later one."""
    from kglib.export import to_json

    extraction = {
        "nodes": [
            {"id": "a_handler", "label": "a", "file_type": "document", "source_file": "a.md"},
            {"id": "z_emitter", "label": "z", "file_type": "document", "source_file": "z.md"},
        ],
        "edges": [
            {"source": "a_handler", "target": "z_emitter", "relation": "cites",
             "confidence": "EXTRACTED", "source_file": "a.md"},
            {"source": "z_emitter", "target": "a_handler", "relation": "cites",
             "confidence": "EXTRACTED", "source_file": "z.md"},
        ],
        "input_tokens": 0,
        "output_tokens": 0,
    }
    G = build_from_json(extraction)
    assert G.number_of_edges() == 1
    data = edge_data(G, "a_handler", "z_emitter")
    assert data["_src"] == "a_handler"
    assert data["_tgt"] == "z_emitter"

    graph_path = tmp_path / "graph.json"
    assert to_json(G, {}, str(graph_path), force=True)
    saved = json.loads(graph_path.read_text())
    saved_cites = [e for e in saved.get("links", saved.get("edges", []))
                   if e.get("relation") == "cites"]
    assert len(saved_cites) == 1
    assert saved_cites[0]["source"] == "a_handler"
    assert saved_cites[0]["target"] == "z_emitter"


# --- edge_data / edge_datas -----------------------------------------------------

def test_edge_data_simple_graph():
    G = nx.Graph()
    G.add_edge("a", "b", relation="references", confidence="EXTRACTED")
    d = edge_data(G, "a", "b")
    assert isinstance(d, dict)
    assert d["relation"] == "references"
    assert d["confidence"] == "EXTRACTED"


def test_edge_datas_simple_graph_returns_singleton_list():
    G = nx.Graph()
    G.add_edge("a", "b", relation="references", confidence="EXTRACTED")
    ds = edge_datas(G, "a", "b")
    assert isinstance(ds, list)
    assert len(ds) == 1
    assert ds[0]["relation"] == "references"


def test_edge_data_multigraph_with_parallel_edges():
    G = nx.MultiGraph()
    G.add_edge("a", "b", relation="cites", confidence="EXTRACTED")
    G.add_edge("a", "b", relation="references", confidence="INFERRED")
    d = edge_data(G, "a", "b")
    assert isinstance(d, dict)
    assert d.get("relation") in ("cites", "references")


def test_edge_datas_multigraph_returns_all_parallel_edges():
    G = nx.MultiGraph()
    G.add_edge("a", "b", relation="cites", confidence="EXTRACTED")
    G.add_edge("a", "b", relation="references", confidence="INFERRED")
    ds = edge_datas(G, "a", "b")
    assert isinstance(ds, list)
    assert len(ds) == 2
    relations = {e.get("relation") for e in ds}
    assert relations == {"cites", "references"}


def test_edge_data_multidigraph():
    G = nx.MultiDiGraph()
    G.add_edge("a", "b", relation="cites")
    G.add_edge("a", "b", relation="references")
    d = edge_data(G, "a", "b")
    assert isinstance(d, dict)
    assert d.get("relation") in ("cites", "references")
    ds = edge_datas(G, "a", "b")
    assert len(ds) == 2


def test_edge_data_node_link_multigraph_roundtrip():
    """A node_link JSON with multigraph: true must load as MultiGraph and the
    helpers must operate on it without raising the 3-tuple unpack ValueError."""
    data = {
        "directed": False,
        "multigraph": True,
        "graph": {},
        "nodes": [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
        ],
        "links": [
            {"source": "a", "target": "b", "relation": "cites", "confidence": "EXTRACTED"},
            {"source": "a", "target": "b", "relation": "references", "confidence": "INFERRED"},
        ],
    }
    try:
        G = json_graph.node_link_graph(data, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(data)
    assert isinstance(G, nx.MultiGraph)
    d = edge_data(G, "a", "b")
    assert isinstance(d, dict)
    assert d.get("relation") in ("cites", "references")
    ds = edge_datas(G, "a", "b")
    assert len(ds) == 2


# --- malformed input tolerance ----------------------------------------------------

def test_build_from_json_skips_non_hashable_node_id():
    # A malformed LLM extraction can emit a list-valued id; build_from_json must
    # skip it (NetworkX add_node would otherwise raise unhashable type) and still
    # build the graph from the well-formed nodes.
    extraction = {
        "nodes": [
            {"id": "a", "label": "A", "file_type": "document", "source_file": "a.md"},
            {"id": ["x", "y"], "label": "B", "file_type": "document", "source_file": "b.md"},
            {"label": "C", "file_type": "document", "source_file": "c.md"},  # missing id
        ],
        "edges": [],
    }
    G = build_from_json(extraction)
    assert set(G.nodes()) == {"a"}


def test_build_from_json_skips_edge_with_non_hashable_endpoint():
    # A list-valued edge endpoint must be skipped rather than crash the
    # `not in node_set` membership test. The well-formed edge survives.
    extraction = {
        "nodes": [
            {"id": "a", "label": "A", "file_type": "document", "source_file": "a.md"},
            {"id": "b", "label": "B", "file_type": "document", "source_file": "b.md"},
        ],
        "edges": [
            {"source": "a", "target": ["b", "c"], "relation": "references",
             "confidence": "INFERRED", "source_file": "a.md"},
            {"source": "a", "target": "b", "relation": "cites",
             "confidence": "EXTRACTED", "source_file": "a.md"},
        ],
    }
    G = build_from_json(extraction)
    assert G.number_of_nodes() == 2
    assert G.number_of_edges() == 1
    assert G.has_edge("a", "b")


# --- legacy-id detection --------------------------------------------------------

def test_graph_has_legacy_ids_detects_old_scheme():
    """The read-only-consumer nudge (query/serve) flags a legacy graph and
    leaves a canonical one alone."""
    from kglib.build import graph_has_legacy_ids
    old = [{"id": "api_readme", "source_file": "docs/v1/api/README.md", "type": "document", "source_location": "L1"}]
    new = [{"id": "docs_v1_api_readme", "source_file": "docs/v1/api/README.md", "type": "document", "source_location": "L1"}]
    assert graph_has_legacy_ids(old, root=".") is True
    assert graph_has_legacy_ids(new, root=".") is False
    # sourceless / top-level file nodes don't false-positive
    assert graph_has_legacy_ids([{"id": "setup", "source_file": "setup.md", "source_location": "L1"}], root=".") is False
    assert graph_has_legacy_ids([{"id": "x", "label": "y"}], root=".") is False


# --- doc-twin merge (documents-specific) -----------------------------------------

def test_markdown_doc_twin_merges_into_semantic_doc_node():
    """A markdown quick-scan's bare `<slug>` doc node and the semantic
    `<slug>_doc` node for the same file must collapse to one node, with edges
    consolidated — otherwise a document is two disconnected halves."""
    ext = {
        "nodes": [
            {"id": "docs_readme_doc", "label": "README", "file_type": "document",
             "source_file": "docs/readme.md", "source_location": "L1"},
            {"id": "docs_readme", "label": "readme.md", "file_type": "document",
             "source_file": "docs/readme.md", "source_location": "L1"},
            {"id": "concept_auth", "label": "auth", "file_type": "concept",
             "source_file": "auth.md", "source_location": "L1"},
            {"id": "docs_guide", "label": "guide.md", "file_type": "document",
             "source_file": "docs/guide.md", "source_location": "L1"},
        ],
        "edges": [
            {"source": "docs_readme_doc", "target": "concept_auth", "relation": "references",
             "source_file": "docs/readme.md", "confidence": "INFERRED", "weight": 1.0},
            {"source": "docs_guide", "target": "docs_readme", "relation": "references",
             "source_file": "docs/guide.md", "confidence": "EXTRACTED", "weight": 1.0},
        ],
    }
    G = build_from_json(ext, directed=False)
    assert "docs_readme" not in G.nodes()          # bare twin merged away
    assert "docs_readme_doc" in G.nodes()           # semantic node is canonical
    assert G.has_edge("docs_guide", "docs_readme_doc")   # quick-scan edge repointed
    assert G.has_edge("docs_readme_doc", "concept_auth")  # semantic edge kept


def test_doc_twin_merge_does_not_touch_non_document_nodes():
    """Doc-twin guard: a concept `foo` and an unrelated `foo_doc` (not
    file_type=document) must NOT merge, even sharing a source_file."""
    ext = {
        "nodes": [
            {"id": "m_foo", "label": "foo", "file_type": "concept",
             "source_file": "m.md", "source_location": "L1"},
            {"id": "m_foo_doc", "label": "foo rationale", "file_type": "rationale",
             "source_file": "m.md", "source_location": "L2"},
        ],
        "edges": [],
    }
    G = build_from_json(ext, directed=False)
    assert {"m_foo", "m_foo_doc"} <= set(G.nodes())


# --- foreign-absolute source_file portability -------------------------------------

FOREIGN_ABSOLUTE_SOURCE_FILES = [
    "/home/ci/build/repo/docs/api/README.md",   # POSIX-absolute (Linux/Docker build)
    "C:/Users/u/repo/docs/api/README.md",       # Windows-absolute, forward slashes
]


@pytest.mark.parametrize("sf", FOREIGN_ABSOLUTE_SOURCE_FILES)
def test_semantic_rekey_skips_absolute_from_either_platform(sf):
    """An absolute source_file is left alone whichever OS wrote it."""
    from kglib.build import _semantic_id_remap
    nodes = [{"id": "api_readme", "source_file": sf, "type": "document"}]
    assert _semantic_id_remap(nodes, None) == {}, (
        f"{sf!r} leaked its on-disk path into the node ID"
    )


@pytest.mark.parametrize("sf", FOREIGN_ABSOLUTE_SOURCE_FILES)
def test_graph_has_legacy_ids_skips_absolute_from_either_platform(sf):
    """The legacy-ID probe derives a stem from source_file, so it must skip an
    absolute path rather than mint a stem out of the whole build directory."""
    from kglib.build import graph_has_legacy_ids
    nodes = [{"id": "api_readme", "source_file": sf,
              "type": "document", "source_location": "L1"}]
    assert graph_has_legacy_ids(nodes, root=None) is False


def test_norm_source_file_relativizes_a_posix_absolute_path():
    """A Linux-built graph's absolute source_file must relativize against the
    matching root regardless of the host running the update."""
    from kglib.build import _norm_source_file
    assert _norm_source_file(
        "/home/ci/build/repo/docs/api/README.md", "/home/ci/build/repo"
    ) == "docs/api/README.md"


def test_derive_prune_root_recovers_root_from_posix_absolute_prune_sources():
    """The prune-root recovery skips any prune source it thinks is relative;
    with a host-only absoluteness test, POSIX-absolute prune sources were
    skipped on Windows and prune silently no-opped."""
    from kglib.build import _derive_prune_root
    stored = {"docs/a.md", "/home/ci/build/repo/docs/b.md"}
    assert _derive_prune_root(
        ["/home/ci/build/repo/docs/a.md"], stored
    ) == "/home/ci/build/repo"
