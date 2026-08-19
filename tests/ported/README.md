# Ported upstream unit tests

Each file ports a docs-relevant subset of an upstream graphify test module,
with imports rewritten `graphify` → `kglib`. Upstream source (checkout at
`../../../graphify/tests/`) noted per file.

| File | Ports from | Notes |
| --- | --- | --- |
| `test_detect_ported.py` | `test_detect.py` | Classification + `_is_sensitive` cases verbatim; `test_detect_skips_noise_dirs` adapted from `test_detect_skips_noise_dot_dirs` to tmp_path (upstream used a fixture dir); `test_detect_skips_sensitive_files` added end-to-end; sweep added `count_words`, small-corpus warning, and the `.kgignore` set (incl. the NFC/NFD macOS cases). Office-classification divergence pinned by `test_classify_office_is_unclassified_in_kg`. Code-only cases (powershell manifests, tree-sitter fixtures, shebang env parsing) dropped. |
| `test_cache_ported.py` | `test_cache.py` | `file_hash` + semantic-cache roundtrip/invalidation + #1757 out-of-scope guard verbatim; `test_semantic_cache_check_returns_uncached` added to cover `check_semantic_cache` directly. AST-cache version-bump tests dropped (kg has no AST cache). |
| `test_cache_portability_ported.py` | `test_word_count_cache.py` (in full), `test_cache.py` | #1656 word-count stat-cache, #1989 salt-keyed digests, #777 source_file portability (kind switched `ast` → `semantic` since kg has no AST cache), #1894 deep-mode namespace pair. `prune_semantic_cache` tests NOT ported (function dropped in kg), noted below. |
| `test_build_ported.py` | `test_build.py` | Generic `build_from_json`/`build`/`build_merge` cases: dedupe helpers, fixture counts (against `tests/fixtures/extraction_docs.json`), weight normalization (#1960), legacy alias folds (#2194), id re-key (#1504/#2197/#2618), path handling (#932/#1279), file_type canonicalization (#660/#840), semantic-tier ghost merges (#2068/#1753), directed-flag inheritance (#2342), bidirectional-pair direction (#1061), edge_data(s) (#796), malformed-input tolerance, doc-twin merge (#1799), hyperedge member revalidation (#1916), prune-root recovery (#2446). Dropped as code/AST-only: AST-twin ghost merges, old-stem alias cases (C/C++ include fallback), cross-language phantom guards (#1749), two-tier AST/semantic build_merge tests, MCP id tests (#2408), and `test_build_merge_preserves_call_edge_direction` (imports the JS extractor). |
| `test_build_merge_ported.py` | `test_build_merge_shrink_guard.py`, `test_build_merge_hyperedges_and_prune.py` | #2497 identity shrink guard, #1796 replace-wins-over-delete, #1574 hyperedge carry-over. Canned graph layout writes `kg-out/graph.json` instead of `graphify-out/graph.json` (`_infer_merge_root` keys on the out-dir name). |
| `test_export_ported.py` | `test_export.py` | #479 shrink-guard trio + #1775 null-label html + `existing_graph_node_count`. obsidian/canvas/cypher/graphml/svg tests dropped (exporters not vendored). |
| `test_serve_ported.py` | `test_serve.py` | `_score_nodes` / `_query_terms` cases verbatim except canned `source_file` values renamed `.py` → `.md` (cosmetic docs-only adaptation). `_communities_from_graph` tests NOT ported — that helper lives in upstream's MCP-server section and was not vendored. |
| `test_serve_engine_ported.py` | `test_serve.py` | Second pass: `_find_node` tiers (#1704/#2467), trigram prefilter + prefilter-identical-to-full-scan invariants, `_bfs`/`_dfs`, `_subgraph_to_text` incl. work-memory overlay annotation, context filters, German/CJK query handling (#1900). `_load_graph` tests NOT ported (loader is MCP-server machinery, not vendored). |
| `test_query_induced_edges_ported.py` | `test_query_induced_edges.py` | Full port (#2323 induced-edge completion). `_complete_induced_edges` survived vendoring in `kglib/serve.py`; the CLI end-to-end case drives `kglib.flow.run_query` instead of `graphify.__main__`. |
| `test_query_cli_ported.py` | `test_query_cli.py` | All 5 cases, mapped from argv plumbing onto `kglib.flow.run_query` (kg's port of the query command). Oversized-graph rejection monkeypatches `kglib.security._MAX_GRAPH_FILE_BYTES`. |
| `test_querylog_ported.py` | `test_querylog.py` | Full port (20 cases); env vars adapted `GRAPHIFY_QUERY_LOG*` → `KG_QUERY_LOG*`. The default opt-in log filename was renamed to `kg-queries.log` (kg branding decision, differs from upstream's `graphify-queries.log`) — pinned by test. |

Notable intentional divergences (also covered by comments in the tests):

- kg classifies `.docx` / `.xlsx` as *unclassified* (upstream converts them to
  markdown sidecars). Documents-only scope: reported, never parsed.
- kg has no AST cache: `cache_dir` ignores the `"ast"` version namespace and
  `save_cached`/`load_cached` default to `kind="semantic"`.
- `dedup` keeps the deterministic pipeline but the LLM tiebreaker
  (`dedup_llm_backend`) is dropped — passing a backend prints a warning and
  skips (kg never calls an LLM).
- Dropped kglib functions whose upstream tests were therefore not ported:
  `prune_semantic_cache`, `cached_files`, `clear_cache` (cache management),
  `_load_graph`, `_communities_from_graph`, `_shortest_path_text` (MCP/server
  surface), `prefix_graph_for_global` & friends (multi-repo merge).
- `test_serve_http.py` intentionally skipped (MCP/HTTP server not vendored).
