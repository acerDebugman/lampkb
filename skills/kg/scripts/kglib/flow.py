# Vendored from graphify (https://github.com/safishamsi/graphify), slimmed to documents-only.
# Importable ports of the graphify CLI flows that kg keeps: manifest stamping,
# query / path / explain, save-result, and the self-contained cluster-only run.
# (Upstream: graphify/cli.py. Everything LLM-backend-related is dropped — kg
# never calls an LLM; the host agent does the extraction and labeling.)
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from kglib.paths import KG_OUT, KG_OUT_NAME, write_text_atomic, write_json_atomic


def _stamped_manifest_files(
    files_by_type: dict[str, list[str]],
    sem_result: dict,
    root: Path,
    partial_source_files: "set[str] | None" = None,
    failed_ast_sources: "set[str] | list[str] | None" = None,
) -> dict[str, list[str]]:
    """Manifest-safe files dict: only stamp semantic files that actually
    produced output (cache hit or fresh extraction). Files whose chunk failed
    have no source_file entry in sem_result — leaving their semantic_hash
    empty so detect_incremental re-queues them.

    A file in ``partial_source_files`` DID produce output this run, but only a
    truncated fragment of it, so it is excluded from stamping too — otherwise
    detect_incremental would see it "done" and never re-dispatch it, leaving the
    incomplete node set live forever on the warm-incremental path. The same
    mechanism applies: leave it unstamped and it is re-queued next run.

    Both sides of the membership test are resolved against the scan ``root``
    before comparing: node/edge ``source_file`` values are
    root-relative on a fresh extraction while ``files_by_type`` entries are
    absolute (from detect()), so a raw string comparison never matched and
    every freshly-extracted semantic doc was dropped from the manifest.
    Mirrors the path normalization in graphify.llm.

    ``failed_ast_sources``: code files whose AST extractor errored
    (missing optional extra, etc.) or returned zero nodes. They must not be
    stamped as up-to-date or a later install of the extra will never re-run.
    Kept for signature parity although kg has no AST pass (never set).
    """
    root = Path(root)

    def _resolve(value: str) -> Path:
        p = Path(value)
        if not p.is_absolute():
            p = root / p
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return p

    sem_extracted: set[Path] = set()
    for coll in ("nodes", "edges"):
        for item in sem_result.get(coll, []):
            sf = item.get("source_file", "")
            if sf:
                sem_extracted.add(_resolve(sf))
    partial_resolved = {_resolve(p) for p in (partial_source_files or set())}
    failed_ast_resolved = {_resolve(p) for p in (failed_ast_sources or [])}
    sem_types = {"document", "paper", "image"}
    return {
        ftype: [
            f for f in flist
            if _resolve(f) not in failed_ast_resolved
            and (
                ftype not in sem_types
                or (_resolve(f) in sem_extracted and _resolve(f) not in partial_resolved)
            )
        ]
        for ftype, flist in files_by_type.items()
    }


def _touch_query_stamp(graph_path: "Path") -> None:
    """Record that kg oriented the agent recently, next to the queried graph.
    Fail-silent."""
    try:
        stamp = Path(graph_path).parent / "cache" / "last_query_stamp"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(stamp, str(time.time()))
    except Exception:
        pass


