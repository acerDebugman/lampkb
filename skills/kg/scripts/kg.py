# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "networkx",
#     "rapidfuzz",
#     "datasketch",
#     "pypdf",
#     "jieba",
# ]
# ///
"""kg — documents-only knowledge-graph tool.

Single entry point for the /kg skill. Vendors a slimmed, documents-only
subset of graphify as the ``kglib`` package next to this file. Run as:

    uv run kg.py <subcommand> ...

All commands operate on ``kg-out/`` under the CURRENT working directory.
Semantic extraction itself is NOT done here — the host agent reads the
corpus and writes kg-out/.kg_chunk_NN.json files; this tool does
everything deterministic around that (detect, cache, merge, build, cluster,
analyze, export, query, reflect).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kglib.paths import KG_OUT  # noqa: E402

_SEM_TYPES = ("document", "paper")


def _out() -> Path:
    return Path(KG_OUT)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj, *, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def cmd_prepare(args) -> int:
    """Step 2 (detect) + Step B0 (semantic cache check) + Step B1 (batch plan)."""
    from kglib.detect import detect

    root = Path(args.root)
    if not root.exists():
        _err(f"error: path not found: {root}")
        return 1
    out = _out()
    out.mkdir(parents=True, exist_ok=True)

    result = detect(root)
    # Write the sidecar from Python, not a shell redirect, so the same block renders
    # on PowerShell hosts without console-encoding drift.
    _write_json(out / ".kg_detect.json", result)
    print(f"Detected {result['total_files']} files", file=sys.stderr)

    cache_hits, uncached, batches = _cache_check_and_batch(result["files"], root)

    summary = {
        "total_files": result["total_files"],
        "total_words": result["total_words"],
        "categories": {k: len(v) for k, v in result["files"].items()},
        "skipped_sensitive": len(result.get("skipped_sensitive", [])),
        "skipped_sensitive_files": result.get("skipped_sensitive", []),
        "cache_hits": cache_hits,
        "uncached": len(uncached),
        "batches": len(batches),
        "scan_root": result.get("scan_root"),
        "warning": result.get("warning"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _cache_check_and_batch(files_by_type: dict, root: Path) -> tuple[int, list[str], list[list[str]]]:
    """Step B0 (semantic cache check) + Step B1 (batch plan) over a files dict.

    Documents and papers only — this skill ignores code/image/video. Writes
    kg-out/.kg_cached.json (or deletes the stale one), .kg_uncached.txt
    and .kg_batches.json. Returns (cache_hits, uncached_files, batches).
    Shared by prepare (full corpus) and update-detect (changed subset).
    """
    from kglib.cache import check_semantic_cache, cached_word_count
    from kglib.detect import count_words
    from kglib.paths import write_text_atomic

    out = _out()
    all_files = [f for cat in _SEM_TYPES for f in files_by_type.get(cat, [])]
    cached_nodes, cached_edges, cached_hyperedges, uncached = check_semantic_cache(all_files, root=root)

    # Always (re)write the cache file: write hits, else DELETE any leftover from a prior
    # run so the merge never picks up a stale .kg_cached.json.
    if cached_nodes or cached_edges or cached_hyperedges:
        _write_json(out / ".kg_cached.json",
                    {"nodes": cached_nodes, "edges": cached_edges, "hyperedges": cached_hyperedges})
    else:
        (out / ".kg_cached.json").unlink(missing_ok=True)
    write_text_atomic(out / ".kg_uncached.txt", "\n".join(uncached))
    print(f"Cache: {len(all_files) - len(uncached)} files hit, {len(uncached)} files need extraction",
          file=sys.stderr)

    # Step B1 - batch plan: at most 10 files or ~30k words per batch, whichever
    # is smaller; files from the same directory stay together so cross-file
    # relationships land in the same batch.
    resolved_root = root.resolve()
    by_dir: dict[str, list[str]] = {}
    for f in uncached:
        by_dir.setdefault(str(Path(f).parent), []).append(f)
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_words = 0
    for d in sorted(by_dir):
        for f in sorted(by_dir[d]):
            w = cached_word_count(Path(f), resolved_root, count_words)
            if cur and (len(cur) >= 10 or cur_words + w > 30_000):
                batches.append(cur)
                cur, cur_words = [], 0
            cur.append(f)
            cur_words += w
    if cur:
        batches.append(cur)
    _write_json(out / ".kg_batches.json", {"batches": batches}, indent=2)
    return len(all_files) - len(uncached), uncached, batches


# ---------------------------------------------------------------------------
# merge-extraction
# ---------------------------------------------------------------------------

def cmd_merge_extraction(args) -> int:
    """Step B3 (collect, cache, merge) + Part C (final extraction)."""
    from kglib.cache import save_semantic_cache

    root = Path(args.root)
    out = _out()

    chunks = sorted(out.glob(".kg_chunk_*.json"))
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    all_hyperedges: list[dict] = []
    valid = 0
    for c in chunks:
        try:
            d = _read_json(c)
        except Exception as exc:
            print(f"[kg] WARNING: skipping invalid chunk {c} ({exc})", file=sys.stderr)
            continue
        if not isinstance(d, dict) or not isinstance(d.get("nodes"), list) or not isinstance(d.get("edges"), list):
            print(f"[kg] WARNING: skipping invalid chunk {c} (missing nodes/edges lists)", file=sys.stderr)
            continue
        all_nodes += d.get("nodes", [])
        all_edges += d.get("edges", [])
        all_hyperedges += d.get("hyperedges", [])
        valid += 1
    # Self-extraction has no per-batch token metering — token counts stay 0.
    new = {
        "nodes": all_nodes, "edges": all_edges, "hyperedges": all_hyperedges,
        "input_tokens": 0, "output_tokens": 0,
    }
    _write_json(out / ".kg_semantic_new.json", new, indent=2)
    print(f"Merged {valid} batches", file=sys.stderr)

    # Save new results to cache.
    uncached_path = out / ".kg_uncached.txt"
    uncached = [line for line in uncached_path.read_text(encoding="utf-8").splitlines() if line] \
        if uncached_path.exists() else []
    saved = save_semantic_cache(new["nodes"], new["edges"], new["hyperedges"],
                                root=root, allowed_source_files=uncached)
    print(f"Cached {saved} files", file=sys.stderr)

    # Merge cached + new results into kg-out/.kg_semantic.json.
    cached_path = out / ".kg_cached.json"
    cached = _read_json(cached_path) if cached_path.exists() else {"nodes": [], "edges": [], "hyperedges": []}
    merged_nodes = cached["nodes"] + new["nodes"]
    merged_edges = cached["edges"] + new["edges"]
    merged_hyperedges = cached.get("hyperedges", []) + new["hyperedges"]
    seen: set = set()
    deduped: list[dict] = []
    for n in merged_nodes:
        if n["id"] not in seen:
            seen.add(n["id"])
            deduped.append(n)
    merged = {
        "nodes": deduped,
        "edges": merged_edges,
        "hyperedges": merged_hyperedges,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    _write_json(out / ".kg_semantic.json", merged, indent=2)
    print(f"Extraction complete - {len(deduped)} nodes, {len(merged_edges)} edges "
          f"({len(cached['nodes'])} from cache, {len(new['nodes'])} new)", file=sys.stderr)

    # Clean up temp files.
    (out / ".kg_cached.json").unlink(missing_ok=True)
    (out / ".kg_uncached.txt").unlink(missing_ok=True)
    (out / ".kg_semantic_new.json").unlink(missing_ok=True)

    # Part C - final extraction. Documents-only corpus: the semantic result IS
    # the extraction (there is no AST pass to merge).
    shutil.copyfile(out / ".kg_semantic.json", out / ".kg_extract.json")
    d = _read_json(out / ".kg_extract.json")
    print(f"Extraction: {len(d['nodes'])} nodes, {len(d['edges'])} edges, "
          f"{len(d.get('hyperedges', []))} hyperedges")
    return 0


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _load_extraction_and_detection(out: Path) -> tuple[dict, dict]:
    extract_path = out / ".kg_extract.json"
    if not extract_path.exists():
        print("error: kg-out/.kg_extract.json not found — run prepare + merge-extraction first.",
              file=sys.stderr)
        raise SystemExit(1)
    extraction = _read_json(extract_path)
    detect_path = out / ".kg_detect.json"
    if detect_path.exists():
        detection = _read_json(detect_path)
    else:
        detection = {"warning": "detect sidecar not available", "total_files": 0, "total_words": 0}
    return extraction, detection


def cmd_build(args) -> int:
    """Step 4 - build graph, cluster, analyze, generate outputs."""
    from kglib.build import build_from_json
    from kglib.cluster import cluster, score_all
    from kglib.analyze import god_nodes, surprising_connections, suggest_questions
    from kglib.report import generate
    from kglib.export import to_json

    root = args.root
    out = _out()
    out.mkdir(parents=True, exist_ok=True)
    extraction, detection = _load_extraction_and_detection(out)

    # root= mirrors the update runbook: relativize source_file to the same
    # base so the full build and incremental update never drift apart on re-extract.
    G = build_from_json(extraction, root=root, directed=args.directed)
    # Guard BEFORE any write: an empty extraction must not clobber a good graph.json /
    # GRAPH_REPORT.md / analysis sidecar. Check immediately after build.
    if G.number_of_nodes() == 0:
        print("ERROR: Graph is empty - extraction produced no nodes.")
        print("Possible causes: all files were skipped, binary-only corpus, or extraction failed.")
        return 1
    communities = cluster(G)
    cohesion = score_all(G, communities)
    tokens = {"input": extraction.get("input_tokens", 0), "output": extraction.get("output_tokens", 0)}
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    labels = {cid: "Community " + str(cid) for cid in communities}
    # Placeholder questions - regenerated with real labels by relabel.
    questions = suggest_questions(G, communities, labels)

    # Export FIRST and honor the shrink-guard: to_json returns False (writing
    # nothing) when the new graph is smaller than the existing graph.json. Only write
    # GRAPH_REPORT.md + the analysis sidecar when the graph was actually written, so
    # they never describe a graph that graph.json doesn't contain.
    wrote = to_json(G, communities, str(out / "graph.json"), force=args.force)
    if not wrote:
        print("ERROR: refused to shrink kg-out/graph.json (existing graph has more nodes).")
        print("If this shrink is intentional (you deleted files), re-run a full build with --force.")
        return 1
    report = generate(G, communities, cohesion, labels, gods, surprises, detection,
                      tokens, root, suggested_questions=questions)
    (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    analysis = {
        "communities": {str(k): v for k, v in communities.items()},
        "cohesion": {str(k): v for k, v in cohesion.items()},
        "gods": gods,
        "surprises": surprises,
        "questions": questions,
    }
    _write_json(out / ".kg_analysis.json", analysis, indent=2)
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities")
    return 0


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

def cmd_diagnose(args) -> int:
    """Step 4.5 - read-only graph health check."""
    from kglib.diagnostics import diagnose_extraction, format_diagnostic_report

    out = _out()
    extract_path = out / ".kg_extract.json"
    if not extract_path.exists():
        _err("error: kg-out/.kg_extract.json not found — run prepare + merge-extraction first.")
        return 1
    extraction = _read_json(extract_path)
    summary = diagnose_extraction(extraction, directed=args.directed, root=args.root)
    print(format_diagnostic_report(summary))
    flags = [f"{summary[k]} {label}" for k, label in (
        ("dangling_endpoint_edges", "dangling-endpoint edges"),
        ("missing_endpoint_edges", "missing-endpoint edges"),
        ("self_loop_edges", "self-loop edges"),
        ("directed_same_endpoint_collapsed_edges", "collapsed (directed) edges"),
        ("undirected_same_endpoint_collapsed_edges", "collapsed (undirected) edges"),
    ) if summary.get(k, 0)]
    print("GRAPH HEALTH WARNING: " + "; ".join(flags) + " - graph may be incomplete/corrupt." if flags
          else "Graph health: OK (no dangling/missing/collapsed edges).")
    return 0


# ---------------------------------------------------------------------------
# relabel
# ---------------------------------------------------------------------------

def cmd_relabel(args) -> int:
    """Step 5 - write curated community labels, regenerate report + graph.json."""
    from kglib.build import build_from_json
    from kglib.cluster import score_all, community_member_sigs
    from kglib.analyze import suggest_questions
    from kglib.report import generate
    from kglib.export import to_json

    root = args.root
    out = _out()
    try:
        labels = {int(k): str(v) for k, v in json.loads(args.labels).items()}
    except Exception as exc:
        _err(f"error: --labels must be a JSON object like '{{\"0\": \"Name\"}}' ({exc})")
        return 1
    extraction, detection = _load_extraction_and_detection(out)
    analysis_path = out / ".kg_analysis.json"
    if not analysis_path.exists():
        _err("error: kg-out/.kg_analysis.json not found — run build first.")
        return 1
    analysis = _read_json(analysis_path)

    # root= as in build / the update runbook — same base for node-key parity.
    G = build_from_json(extraction, root=root, directed=args.directed)
    communities = {int(k): v for k, v in analysis["communities"].items()}
    cohesion = {int(k): v for k, v in analysis["cohesion"].items()}
    tokens = {"input": extraction.get("input_tokens", 0), "output": extraction.get("output_tokens", 0)}

    # Regenerate questions with real community labels (labels affect question phrasing)
    questions = suggest_questions(G, communities, labels)

    report = generate(G, communities, cohesion, labels, analysis["gods"], analysis["surprises"],
                      detection, tokens, root, suggested_questions=questions)
    (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    _write_json(out / ".kg_labels.json", {str(k): v for k, v in labels.items()})
    # Membership signatures beside the labels so a later cluster-only can tell
    # which communities changed and never reuses a stale label (same .sig
    # mechanism cluster-only writes).
    (out / ".kg_labels.json.sig").write_text(
        json.dumps({str(k): v for k, v in community_member_sigs(communities).items()}),
        encoding="utf-8",
    )
    # Re-export so graph.json nodes carry the curated community_name.
    # Same extraction as build, so the shrink-guard passes on node count;
    # if it still refuses, surface the guard message - do not force past it.
    wrote = to_json(G, communities, str(out / "graph.json"), community_labels=labels)
    if not wrote:
        print("ERROR: refused to shrink kg-out/graph.json (existing graph has more nodes).")
        print("If this shrink is intentional (you deleted files), re-run a full build with --force.")
    print("Report updated with community labels")
    return 0


# ---------------------------------------------------------------------------
# export-html
# ---------------------------------------------------------------------------

def cmd_export_html(args) -> int:
    """Step 6 - generate graph.html (auto-aggregates to community view > 5000 nodes)."""
    from kglib.export import to_html
    from kglib.security import check_graph_file_size_cap

    out = _out()
    graph_path = Path(args.graph) if args.graph else out / "graph.json"
    if not graph_path.exists():
        _err(f"error: graph not found: {graph_path}. Run /kg <path> first.")
        return 1
    # Over-cap fallback: an oversized graph.json should not be a hard
    # error for the HTML view; fall back to the community-aggregation view.
    over_cap = False
    try:
        check_graph_file_size_cap(graph_path)
    except ValueError:
        over_cap = True
        try:
            over_cap_bytes = graph_path.stat().st_size
        except OSError:
            over_cap_bytes = -1
        _err(f"warning: graph.json exceeds cap ({over_cap_bytes} bytes); "
             f"falling back to community-aggregation view (node_limit=5000)")
    from kglib.paths import load_node_link_graph
    raw = _read_json(graph_path)
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    G = load_node_link_graph(raw)

    # Load optional analysis/labels
    out_dir = graph_path.parent
    analysis_path = out_dir / ".kg_analysis.json"
    labels_path = out_dir / ".kg_labels.json"
    communities: dict[int, list[str]] = {}
    if analysis_path.exists():
        _an = _read_json(analysis_path)
        communities = {int(k): v for k, v in _an.get("communities", {}).items()}

    # Fallback: graph.json carries the per-node community as a node attribute
    # (`to_json` writes it on every node). The analysis sidecar is the
    # canonical source — but the update / rebuild path doesn't always have it.
    # Reconstruct from the graph itself so export html doesn't silently
    # produce a degraded artifact.
    if not communities:
        reconstructed: dict[int, list[str]] = {}
        for node_id, data in G.nodes(data=True):
            cid_raw = data.get("community")
            if cid_raw is None:
                continue
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError):
                continue
            reconstructed.setdefault(cid, []).append(str(node_id))
        if reconstructed:
            communities = reconstructed

    labels: dict[int, str] = {}
    if labels_path.exists():
        labels = {int(k): v for k, v in _read_json(labels_path).items()}

    node_limit = 5000
    if args.no_viz:
        html_target = out_dir / "graph.html"
        if html_target.exists():
            html_target.unlink()
        print("--no-viz: skipped graph.html")
        return 0
    # Over-cap fallback: force the community-aggregation path so the
    # oversized graph still renders a usable artifact.
    effective_node_limit = 5000 if over_cap else node_limit
    to_html(G, communities, str(out_dir / "graph.html"),
            community_labels=labels or None, node_limit=effective_node_limit)
    if G.number_of_nodes() <= effective_node_limit:
        print("graph.html written - open in any browser, no server needed")
    return 0


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _stamp_manifest(detect: dict, extract: dict, root: str) -> None:
    """Shared manifest-stamping semantics for finalize and update-merge.

    root= relativizes the manifest keys to the scan root (same base as the build),
    so the on-disk manifest is portable across clones/machines and a later update
    matches cached files instead of missing every one.

    Only stamp semantic files (docs/papers) that ACTUALLY produced output:
    a detected file whose batch failed or was omitted must stay unstamped so the
    next update re-queues it, otherwise it is marked done and its content is lost.
    """
    from kglib.detect import save_manifest
    from kglib.flow import _stamped_manifest_files

    # In update mode, 'all_files' carries the full corpus; 'files' is the changed
    # subset. Full-rebuild mode populates only 'files', so the fallback handles that.
    _corpus = detect.get("all_files") or detect["files"]
    _manifest_files = _stamped_manifest_files(_corpus, extract, Path(root))
    # Files dispatched this run (the changed subset) but NOT stamped above still carry
    # a stale semantic_hash from a prior run; clear it so detect_incremental re-queues
    # them instead of reading them as unchanged.
    _dispatched = {f for t, fl in detect["files"].items() if t in _SEM_TYPES for f in fl}
    _stamped = {f for fl in _manifest_files.values() for f in fl}
    _cleared = _dispatched - _stamped
    # scan_corpus = the RAW full corpus (not the stamp-filtered subset) so in-root
    # files newly excluded since last run are dropped rather than masquerading as
    # deletions; untouched files' prior rows are still preserved.
    _scan = {f for fl in _corpus.values() for f in fl}
    save_manifest(_manifest_files, root=root, scan_corpus=_scan, clear_semantic=_cleared or None)


def _update_cost(detect: dict, extract: dict, out: Path) -> None:
    from datetime import datetime, timezone

    # Update cumulative cost tracker (self-extraction reports 0 tokens — say so to the user)
    input_tok = extract.get("input_tokens", 0)
    output_tok = extract.get("output_tokens", 0)

    cost_path = out / "cost.json"
    if cost_path.exists():
        cost = _read_json(cost_path)
    else:
        cost = {"runs": [], "total_input_tokens": 0, "total_output_tokens": 0}

    cost["runs"].append({
        "date": datetime.now(timezone.utc).isoformat(),
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "files": detect.get("total_files", 0),
    })
    cost["total_input_tokens"] += input_tok
    cost["total_output_tokens"] += output_tok
    _write_json(cost_path, cost, indent=2)

    print(f"This run: {input_tok:,} input tokens, {output_tok:,} output tokens "
          f"(self-extraction: tokens are your own context, not metered)")
    print(f"All time: {cost['total_input_tokens']:,} input, {cost['total_output_tokens']:,} output "
          f"({len(cost['runs'])} runs)")


def cmd_finalize(args) -> int:
    """Step 9 - save manifest, update cost tracker, clean up temp files."""
    out = _out()
    extract_path = out / ".kg_extract.json"
    detect_path = out / ".kg_detect.json"
    if not extract_path.exists() or not detect_path.exists():
        _err("error: kg-out/.kg_extract.json / .kg_detect.json not found — "
             "run the build steps first.")
        return 1
    detect = _read_json(detect_path)
    extract = _read_json(extract_path)

    # Save manifest for the update flow.
    _stamp_manifest(detect, extract, args.root)
    _update_cost(detect, extract, out)

    # Temp-file cleanup (same set as SKILL Step 9).
    for name in (".kg_detect.json", ".kg_extract.json",
                 ".kg_semantic.json", ".kg_analysis.json",
                 ".needs_update"):
        (out / name).unlink(missing_ok=True)
    for chunk in out.glob(".kg_chunk_*.json"):
        chunk.unlink(missing_ok=True)
    return 0


# ---------------------------------------------------------------------------
# update-detect / update-merge / diff
# ---------------------------------------------------------------------------

def cmd_update_detect(args) -> int:
    """Incremental detect: which files changed since the last stamped run."""
    from kglib.detect import detect_incremental

    root = Path(args.root)
    if not root.exists():
        _err(f"error: path not found: {root}")
        return 1
    out = _out()
    out.mkdir(parents=True, exist_ok=True)

    result = detect_incremental(root)
    new_total = result.get("new_total", 0)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    _write_json(out / ".kg_incremental.json", result)
    deleted = list(result.get("deleted_files", []))
    if new_total == 0 and not deleted:
        print("No files changed since last run. Nothing to update.")
        return 0
    if deleted:
        print(f"{len(deleted)} deleted file(s) to prune.")
    if new_total > 0:
        print(f"{new_total} new/changed file(s) to re-extract.")

    # Populate .kg_detect.json so the prepare/merge/build steps (which read
    # it unconditionally) see the right state for an incremental run. 'files'
    # carries the changed subset (drives the cache check on only what changed);
    # 'all_files' carries the full corpus for any step that needs corpus-wide context.
    _write_json(out / ".kg_detect.json", {
        "files": result.get("new_files", {}),
        "all_files": result.get("files", {}),
        "total_files": result.get("new_total", 0),
        "total_words": result.get("total_words", 0),
        "skipped_sensitive": result.get("skipped_sensitive", []),
        "needs_graph": True,
    })

    if new_total == 0:
        # Only deletions: create an empty extraction so the merge step can prune.
        extract_path = out / ".kg_extract.json"
        if not extract_path.exists():
            print("[kg update] Only deletions -- creating empty extraction for merge.")
            _write_json(extract_path, {"nodes": [], "edges": [], "hyperedges": [],
                                       "input_tokens": 0, "output_tokens": 0})
    else:
        # Cache-check the CHANGED document/paper subset and write fresh
        # .kg_uncached.txt / .kg_batches.json for it — the update
        # flow's self-extraction loop consumes those batches. Non-document
        # changed files (code/image/video) are ignored by this skill.
        _cache_check_and_batch(result.get("new_files", {}), root)
    return 0


def cmd_update_merge(args) -> int:
    """Merge a fresh (incremental) extraction into the existing graph."""
    from kglib.build import build_merge

    root = args.root
    out = _out()
    extract_path = out / ".kg_extract.json"
    incremental_path = out / ".kg_incremental.json"
    if not extract_path.exists() or not incremental_path.exists():
        _err("error: kg-out/.kg_extract.json / .kg_incremental.json not found — "
             "run update-detect (+ extraction) first.")
        return 1

    # Save the old graph for the diff step.
    graph_path = out / "graph.json"
    if graph_path.exists():
        shutil.copyfile(graph_path, out / ".kg_old.json")

    # Load new extraction and incremental state
    new_extraction = _read_json(extract_path)
    incremental = _read_json(incremental_path)
    deleted = list(incremental.get("deleted_files", []))
    # prune_sources is ONLY for genuinely DELETED files. Changed/re-extracted files are
    # handled by build_merge's replace-on-re-extract: every source_file in
    # new_chunks is dropped from the base before merge, so old/stale nodes don't survive.
    prune = list(deleted) or None

    # Use build_merge() — reads graph.json directly without NetworkX round-trip
    # so edge direction is always preserved.
    # Pass root= so prune_sources (absolute paths from detect_incremental) are
    # relativized to match the graph's relative source_file values; without it
    # nothing is pruned and stale nodes accumulate on every update.
    G = build_merge(
        [new_extraction],
        graph_path=str(graph_path),
        prune_sources=prune,
        root=root,
        directed=args.directed,
    )
    print(f"[kg update] Merged: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # Write merged result back to .kg_extract.json so build sees the full graph
    merged_out = {
        "nodes": [{"id": n, **d} for n, d in G.nodes(data=True)],
        "edges": [
            {**{k: val for k, val in d.items() if k not in ("_src", "_tgt", "source", "target")},
             "source": d.get("_src", u), "target": d.get("_tgt", v)}
            for u, v, d in G.edges(data=True)
        ],
        "hyperedges": list(G.graph.get("hyperedges", [])),
        "input_tokens": new_extraction.get("input_tokens", 0),
        "output_tokens": new_extraction.get("output_tokens", 0),
    }
    _write_json(extract_path, merged_out)
    print(f"[kg update] Merged extraction written ({len(merged_out['nodes'])} nodes, "
          f"{len(merged_out['edges'])} edges)")

    # Save manifest so the next update diffs against today's state.
    # Only stamp semantic files (docs/papers) that ACTUALLY produced output
    # THIS run: a changed doc whose batch failed must stay unstamped so the
    # next update re-queues it.
    from kglib.detect import save_manifest
    from kglib.flow import _stamped_manifest_files
    _manifest_files = _stamped_manifest_files(incremental["files"], new_extraction, Path(root))
    _dispatched = {f for t, fl in incremental.get("new_files", {}).items() if t in _SEM_TYPES for f in fl}
    _stamped = {f for fl in _manifest_files.values() for f in fl}
    _cleared = _dispatched - _stamped
    _scan = {f for fl in incremental["files"].values() for f in fl}
    save_manifest(_manifest_files, root=root, scan_corpus=_scan, clear_semantic=_cleared or None)
    print("[kg update] Manifest saved.")
    return 0


def cmd_diff(args) -> int:
    """Graph diff of the pre-update graph vs the merged extraction; then clean up."""
    from kglib.analyze import graph_diff
    from kglib.build import build_from_json
    from networkx.readwrite import json_graph

    out = _out()
    old_path = out / ".kg_old.json"
    extract_path = out / ".kg_extract.json"
    old_data = _read_json(old_path) if old_path.exists() else None
    if not extract_path.exists():
        _err("error: kg-out/.kg_extract.json not found — run update-merge first.")
        return 1
    new_extract = _read_json(extract_path)
    G_new = build_from_json(new_extract, directed=args.directed)

    if old_data:
        if "links" not in old_data and "edges" in old_data:
            old_data = dict(old_data, links=old_data["edges"])
        try:
            G_old = json_graph.node_link_graph(old_data, edges="links")
        except TypeError:
            G_old = json_graph.node_link_graph(old_data)
        diff = graph_diff(G_old, G_new)
        print(diff["summary"])
        if diff["new_nodes"]:
            print("New nodes:", ", ".join(n["label"] for n in diff["new_nodes"][:5]))
        if diff["new_edges"]:
            print("New edges:", len(diff["new_edges"]))
    else:
        print("No pre-update graph snapshot found - nothing to diff against.")

    old_path.unlink(missing_ok=True)
    (out / ".kg_incremental.json").unlink(missing_ok=True)
    return 0


# ---------------------------------------------------------------------------
# cluster-only
# ---------------------------------------------------------------------------

def cmd_cluster_only(args) -> int:
    from kglib.flow import run_cluster_only
    return run_cluster_only(
        Path(args.path),
        graph_override=Path(args.graph) if args.graph else None,
        resolution=args.resolution,
        exclude_hubs=args.exclude_hubs,
        no_viz=args.no_viz,
        min_community_size=args.min_community_size,
    )


# ---------------------------------------------------------------------------
# query / path / explain / save-result / reflect / vocab / benchmark
# ---------------------------------------------------------------------------

def cmd_query(args) -> int:
    from kglib.flow import run_query
    return run_query(args.question, use_dfs=args.dfs, budget=args.budget,
                     graph_path=args.graph, context_filters=args.context)


def cmd_path(args) -> int:
    if args.directed and args.undirected:
        _err("error: --directed and --undirected are mutually exclusive")
        return 1
    from kglib.flow import run_path
    return run_path(args.source, args.target, graph_path=args.graph, undirected=args.undirected)


def cmd_explain(args) -> int:
    from kglib.flow import run_explain
    return run_explain(args.node, graph_path=args.graph)


def cmd_save_result(args) -> int:
    answer = args.answer
    if args.answer_file:
        answer = Path(args.answer_file).read_text(encoding="utf-8").strip()
    if not answer:
        _err("error: --answer or --answer-file is required")
        return 1
    from kglib.flow import run_save_result
    run_save_result(
        question=args.question,
        answer=answer,
        query_type=args.type,
        nodes=args.nodes,
        outcome=args.outcome,
        correction=args.correction,
    )
    return 0


def cmd_reflect(args) -> int:
    from kglib.reflect import reflect as _reflect, lessons_fresh as _lessons_fresh

    out = _out()
    memory_dir = Path(args.memory_dir) if args.memory_dir else out / "memory"
    lessons_out = Path(args.out) if args.out else out / "reflections" / "LESSONS.md"

    graph_arg = args.graph
    if graph_arg is None:
        default_graph = out / "graph.json"
        if default_graph.exists():
            graph_arg = str(default_graph)

    _gp = Path(graph_arg) if graph_arg else None
    _analysis_path = None
    _labels_path = None
    if _gp is not None:
        _analysis_path = Path(args.analysis) if args.analysis else (
            _gp.parent / ".kg_analysis.json")
        _labels_path = Path(args.labels) if args.labels else (
            _gp.parent / ".kg_labels.json")

    if args.if_stale and _lessons_fresh(lessons_out, memory_dir, _gp, _analysis_path, _labels_path):
        print(f"Lessons already up to date -> {lessons_out} (skipped; omit --if-stale to force)")
        return 0
    out_path, agg = _reflect(
        memory_dir=memory_dir,
        out_path=lessons_out,
        graph_path=_gp,
        analysis_path=_analysis_path,
        labels_path=_labels_path,
        half_life_days=args.half_life_days,
        min_corroboration=args.min_corroboration,
    )
    c = agg["counts"]
    print(
        f"Reflected {agg['total']} memories "
        f"({c['useful']} useful, {c['dead_end']} dead ends, "
        f"{c['corrected']} corrected) -> {out_path}"
    )
    return 0


def cmd_vocab(args) -> int:
    """Extract the label vocabulary from graph.json into kg-out/.vocab.txt."""
    import re

    out = _out()
    graph_path = out / "graph.json"
    if not graph_path.exists():
        _err("ERROR: No graph found. Run /kg <path> first to build the graph.")
        return 1
    data = _read_json(graph_path)
    vocab: set[str] = set()
    for n in data["nodes"]:
        for c in re.findall(r"[^\W\d_]+", n.get("label", "") or "", re.UNICODE):
            parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+", c) or [c]
            for p in parts:
                t = p.lower()
                if 3 <= len(t) <= 30:
                    vocab.add(t)
    (out / ".vocab.txt").write_text("\n".join(sorted(vocab)), encoding="utf-8")
    print(f"vocab: {len(vocab)} tokens")
    return 0


def cmd_benchmark(args) -> int:
    from kglib.benchmark import run_benchmark, print_benchmark

    out = _out()
    graph_path = args.graph or str(out / "graph.json")
    if not Path(graph_path).exists():
        _err(f"error: graph not found: {graph_path}. Run /kg <path> first.")
        return 1
    # Try to load corpus_words from detect output
    corpus_words = None
    detect_path = out / ".kg_detect.json"
    if detect_path.exists():
        try:
            corpus_words = _read_json(detect_path).get("total_words")
        except Exception:
            pass
    result = run_benchmark(graph_path, corpus_words=corpus_words)
    print_benchmark(result)
    return 0


# ---------------------------------------------------------------------------
# argparse dispatcher
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kg.py",
        description="kg — documents-only knowledge-graph tool (operates on ./kg-out/).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("prepare", help="detect + cache check + batch plan")
    sp.add_argument("--root", required=True)
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("merge-extraction", help="merge .kg_chunk_*.json into the extraction")
    sp.add_argument("--root", required=True)
    sp.set_defaults(func=cmd_merge_extraction)

    sp = sub.add_parser("build", help="build graph, cluster, analyze, export graph.json + report")
    sp.add_argument("--root", required=True)
    sp.add_argument("--directed", action="store_true")
    sp.add_argument("--force", action="store_true",
                    help="override the shrink-guard (intentional graph reduction, e.g. deleted files)")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("diagnose", help="read-only graph health check")
    sp.add_argument("--root", required=True)
    sp.add_argument("--directed", action="store_true")
    sp.set_defaults(func=cmd_diagnose)

    sp = sub.add_parser("relabel", help="write curated community labels, regenerate report + graph.json")
    sp.add_argument("--root", required=True)
    sp.add_argument("--labels", required=True, help='JSON object, e.g. {"0": "Name", ...}')
    sp.add_argument("--directed", action="store_true")
    sp.set_defaults(func=cmd_relabel)

    sp = sub.add_parser("export-html", help="generate graph.html (aggregates > 5000 nodes)")
    sp.add_argument("--graph", default=None)
    sp.add_argument("--no-viz", action="store_true")
    sp.set_defaults(func=cmd_export_html)

    sp = sub.add_parser("finalize", help="stamp manifest, update cost.json, clean temp files")
    sp.add_argument("--root", required=True)
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("update-detect", help="incremental detect (new/changed/deleted files)")
    sp.add_argument("--root", required=True)
    sp.set_defaults(func=cmd_update_detect)

    sp = sub.add_parser("update-merge", help="merge incremental extraction into the existing graph")
    sp.add_argument("--root", required=True)
    sp.add_argument("--directed", action="store_true")
    sp.set_defaults(func=cmd_update_merge)

    sp = sub.add_parser("diff", help="diff pre-update graph vs merged extraction, then clean up")
    sp.add_argument("--directed", action="store_true")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("cluster-only", help="re-cluster the existing graph and regenerate outputs")
    sp.add_argument("path", nargs="?", default=".")
    sp.add_argument("--graph", default=None)
    sp.add_argument("--resolution", type=float, default=1.0)
    sp.add_argument("--exclude-hubs", type=float, default=None)
    sp.add_argument("--no-viz", action="store_true")
    sp.add_argument("--min-community-size", type=int, default=3)
    sp.set_defaults(func=cmd_cluster_only)

    sp = sub.add_parser("query", help="BFS/DFS traversal over the graph")
    sp.add_argument("question")
    sp.add_argument("--dfs", action="store_true")
    sp.add_argument("--budget", type=int, default=2000)
    sp.add_argument("--context", action="append", default=[])
    sp.add_argument("--graph", default=None)
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("path", help="shortest path between two concepts")
    sp.add_argument("source")
    sp.add_argument("target")
    sp.add_argument("--directed", action="store_true")
    sp.add_argument("--undirected", action="store_true")
    sp.add_argument("--graph", default=None)
    sp.set_defaults(func=cmd_path)

    sp = sub.add_parser("explain", help="plain-language explanation of a node")
    sp.add_argument("node")
    sp.add_argument("--graph", default=None)
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("save-result", help="save a Q&A result into kg-out/memory/")
    sp.add_argument("--question", required=True)
    sp.add_argument("--answer", default=None)
    sp.add_argument("--answer-file", dest="answer_file", default=None)
    sp.add_argument("--type", dest="type", default="query")
    sp.add_argument("--nodes", nargs="*", default=[])
    sp.add_argument("--outcome", choices=("useful", "dead_end", "corrected"), default=None)
    sp.add_argument("--correction", default=None)
    sp.set_defaults(func=cmd_save_result)

    sp = sub.add_parser("reflect", help="aggregate work-memory into reflections/LESSONS.md")
    sp.add_argument("--memory-dir", default=None)
    sp.add_argument("--out", default=None)
    sp.add_argument("--graph", default=None)
    sp.add_argument("--analysis", default=None)
    sp.add_argument("--labels", default=None)
    sp.add_argument("--half-life-days", type=float, default=30.0,
                    help="signal weight halves every N days (default 30)")
    sp.add_argument("--min-corroboration", type=int, default=2,
                    help="distinct useful results to promote a node to preferred (default 2)")
    sp.add_argument("--if-stale", action="store_true",
                    help="skip when LESSONS.md is already newer than every input")
    sp.set_defaults(func=cmd_reflect)

    sp = sub.add_parser("vocab", help="extract label vocabulary from graph.json into kg-out/.vocab.txt")
    sp.set_defaults(func=cmd_vocab)

    sp = sub.add_parser("benchmark", help="token-reduction benchmark vs naive full-corpus reading")
    sp.add_argument("--graph", default=None)
    sp.set_defaults(func=cmd_benchmark)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
