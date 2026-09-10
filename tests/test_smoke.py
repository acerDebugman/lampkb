# Vendored-test layer 2: the end-to-end smoke as pytest.
#
# Fast pass drives the kg.py command functions in-process (kg-out/ is
# CWD-relative, so every test chdirs into the tmp corpus). The subprocess pass
# runs the real `uv run kg.py ...` chain end to end.
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import kg
from conftest import write_smoke_chunks


def _run(capsys, func, namespace) -> int:
    rc = func(namespace)
    return rc


def _full_pipeline(capsys, ns, corpus: Path) -> None:
    """prepare → chunks → merge-extraction → build → diagnose → relabel →
    export-html → finalize, all in-process."""
    assert kg.cmd_prepare(ns(root=str(corpus))) == 0
    assert write_smoke_chunks(corpus) == 2
    assert kg.cmd_merge_extraction(ns(root=str(corpus))) == 0
    assert kg.cmd_build(ns(root=str(corpus), directed=False, force=False)) == 0
    assert kg.cmd_diagnose(ns(root=str(corpus), directed=False)) == 0
    out = capsys.readouterr().out
    assert "Graph health: OK" in out
    assert kg.cmd_relabel(ns(root=str(corpus), directed=False,
                             labels='{"0": "Auth Services", "1": "Config and Metrics", "2": "Session Flow"}')) == 0
    assert kg.cmd_export_html(ns(graph=None, no_viz=False)) == 0
    assert kg.cmd_finalize(ns(root=str(corpus))) == 0


def _read_graph(corpus: Path) -> dict:
    return json.loads((corpus / "kg-out" / "graph.json").read_text(encoding="utf-8"))