def _enforce_graph_size_cap_or_exit(graph_path: Path) -> None:
    from kglib.security import check_graph_file_size_cap
    try:
        check_graph_file_size_cap(graph_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_raw_graph(gp: Path) -> dict:
    _enforce_graph_size_cap_or_exit(gp)
    raw = json.loads(gp.read_text(encoding="utf-8"))
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    return raw


def _legacy_id_note(raw: dict) -> None:
    try:
        from kglib.build import graph_has_legacy_ids as _legacy
        if _legacy(raw.get("nodes", [])):
            print(
                "[kg] note: this graph uses the legacy node-ID scheme; "
                "re-run a full build to get path-qualified IDs "
                "(fixes same-name-file collisions).",
                file=sys.stderr,
            )
    except Exception:
        pass


def run_query(
    question: str,
    *,
    use_dfs: bool = False,
    budget: int = 2000,
    graph_path: str | None = None,
    context_filters: list[str] | None = None,
) -> int:
    """The `query` command flow (upstream: cli.py `query`). Prints the result."""
    from kglib.serve import _query_graph_text
    from kglib import querylog

    gp = Path(graph_path or (Path(KG_OUT) / "graph.json")).resolve()
    if not gp.exists():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        return 1
    if not gp.suffix == ".json":
        print("error: graph file must be a .json file", file=sys.stderr)
        return 1
    try:
        _raw = _load_raw_graph(gp)
        # `query` deliberately keeps the graph undirected (unlike `path` /
        # `explain`, which force directed=True): BFS/DFS here must explore
        # both callers and callees of the seed node to build useful
        # context, and forcing a DiGraph would make G.neighbors() return
        # successors only, silently dropping every caller-side result for
        # a seed with no outgoing edges. Direction is instead preserved
        # per-edge below (mirrors kglib/build.py's _src/_tgt pattern)
        # so the *rendering* stays correct without narrowing traversal.
        # Keep in-file markers when present: unconditionally
        # overwriting them with source/target would clobber the true
        # direction of a link persisted in flipped endpoint order.
        _raw = dict(
            _raw,
            links=[
                {
                    **link,
                    "_src": link.get("_src", link.get("source")),
                    "_tgt": link.get("_tgt", link.get("target")),
                }
                for link in _raw.get("links", [])
            ],
        )
        try:
            G = json_graph.node_link_graph(_raw, edges="links")
        except TypeError:
            G = json_graph.node_link_graph(_raw)
        _legacy_id_note(_raw)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: could not load graph: {exc}", file=sys.stderr)
        return 1
    _t0 = time.perf_counter()
    _mode = "dfs" if use_dfs else "bfs"
    _result = _query_graph_text(
        G,
        question,
        mode=_mode,
        depth=2,
        token_budget=budget,
        context_filters=context_filters or [],
    )
    querylog.log_query(
        kind="query",
        question=question,
        corpus=str(gp),
        result=_result,
        mode=_mode,
        depth=2,
        token_budget=budget,
        duration_ms=(time.perf_counter() - _t0) * 1000,
    )
    _touch_query_stamp(gp)
    print(_result)
    return 0


def run_path(
    source_label: str,
    target_label: str,
    *,
    graph_path: str | None = None,
    undirected: bool = False,
) -> int:
    """The `path` command flow (upstream: cli.py `path`). Prints the result."""
    from kglib.serve import _pick_scored_endpoint, _score_nodes
    from kglib.build import edge_datas
    from kglib import querylog

    gp = Path(graph_path or (Path(KG_OUT) / "graph.json")).resolve()
    if not gp.exists():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        return 1
    _raw = _load_raw_graph(gp)
    # Force directed so the renderer can recover stored caller→callee
    # direction, and multigraph so exact-pair parallel links (e.g. a
    # `references` and a `calls` edge between the same two nodes) survive load
    # instead of being silently collapsed last-writer-wins — otherwise the
    # printed relation could be one the traversed pair doesn't actually
    # carry. Local to this read; serve's shared graph is untouched.
    _raw = {**_raw, "directed": True, "multigraph": True}
    try:
        G = json_graph.node_link_graph(_raw, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(_raw)
    src_scored = _score_nodes(G, [t.lower() for t in source_label.split()])
    tgt_scored = _score_nodes(G, [t.lower() for t in target_label.split()])
    if not src_scored:
        print(f"No node matching '{source_label}' found.", file=sys.stderr)
        return 1
    if not tgt_scored:
        print(f"No node matching '{target_label}' found.", file=sys.stderr)
        return 1
    src_nid = _pick_scored_endpoint(G, src_scored, source_label)
    tgt_nid = _pick_scored_endpoint(G, tgt_scored, target_label)
    # Ambiguity guard: when both queries resolve to the same node, the
    # shortest path is trivially zero hops, which is almost never what the
    # caller wanted.
    if src_nid == tgt_nid:
        print(
            f"'{source_label}' and '{target_label}' both resolved to the same "
            f"node '{src_nid}'. Use a more specific label or the exact node ID.",
            file=sys.stderr,
        )
        return 1
    for _name, _scored, _nid in (
        ("source", src_scored, src_nid),
        ("target", tgt_scored, tgt_nid),
    ):
        # A close runner-up only made the resolution ambiguous when the raw
        # score head is what got picked; a full-token override was chosen on
        # token coverage, not score, so the head's margin is irrelevant.
        if len(_scored) >= 2 and _nid == _scored[0][1]:
            _top, _runner = _scored[0][0], _scored[1][0]
            if _top > 0 and (_top - _runner) / _top < 0.10:
                print(
                    f"warning: {_name} match was ambiguous "
                    f"(top score {_top:g}, runner-up {_runner:g})",
                    file=sys.stderr,
                )
    # Deterministic shortest path: hash-seeded neighbor views
    # returned an arbitrary route among equal-length paths that varied per
    # process. Build a sorted, materialized graph so neighbor order — and
    # thus the chosen path — is canonical for a given graph.json.
    try:
        if undirected:
            _und = nx.Graph()
            _und.add_nodes_from(sorted(G.nodes))
            _und.add_edges_from(sorted((min(u, v), max(u, v)) for u, v in G.edges()))
            path_nodes = nx.shortest_path(_und, src_nid, tgt_nid)
        else:
            # Directed by default. True direction is NOT raw arc
            # order: legacy canonicalized files persist a flipped arc with
            # _src/_tgt markers, so build the digraph from _src/_tgt
            # (falling back to the loaded arc) rather than to_directed().
            _dg = nx.DiGraph()
            _dg.add_nodes_from(sorted(G.nodes))
            _dg.add_edges_from(sorted(
                (d.get("_src", u), d.get("_tgt", v)) for u, v, d in G.edges(data=True)
            ))
            path_nodes = nx.shortest_path(_dg, src_nid, tgt_nid)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        if undirected:
            print(f"No path found between '{source_label}' and '{target_label}'.")
        else:
            print(
                f"No directed path found between '{source_label}' and "
                f"'{target_label}'. Re-run with --undirected to search "
                "ignoring edge direction."
            )
        return 0
    hops = len(path_nodes) - 1
    segments = []
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        # Report the ACTUAL stored relation(s) of the traversed pair and
        # direction — never a fabricated `calls`. A pair may carry
        # several parallel relations; show all, and fall back to an honest
        # "related" when the stored edge has no relation.
        # Direction truth lives in the per-link _src/_tgt markers:
        # undirected NetworkX storage canonicalizes endpoint order, so the
        # persisted source/target arc can be flipped relative to the real
        # caller→callee direction. Recover it from _src when present, else
        # fall back to the loaded arc tail (markerless canonical files keep
        # today's behavior).
        fwd, bwd = [], []
        for a, b in ((u, v), (v, u)):
            if G.has_edge(a, b):
                for d in edge_datas(G, a, b):
                    (fwd if d.get("_src", a) == u else bwd).append(d)
        datas = fwd or bwd
        forward = bool(fwd)
        rels = sorted({d.get("relation") for d in datas if d.get("relation")})
        rel = "/".join(rels) if rels else "related"
        confs = sorted({d.get("confidence") for d in datas if d.get("confidence")})
        conf_str = f" [{'/'.join(confs)}]" if confs else ""
        if i == 0:
            segments.append(G.nodes[u].get("label", u))
        if forward:
            segments.append(f"--{rel}{conf_str}--> {G.nodes[v].get('label', v)}")
        else:
            segments.append(f"<--{rel}{conf_str}-- {G.nodes[v].get('label', v)}")
    print(f"Shortest path ({hops} hops):\n  " + " ".join(segments))
    querylog.log_query(
        kind="path",
        question=f"{source_label} -> {target_label}",
        corpus=str(gp),
        nodes_returned=hops,
    )
    _touch_query_stamp(gp)
    return 0


def run_explain(label: str, *, graph_path: str | None = None) -> int:
    """The `explain` command flow (upstream: cli.py `explain`). Prints the result."""
    from kglib.serve import _find_node, find_node_ambiguity
    from kglib.build import edge_data
    from kglib import querylog

    gp = Path(graph_path or (Path(KG_OUT) / "graph.json")).resolve()
    if not gp.exists():
        print(f"error: graph file not found: {gp}", file=sys.stderr)
        return 1
    _raw = _load_raw_graph(gp)
    # Force directed so the renderer can recover stored caller→callee direction.
    _raw = {**_raw, "directed": True}
    try:
        G = json_graph.node_link_graph(_raw, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(_raw)
    matches = _find_node(G, label)
    if not matches:
        print(f"No node matching '{label}' found.")
        return 0
    rivals = find_node_ambiguity(G, label)
    if rivals:
        print(f"Ambiguous: '{label}' matches {len(rivals)} nodes in different files.")
        for rival in rivals:
            print(f"  {G.nodes[rival].get('source_file') or rival}")
            print(f"    id: {rival}")
        print("Retry with the root-relative path or the full node id.")
        return 1
    nid = matches[0]
    d = G.nodes[nid]
    print(f"Node: {d.get('label', nid)}")
    print(f"  ID:        {nid}")
    print(
        f"  Source:    {d.get('source_file', '')} {d.get('source_location', '')}".rstrip()
    )
    print(f"  Type:      {d.get('file_type', '')}")
    print(f"  Community: {d.get('community_name') or d.get('community', '')}")
    # Work-memory overlay: a derived experiential hint from `kg reflect`,
    # merged in display-only from the .kg_learning.json sidecar next to
    # graph.json. No line when the node has no overlay entry.
    try:
        from kglib.reflect import load_learning_overlay as _llo
        from kglib.security import sanitize_label as _sl
        _overlay = _llo(gp)
        _entry = _overlay.get(str(nid))
        if _entry:
            _status = _sl(str(_entry.get("status", "")))
            if _status == "contested":
                _line = (f"  Lesson: contested (useful {_entry.get('uses', 0)} / "
                         f"dead-end {_entry.get('neg', 0)})")
            elif _status == "preferred":
                _line = (f"  Lesson: preferred source (start here) — "
                         f"{_entry.get('uses', 0)} useful, score={_entry.get('score', 0)}")
            else:
                _line = (f"  Lesson: {_status or 'tentative'} — "
                         f"{_entry.get('uses', 0)} useful, score={_entry.get('score', 0)}")
            if _entry.get("stale"):
                _line += " [source changed since — re-verify]"
            print(_line)
    except Exception:
        pass
    print(f"  Degree:    {G.degree(nid)}")
    connections: list[tuple[str, str, dict]] = []  # (direction, neighbor_id, edge_data)
    # Classify by the edge's TRUE direction, not the loaded arc order:
    # a link persisted in flipped endpoint order carries its truth in the
    # per-edge _src marker. Markerless edges fall back to the arc
    # tail (today's behavior).
    for nb in G.successors(nid):
        _ed = edge_data(G, nid, nb)
        connections.append(
            ("out" if _ed.get("_src", nid) == nid else "in", nb, _ed)
        )
    for nb in G.predecessors(nid):
        _ed = edge_data(G, nb, nid)
        connections.append(
            ("in" if _ed.get("_src", nb) == nb else "out", nb, _ed)
        )
    if connections:
        print(f"\nConnections ({len(connections)}):")
        connections.sort(key=lambda c: G.degree(c[1]), reverse=True)
        for direction, nb, edata in connections[:20]:
            rel = edata.get("relation", "")
            conf = edata.get("confidence", "")
            arrow = "-->" if direction == "out" else "<--"
            # Append the edge's location — the actual reference SITE (in the
            # citing file for an incoming edge), not a def line (#BUG1).
            # Labeled by [rel] so the meaning is unambiguous.
            loc = edata.get("source_location") or ""
            sfile = edata.get("source_file") or ""
            at = f" {sfile}:{loc}" if loc else ""
            print(f"  {arrow} {G.nodes[nb].get('label', nb)} [{rel}] [{conf}]{at}")
        if len(connections) > 20:
            remainder = connections[20:]
            print(f"  ... and {len(remainder)} more")
            # A bare count silently hides the answer on high-degree
            # nodes ("what references this, what's the impact?"). Group the cut
            # connections by direction + file so their shape is visible
            # without falling back to a corpus-wide grep.
            by_file: dict[tuple[str, str], int] = {}
            for direction, _nb, edata in remainder:
                sfile = edata.get("source_file") or "(unknown file)"
                key = (direction, sfile)
                by_file[key] = by_file.get(key, 0) + 1
            # Count desc, then (direction, file) so equal-count groups have a
            # byte-stable order (not the degree-derived insertion order).
            grouped = sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))
            print("  Grouped by file:")
            for (direction, sfile), count in grouped[:20]:
                arrow = "-->" if direction == "out" else "<--"
                noun = "connection" if count == 1 else "connections"
                print(f"    {arrow} {sfile}: {count} {noun}")
            if len(grouped) > 20:
                print(f"    ... and {len(grouped) - 20} more files")
    querylog.log_query(
        kind="explain",
        question=label,
        corpus=str(gp),
        nodes_returned=len(connections),
    )
    _touch_query_stamp(gp)
    return 0


def run_save_result(
    question: str,
    answer: str,
    *,
    query_type: str = "query",
    nodes: list[str] | None = None,
    outcome: str | None = None,
    correction: str | None = None,
    memory_dir: Path | None = None,
) -> Path:
    """The `save-result` command flow (upstream: cli.py `save-result`)."""
    from kglib.ingest import save_query_result as _sqr

    out = _sqr(
        question=question,
        answer=answer,
        memory_dir=Path(memory_dir) if memory_dir else Path(KG_OUT) / "memory",
        query_type=query_type,
        source_nodes=nodes or None,
        outcome=outcome,
        correction=correction,
    )
    print(f"Saved to {out}")
    return out


def run_cluster_only(
    root: Path,
    *,
    graph_override: Path | None = None,
    resolution: float = 1.0,
    exclude_hubs: float | None = None,
    no_viz: bool = False,
    min_community_size: int = 3,
) -> int:
    """The self-contained `cluster-only` flow (upstream: cli.py `cluster-only`).

    Re-clusters the existing graph, names communities, and regenerates
    GRAPH_REPORT.md, graph.json and graph.html. kg has no LLM labeling
    backend: saved labels are reused when their community membership
    signatures still match, everything else is named by its structural hub.
    """
    from kglib.build import build_from_json
    from kglib.cluster import (
        cluster,
        score_all,
        remap_communities_to_previous,
        community_member_sigs,
        label_communities_by_hub,
    )
    from kglib.analyze import god_nodes, surprising_connections, suggest_questions
    from kglib.report import generate
    from kglib.export import to_json, to_html

    watch_path = Path(root)
    graph_json = graph_override if graph_override is not None else watch_path / KG_OUT / "graph.json"
    if not graph_json.exists():
        print(
            f"error: no graph found at {graph_json} — run /kg first",
            file=sys.stderr,
        )
        return 1

    print("Loading existing graph...")
    # Solution 3: don't hard-exit on an oversized graph.json here.
    # Core outputs (graph.json + GRAPH_REPORT.md) still get written; the
    # graph.html render below falls back to the community-aggregation view
    # (node_limit=5000) when over the cap.
    from kglib.security import check_graph_file_size_cap as _check_cap
    _over_cap = False
    try:
        _check_cap(graph_json)
    except ValueError:
        _over_cap = True
        try:
            _over_cap_bytes = graph_json.stat().st_size
        except OSError:
            _over_cap_bytes = -1
        print(
            f"warning: graph.json exceeds cap ({_over_cap_bytes} bytes); "
            f"falling back to community-aggregation view (node_limit=5000)",
            file=sys.stderr,
        )
    _raw = json.loads(graph_json.read_text(encoding="utf-8"))
    _directed = bool(_raw.get("directed", False))
    G = build_from_json(_raw, directed=_directed)
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print("Re-clustering...")
    communities = cluster(G, resolution=resolution, exclude_hubs_percentile=exclude_hubs)
    # Mirror the watch/update path: map new cids to prior ones by
    # node-overlap so the existing .kg_labels.json keeps attaching
    # to the same conceptual community after re-clustering. Without this,
    # labels follow raw cid index and become misaligned whenever the
    # graph has changed between labeling and cluster-only.
    previous_node_community = {
        n["id"]: n["community"]
        for n in _raw.get("nodes", [])
        if n.get("community") is not None and n.get("id") is not None
    }
    if previous_node_community:
        communities = remap_communities_to_previous(communities, previous_node_community)
    cohesion = score_all(G, communities)
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    # Where outputs (GRAPH_REPORT.md, re-clustered graph.json, labels,
    # analysis, html) land. When `--graph` points at a graph INSIDE a
    # kg-out/ dir (another project/tenant's output), write beside it,
    # not into a stray kg-out/ in the CWD. But when `--graph`
    # points at an arbitrary path — e.g. a `backup/graph.json` archived
    # before re-clustering — fall back to the CWD's kg-out/,
    # which is the restore-into-place workflow that test pins. The default
    # (no --graph) case already has graph_json under watch_path/kg-out.
    _out_name = Path(KG_OUT).name
    if graph_override is not None and graph_json.parent.name == _out_name:
        out = graph_json.parent
    else:
        out = watch_path / KG_OUT
    out.mkdir(parents=True, exist_ok=True)
    labels_path = out / ".kg_labels.json"
    existing_labels: dict[int, str] = {}
    if labels_path.exists():
        try:
            existing_labels = {
                int(k): v
                for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()
                if isinstance(v, str)
            }
        except Exception:
            existing_labels = {}
    if labels_path.exists():
        # Reuse saved labels, but don't blindly trust them: the graph may have
        # been re-scoped/re-clustered since labeling, in which case a cid now
        # covers a DIFFERENT community and its old (curated) name is wrong
        # (#label-stale). Validate each community against the membership
        # signature saved beside the labels; any community that changed (or has
        # no saved label) is renamed by its current hub — deterministic and
        # correct-by-construction. Unchanged communities keep their saved
        # label. When no signature sidecar exists (labels predate this),
        # fall back to hub-filling only the communities missing a label.
        sig_path = labels_path.parent / (labels_path.name + ".sig")
        saved_sigs: dict[int, str] = {}
        if sig_path.exists():
            try:
                saved_sigs = {
                    int(k): v for k, v in
                    json.loads(sig_path.read_text(encoding="utf-8")).items()
                    if isinstance(v, str)
                }
            except Exception:
                saved_sigs = {}
        cur_sigs = community_member_sigs(communities)
        count_mismatch = len(existing_labels) != len(communities)
        labels = {}
        hub_labels: dict[int, str] | None = None
        changed = 0
        for cid in communities:
            # A persisted "Community {cid}" is a placeholder, not an earned
            # label — treat it as absent so the hub labeler replaces it and an
            # already-polluted sidecar heals instead of suppressing real
            # labels forever.
            have_label = (
                cid in existing_labels
                and existing_labels[cid] != f"Community {cid}"
            )
            if saved_sigs:
                # Precise: the membership signature tells us if this exact
                # community changed since it was labeled.
                fresh = have_label and saved_sigs.get(cid) == cur_sigs.get(cid)
            else:
                # No signature sidecar (labels predate it). A differing community
                # COUNT means the labels describe a different clustering, so a cid's
                # old label can't be trusted; equal count is the best "same" signal.
                fresh = have_label and not count_mismatch
            if fresh:
                labels[cid] = existing_labels[cid]
            else:
                if hub_labels is None:
                    hub_labels = label_communities_by_hub(G, communities)
                labels[cid] = hub_labels[cid]
                if have_label:
                    changed += 1
        if changed:
            print(
                f"[kg] community set changed since labeling "
                f"({len(existing_labels)} saved labels, {len(communities)} communities now; "
                f"renamed {changed} community(ies) by their hub). "
                f"Re-run the /kg relabel step to refresh names.",
                file=sys.stderr,
            )
    else:
        # No labels file yet. When run standalone there is no orchestrating
        # agent to do the skill's labeling step, so auto-name communities
        # after their highest-degree hub rather than leave "Community N".
        # kg has no LLM backend to override these.
        print("Labeling communities...")
        labels = label_communities_by_hub(G, communities)
    questions = suggest_questions(G, communities, labels)
    # cluster-only re-clusters an EXISTING graph: the content is exactly
    # what the build saw, so keep the build-time commit stamp instead of
    # re-deriving it from the shell's cwd.
    _commit = _raw.get("built_at_commit")
    if not _commit:
        from kglib.export import _git_head as _gh
        _commit = _gh(cwd=watch_path)
    # Snapshot BEFORE any artifact is replaced: GRAPH_REPORT.md was written
    # first, so the dated folder held the NEW report, not the previous.
    from kglib.export import backup_if_protected as _backup
    _backup(out)
    # The shrink-guard can refuse this write, so it goes before the sidecars —
    # a report and labels describing a clustering graph.json does not contain
    # are worse than no run at all.
    if not to_json(G, communities, str(out / "graph.json"),
                   community_labels=labels, built_at_commit=_commit):
        print(
            "graph.json NOT written: refusing to overwrite (see warning above). "
            "GRAPH_REPORT.md, .kg_labels.json and .kg_analysis.json "
            "left untouched.",
            file=sys.stderr,
        )
        return 1
    tokens = {"input": 0, "output": 0}
    from kglib.report import load_learning_for_report as _llfr
    report = generate(G, communities, cohesion, labels, gods, surprises,
                      {"warning": "cluster-only mode — file stats not available"},
                      tokens, str(watch_path), suggested_questions=questions,
                      min_community_size=min_community_size, built_at_commit=_commit,
                      learning=_llfr(out / "graph.json"))
    (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    analysis = {
        "communities": {str(k): v for k, v in communities.items()},
        "cohesion": {str(k): v for k, v in cohesion.items()},
        "gods": gods,
        "surprises": surprises,
        "questions": questions,
    }
    (out / ".kg_analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_json_atomic(labels_path, {str(k): v for k, v in labels.items()}, ensure_ascii=False)
    # Membership signatures beside the labels so a later cluster-only can
    # detect which communities changed and avoid reusing a stale label
    # (see reuse above).
    (labels_path.parent / (labels_path.name + ".sig")).write_text(
        json.dumps({str(k): v for k, v in community_member_sigs(communities).items()}), encoding="utf-8")

    # Mirror watch.py pattern: gate to_html so core outputs (graph.json +
    # GRAPH_REPORT.md) always land. Honor --no-viz explicitly; otherwise
    # fall back to ValueError handling so an oversized graph doesn't crash
    # mid-write and leave a stale graph.html on disk.
    html_target = out / "graph.html"
    if no_viz:
        if html_target.exists():
            html_target.unlink()
        print(f"Done - {len(communities)} communities. GRAPH_REPORT.md and graph.json updated (--no-viz; graph.html removed).")
    else:
        try:
            # Over-cap fallback: force the community-aggregation
            # path so an oversized graph still renders a usable graph.html.
            _node_limit = 5000 if _over_cap else None
            to_html(G, communities, str(html_target), community_labels=labels or None,
                    node_limit=_node_limit)
            print(f"Done - {len(communities)} communities. GRAPH_REPORT.md, graph.json and graph.html updated.")
        except ValueError as viz_err:
            if html_target.exists():
                html_target.unlink()
            print(f"Skipped graph.html: {viz_err}")
            print(f"Done - {len(communities)} communities. GRAPH_REPORT.md and graph.json updated.")
    return 0
