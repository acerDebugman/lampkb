# kg skill — context

Glossary and decisions for the `kg` skill (`SKILL.md` in this directory) and its bundled pipeline (`scripts/`).

## Glossary

- **kg**: a self-contained skill that turns a document corpus into a persistent knowledge graph. Derived from graphify's agents-platform skill; the pipeline logic is vendored and slimmed from graphify into `scripts/kglib/`.
- **Skill-load-time file read**: the host's skill system injecting companion files into context mid-run. The target host does NOT have this for *instructions* — which is why SKILL.md is a single self-contained file.
- **Runtime execution**: the host running bash. All deterministic logic is executed as `uv run "$KG_SCRIPTS/kg.py" <subcommand>`; scripts are a black-box CLI, never read into context.
- **Self-extraction**: semantic extraction performed by the host agent itself (read documents in batches → emit extraction JSON per the inlined spec). The host is the LLM; there are no subagents and no external LLM API.
- **kg-out/**: the output directory for all artifacts. Hardcoded as the default in `kglib/paths.py`; no environment variable. All sidecar files inside it are named `.kg_*` (e.g. `.kg_detect.json`, `.kg_batches.json`, `.kg_labels.json`).
- **KG_SCRIPTS**: absolute path of the `scripts/` directory beside SKILL.md, resolved by the agent at the start of each invocation.
- **.kgignore**: corpus-side ignore file (renamed from upstream's `.graphifyignore`).

## Decisions

### Session 1 — monolith assembly (from graphify split skill)

- Derived by hand from `graphify/skill-agents.md` as a one-off snapshot; NOT registered in graphify's `tools/skillgen`, not auto-synced with upstream.
- Documents only: no AST/code pass, no video transcription, no image/vision rules.
- No subagents, no Gemini, no API keys; self-extraction batches capped at ≤10 files or ~30k words.
- Frontmatter `name: kg`; slash-command `/kg`; output dir `kg-out/`.

### Session 2 — taoskg standalone project

- **Full de-coupling from graphify**: neither the `graphify` CLI nor the `graphifyy` library is a dependency. Needed logic is vendored into `scripts/kglib/` (copy-then-slim, preserving #issue-tagged behavior contracts such as the #479 shrink-guard, replace-on-re-extract, and manifest stamping rules #1417/#1908/#1948/#2015). Vendored files carry an attribution header. (Issue-number comments were later stripped from the code; upstream provenance for ported tests remains in `tests/ported/README.md`.)
- **Layout**: single entry `scripts/kg.py` (PEP 723 header declares `networkx`, `rapidfuzz`, `datasketch`, `pypdf`; `requires-python >= 3.10`) + `scripts/kglib/` package located via `__file__`. Zero install; the skill directory is portable as a unit.
- **Feature boundary**: full build, `--update`, `--cluster-only`, `query`/`path`/`explain` + save-result/reflect, benchmark, PDF text extraction. Dropped: graphml/neo4j/falkordb/mcp/wiki exports, `--svg`, office docs (docx/xlsx), all tree-sitter/code/image/video paths. Dependency set is exactly the four above.
- **Subcommand surface**: `prepare` (detect + cache check + batch planning), `merge-extraction`, `build`, `diagnose`, `relabel`, `export-html`, `finalize`, `update-detect` (also cache-checks and batch-plans the changed subset), `update-merge`, `diff`, `cluster-only`, `query`, `path`, `explain`, `save-result`, `reflect`, `vocab`, `benchmark`. LLM-judgment work stays in SKILL.md: the extraction loop, community labeling, query token selection, answer composition.
- **Config surface**: `GRAPHIFY_OUT` env var deleted (default `kg-out` baked in); `.graphify_python` interpreter file and interpreter-detection bash deleted (uv owns the environment); `.graphify_root` mechanism deleted (all flows take explicit paths; `update-merge` always passes `root=`).
- **Branding**: all kg-out sidecars renamed `.graphify_*` → `.kg_*`; corpus ignore file `.graphifyignore` → `.kgignore` (user-facing behavior change: legacy `.graphifyignore` files are no longer read); default opt-in query log renamed to `~/.cache/kg-queries.log` (env vars `KG_QUERY_LOG*`); remaining `GRAPHIFY_*` env vars renamed `KG_*`. Sanctioned leftovers: vendored-from attribution headers, provenance prose.
- **Verification**: (1) deterministic equivalence tests — fixed extraction JSON fixture → kglib vs upstream graphify build/query outputs identical; (2) smoke tests — full pipeline incl. update/shrink-guard paths; (3) upstream unit tests ported (202 cases under `tests/ported/`, provenance in `tests/ported/README.md`). Suite: 216 tests, `uv run --with pytest pytest tests/` from the taoskg root.