def test_full_pipeline_in_process(docs_corpus, ns, capsys, monkeypatch):
    monkeypatch.chdir(docs_corpus)
    _full_pipeline(capsys, ns, docs_corpus)

    out_dir = docs_corpus / "kg-out"
    assert (out_dir / "graph.json").exists()
    assert (out_dir / "GRAPH_REPORT.md").exists()
    assert (out_dir / "graph.html").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "cost.json").exists()
    # Temp sidecars cleaned by finalize.
    assert not (out_dir / ".kg_extract.json").exists()
    assert not (out_dir / ".kg_detect.json").exists()

    g = _read_graph(docs_corpus)
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids)) == 8
    assert len(g["links"]) == 9
    assert any(n.get("community_name") for n in g["nodes"]), "relabel must stamp community_name"

    report = (out_dir / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    assert "## God Nodes" in report
    assert "## Surprising Connections" in report


def test_query_path_explain_save_reflect_in_process(docs_corpus, ns, capsys, monkeypatch):
    monkeypatch.chdir(docs_corpus)
    _full_pipeline(capsys, ns, docs_corpus)
    capsys.readouterr()

    assert kg.cmd_vocab(ns()) == 0
    assert "vocab:" in capsys.readouterr().out
    assert (docs_corpus / "kg-out" / ".vocab.txt").exists()

    assert kg.cmd_query(ns(question="config loader", dfs=False, budget=1500,
                           context=[], graph=None)) == 0
    qout = capsys.readouterr().out
    assert "NODE Config Loader" in qout

    assert kg.cmd_path(ns(source="Gamma Audit Log", target="Config Loader",
                          directed=False, undirected=True, graph=None)) == 0
    assert "Shortest path (2 hops)" in capsys.readouterr().out

    assert kg.cmd_explain(ns(node="Config Loader", graph=None)) == 0
    eout = capsys.readouterr().out
    assert "Node: Config Loader" in eout
    assert "Connections (3)" in eout

    assert kg.cmd_save_result(ns(question="How does config reach services?",
                                 answer="Alpha and Beta read the Config Loader directly.",
                                 answer_file=None, type="query",
                                 nodes=["Config Loader", "Alpha Service"],
                                 outcome="useful", correction=None)) == 0
    assert list((docs_corpus / "kg-out" / "memory").glob("query_*.md"))

    assert kg.cmd_reflect(ns(memory_dir=None, out=None, graph=None, analysis=None,
                             labels=None, half_life_days=30.0, min_corroboration=2,
                             if_stale=False)) == 0
    assert "Reflected 1 memories" in capsys.readouterr().out
    # --if-stale skips when LESSONS.md is fresh.
    assert kg.cmd_reflect(ns(memory_dir=None, out=None, graph=None, analysis=None,
                             labels=None, half_life_days=30.0, min_corroboration=2,
                             if_stale=True)) == 0
    assert "already up to date" in capsys.readouterr().out


def test_update_modify_no_duplicates(docs_corpus, ns, capsys, monkeypatch):
    """Modify a file, run the incremental flow, assert replace-on-re-extract."""
    monkeypatch.chdir(docs_corpus)
    _full_pipeline(capsys, ns, docs_corpus)

    beta = docs_corpus / "beta.md"
    beta.write_text(beta.read_text(encoding="utf-8")
                    + "\nBeta now also rotates refresh tokens on every use.\n",
                    encoding="utf-8")

    assert kg.cmd_update_detect(ns(root=str(docs_corpus))) == 0
    uncached = (docs_corpus / "kg-out" / ".kg_uncached.txt").read_text(encoding="utf-8")
    assert str(beta) in uncached.splitlines()
    assert json.loads((docs_corpus / "kg-out" / ".kg_batches.json").read_text())["batches"]

    # Re-extract the changed file (the host agent's chunk).
    b = str(beta)
    chunk = {
        "nodes": [
            {"id": "beta_beta_component", "label": "Beta Component", "entity_type": "document",
             "definition": "", "source_file": b, "source_location": None, "source_url": None,
             "captured_at": None, "author": None, "contributor": None},
            {"id": "beta_session_management", "label": "Session Management", "entity_type": "concept",
             "definition": "", "source_file": b, "source_location": None, "source_url": None,
             "captured_at": None, "author": None, "contributor": None},
            {"id": "beta_token_refresh", "label": "Token Refresh", "entity_type": "procedure",
             "definition": "", "source_file": b, "source_location": None, "source_url": None,
             "captured_at": None, "author": None, "contributor": None},
            {"id": "beta_token_rotation", "label": "Token Rotation", "entity_type": "method",
             "definition": "", "source_file": b, "source_location": None, "source_url": None,
             "captured_at": None, "author": None, "contributor": None},
        ],
        "edges": [
            {"source": "beta_beta_component", "target": "beta_session_management",
             "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0,
             "source_file": b, "source_location": None, "weight": 1.0},
            {"source": "beta_token_refresh", "target": "beta_token_rotation",
             "relation": "顺序", "confidence": "EXTRACTED", "confidence_score": 1.0,
             "source_file": b, "source_location": None, "weight": 1.0},
        ],
        "input_tokens": 0, "output_tokens": 0,
    }
    (docs_corpus / "kg-out" / ".kg_chunk_01.json").write_text(
        json.dumps(chunk, ensure_ascii=False), encoding="utf-8")

    assert kg.cmd_merge_extraction(ns(root=str(docs_corpus))) == 0
    assert kg.cmd_update_merge(ns(root=str(docs_corpus), directed=False)) == 0
    assert kg.cmd_build(ns(root=str(docs_corpus), directed=False, force=False)) == 0
    capsys.readouterr()
    assert kg.cmd_diff(ns(directed=False)) == 0
    dout = capsys.readouterr().out
    assert "new node" in dout
    assert kg.cmd_finalize(ns(root=str(docs_corpus))) == 0

    g = _read_graph(docs_corpus)
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids)), "replace-on-re-extract must not duplicate nodes"
    assert "beta_token_rotation" in ids
    assert ids.count("beta_beta_component") == 1
    # Temp files consumed by diff.
    assert not (docs_corpus / "kg-out" / ".kg_old.json").exists()
    assert not (docs_corpus / "kg-out" / ".kg_incremental.json").exists()


