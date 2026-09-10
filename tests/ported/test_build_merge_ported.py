# Ported from graphify/tests/test_build_merge_shrink_guard.py.
# graphify -> kglib; the canned graph layout uses kg-out/ instead of
# graphify-out/ (kg's output-dir name; _infer_merge_root keys on it).
# Hyperedge carry-over cases dropped with the hyperedge mechanism itself.
from __future__ import annotations

import json
from pathlib import Path

import pytest

import kglib.build as buildmod
from kglib.build import build_merge


def _node(i: int, sf: str) -> dict:
    """Deterministic node dict for source file *sf* (semantic-tier shape)."""
    stem = sf.replace("/", "_").replace(".", "_")
    return {
        "id": f"{stem}_n{i}",
        "label": f"{sf} node {i}",
        "entity_type": "document",
        "definition": "",
        "source_file": sf,
        "source_location": f"L{i + 1}",
        "_origin": "ast",
    }


def _write_graph(graph_path: Path, nodes, edges=()) -> None:
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps({"nodes": list(nodes), "edges": list(edges)}),
        encoding="utf-8",
    )


def _seed_12(tmp_path: Path) -> Path:
    """Standard layout, 12 nodes: 10 from a.md + 2 from b.md."""
    gp = tmp_path / "kg-out" / "graph.json"
    _write_graph(
        gp,
        [_node(i, "a.md") for i in range(10)] + [_node(i, "b.md") for i in range(2)],
    )
    return gp


# ── Identity-based shrink guard ────────────────────────────────────────────

def test_legit_prune_driven_reduction_allowed(tmp_path):
    """The guard is ACTIVE with prune_sources, but a prune-explained loss
    passes: 12 nodes, prune b.md -> 10, no raise."""
    gp = _seed_12(tmp_path)
    G = build_merge([], gp, prune_sources=["b.md"], dedup=False)
    assert G.number_of_nodes() == 10
    assert not any(d.get("source_file") == "b.md" for _, d in G.nodes(data=True))


def test_legit_replacement_reduction_allowed(tmp_path):
    """No false-refuse: a.md re-extracted with fewer symbols (10 -> 7)
    legitimately shrinks 12 -> 9."""
    gp = _seed_12(tmp_path)
    chunk = {"nodes": [_node(i, "a.md") for i in range(7)], "edges": []}
    G = build_merge([chunk], gp, prune_sources=None, dedup=False)
    assert G.number_of_nodes() == 9


def test_unexplained_loss_blocked(tmp_path, monkeypatch):
    """A build that drops a node from an UNTOUCHED file (neither re-extracted
    nor pruned this run) must raise instead of silently destroying it — the
    exact failure the dead guard waved through."""
    gp = tmp_path / "kg-out" / "graph.json"
    _write_graph(
        gp,
        [_node(i, "a.md") for i in range(10)]
        + [_node(0, "c.md"), _node(1, "c.md")],
    )
    chunk_for_a = {"nodes": [_node(i, "a.md") for i in range(10)], "edges": []}

    real_build = buildmod.build

    def _broken_build(chunks, **kwargs):
        G = real_build(chunks, **kwargs)
        G.remove_node("c_md_n0")  # untouched c.md node silently lost
        return G

    monkeypatch.setattr(buildmod, "build", _broken_build)
    with pytest.raises(ValueError, match="neither re-extracted nor pruned"):
        build_merge([chunk_for_a], gp, dedup=False)


def test_grow_and_equal_unaffected(tmp_path):
    gp = _seed_12(tmp_path)
    # equal: a.md re-emits its 10 identical nodes -> still 12
    chunk = {"nodes": [_node(i, "a.md") for i in range(10)], "edges": []}
    assert build_merge([chunk], gp, dedup=False).number_of_nodes() == 12
    # grow: a.md re-emits 11 nodes -> 13
    chunk2 = {"nodes": [_node(i, "a.md") for i in range(11)], "edges": []}
    assert build_merge([chunk2], gp, dedup=False).number_of_nodes() == 13


def test_replacement_is_reported_and_own_file_loss_excused(tmp_path, capsys):
    """Visibility: the replace-on-re-extract rebind is announced on
    stderr, and a re-extract that under-produces for its OWN file is excused
    (owned by the extraction layer's incomplete-build guard)."""
    gp = _seed_12(tmp_path)
    chunk = {"nodes": [_node(0, "a.md")], "edges": []}
    G = build_merge([chunk], gp, dedup=False)  # must not raise
    assert G.number_of_nodes() == 3  # 2 b.md + 1 fresh a.md
    assert "Replaced 10 node(s)" in capsys.readouterr().err


# ── Replace wins over delete ───────────────────────────────────────────────

def _seed_two_docs(tmp_path) -> Path:
    graph_path = tmp_path / "kg-out" / "graph.json"
    graph_path.parent.mkdir(parents=True)
    _write_graph(
        graph_path,
        nodes=[
            {"id": "foo_widget_cache", "label": "Widget Cache Design",
             "entity_type": "concept", "definition": "",
             "source_file": "docs/foo.md", "source_location": "L1"},
            {"id": "bar_other", "label": "Other",
             "entity_type": "concept", "definition": "",
             "source_file": "docs/bar.md", "source_location": "L1"},
        ],
        edges=[],
    )
    return graph_path


def test_reextracted_file_in_prune_sources_is_not_deleted(tmp_path):
    """A file present in BOTH new_chunks (re-extracted) and prune_sources
    must be REPLACED, not deleted."""
    graph_path = _seed_two_docs(tmp_path)
    new_chunk = {"nodes": [
        {"id": "foo_widget_cache", "label": "Widget Cache Design",
         "entity_type": "concept", "definition": "",
         "source_file": "docs/foo.md", "source_location": "L2"}
    ], "edges": []}

    G = build_merge([new_chunk], graph_path=str(graph_path),
                    prune_sources=["docs/foo.md"], root=str(tmp_path))
    labels = {G.nodes[n].get("label") for n in G.nodes()}
    assert "Widget Cache Design" in labels, "re-extracted node was wrongly pruned"


def test_genuine_deletion_still_prunes(tmp_path):
    """The guard must not break real deletions: a file in prune_sources but NOT
    in new_chunks is still removed."""
    graph_path = _seed_two_docs(tmp_path)
    new_chunk = {"nodes": [
        {"id": "foo_widget_cache", "label": "Widget Cache Design",
         "entity_type": "concept", "definition": "",
         "source_file": "docs/foo.md", "source_location": "L2"}
    ], "edges": []}
    # bar.md genuinely deleted (not re-extracted)
    G = build_merge([new_chunk], graph_path=str(graph_path),
                    prune_sources=["docs/bar.md"], root=str(tmp_path))
    labels = {G.nodes[n].get("label") for n in G.nodes()}
    assert "Other" not in labels, "genuinely deleted file's node should be pruned"
    assert "Widget Cache Design" in labels