def test_update_delete_shrink_guard_then_force(docs_corpus, ns, capsys, monkeypatch):
    """Deletion update: prune via update-merge, shrink-guard refuses, --force wins."""
    monkeypatch.chdir(docs_corpus)
    _full_pipeline(capsys, ns, docs_corpus)

    (docs_corpus / "docs" / "delta.md").unlink()
    assert kg.cmd_update_detect(ns(root=str(docs_corpus))) == 0
    # Only deletions -> empty extraction created for the prune merge.
    empty_extract = json.loads((docs_corpus / "kg-out" / ".kg_extract.json").read_text())
    assert empty_extract["nodes"] == []

    assert kg.cmd_update_merge(ns(root=str(docs_corpus), directed=False)) == 0
    merged = json.loads((docs_corpus / "kg-out" / ".kg_extract.json").read_text())
    assert not any("delta" in n["id"] for n in merged["nodes"]), "deleted file must be pruned"

    # The shrink-guard refuses the smaller graph...
    capsys.readouterr()
    assert kg.cmd_build(ns(root=str(docs_corpus), directed=False, force=False)) == 1
    assert "refused to shrink" in capsys.readouterr().out
    # ...and --force is the documented override for an intentional shrink.
    assert kg.cmd_build(ns(root=str(docs_corpus), directed=False, force=True)) == 0

    g = _read_graph(docs_corpus)
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids))
    assert not any("delta" in i for i in ids)
    assert "docs_gamma_gamma_audit_log" in ids


def test_empty_extraction_guard(docs_corpus, ns, capsys, monkeypatch):
    """No chunk files -> empty extraction -> build refuses before any write."""
    monkeypatch.chdir(docs_corpus)
    assert kg.cmd_prepare(ns(root=str(docs_corpus))) == 0
    assert kg.cmd_merge_extraction(ns(root=str(docs_corpus))) == 0
    capsys.readouterr()
    assert kg.cmd_build(ns(root=str(docs_corpus), directed=False, force=False)) == 1
    assert "ERROR: Graph is empty" in capsys.readouterr().out
    assert not (docs_corpus / "kg-out" / "graph.json").exists()


# --- subprocess end-to-end ---------------------------------------------------

@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_full_pipeline_subprocess(docs_corpus, scripts_dir):
    kg_py = scripts_dir / "kg.py"

    def run(*argv) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["uv", "run", str(kg_py), *argv],
            cwd=docs_corpus, capture_output=True, text=True, timeout=300,
        )

    r = run("prepare", "--root", ".")
    assert r.returncode == 0, r.stderr
    assert write_smoke_chunks(docs_corpus) == 2
    for argv in (
        ("merge-extraction", "--root", "."),
        ("build", "--root", "."),
        ("diagnose", "--root", "."),
        ("relabel", "--root", ".", "--labels", '{"0": "Auth Services", "1": "Config and Metrics", "2": "Session Flow"}'),
        ("export-html",),
        ("finalize", "--root", "."),
        ("vocab",),
        ("query", "config loader"),
        ("path", "Gamma Audit Log", "Config Loader", "--undirected"),
        ("explain", "Config Loader"),
        ("save-result", "--question", "q", "--answer", "a", "--type", "query",
         "--nodes", "Config Loader", "--outcome", "useful"),
        ("reflect",),
        ("cluster-only", "."),
        ("benchmark",),
    ):
        r = run(*argv)
        assert r.returncode == 0, f"{argv[0]} failed: {r.stderr}\n{r.stdout}"

    out_dir = docs_corpus / "kg-out"
    assert (out_dir / "graph.json").exists()
    assert (out_dir / "GRAPH_REPORT.md").exists()
    assert (out_dir / "graph.html").exists()
