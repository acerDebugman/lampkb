# kg skill — context

Glossary and decisions for the `kg` skill (`SKILL.md` in this directory) and its bundled pipeline (`scripts/`).

## Glossary

- **kg**: a self-contained skill that turns a document corpus into a persistent knowledge graph. Derived from graphify's agents-platform skill; the pipeline logic is vendored and slimmed from graphify into `scripts/kglib/`.
- **Skill-load-time file read**: the host's skill system injecting companion files into context mid-run. The target host does NOT have this for *instructions* — which is why SKILL.md is a single self-contained file.
- **Runtime execution**: the host running bash. All deterministic logic is executed as `uv run "$KG_SCRIPTS/kg.py" <subcommand>`; scripts are a black-box CLI, never read into context.
- **Self-extraction**: semantic extraction performed by the host agent itself (read documents in batches → emit extraction JSON per the inlined spec). The host is the LLM; there are no subagents and no external LLM API.
- **kg-out/**: the output directory for all artifacts. Hardcoded as the default in `kglib/paths.py`; no environment variable.
- **KG_SCRIPTS**: absolute path of the `scripts/` directory beside SKILL.md, resolved by the agent at the start of each invocation.

## Decisions

### Session 1 — monolith assembly (from graphify split skill)

- Derived by hand from `graphify/skill-agents.md` as a one-off snapshot; NOT registered in graphify's `tools/skillgen`, not auto-synced with upstream.
- Documents only: no AST/code pass, no video transcription, no image/vision rules.
- No subagents, no Gemini, no API keys; self-extraction batches capped at ≤10 files or ~30k words.
- Frontmatter `name: kg`; slash-command `/kg`; output dir `kg-out/`.

### Session 2 — taoskg standalone project

- **Full de-coupling from graphify**: neither the `graphify` CLI nor the `graphifyy` library is a dependency. Needed logic is vendored into `scripts/kglib/` (copy-then-slim, preserving #issue-tagged behavior contracts such as the #479 shrink-guard, replace-on-re-extract, and manifest stamping rules #1417/#1908/#1948/#2015). Vendored files carry an attribution header and keep issue-number comments.
- **Layout**: single entry `scripts/kg.py` (PEP 723 header declares `networkx`, `rapidfuzz`, `datasketch`, `pypdf`; `requires-python >= 3.10`) + `scripts/kglib/` package located via `__file__`. Zero install; the skill directory is portable as a unit.
- **Feature boundary**: full build, `--update`, `--cluster-only`, `query`/`path`/`explain` + save-result/reflect, benchmark, PDF text extraction. Dropped: graphml/neo4j/falkordb/mcp/wiki exports, `--svg`, office docs (docx/xlsx), all tree-sitter/code/image/video paths. Dependency set is exactly the four above.
- **Subcommand surface**: `prepare` (detect + cache check + batch planning), `merge-extraction`, `build`, `diagnose`, `relabel`, `export-html`, `finalize`, `update-detect` (also cache-checks and batch-plans the changed subset), `update-merge`, `diff`, `cluster-only`, `query`, `path`, `explain`, `save-result`, `reflect`, `vocab`, `benchmark`. LLM-judgment work stays in SKILL.md: the extraction loop, community labeling, query token selection, answer composition.
- **Config surface**: `GRAPHIFY_OUT` env var deleted (default `kg-out` baked in); `.graphify_python` interpreter file and interpreter-detection bash deleted (uv owns the environment); `.graphify_root` deleted.
- **Verification**: (1) deterministic equivalence tests — fixed extraction JSON fixture → compare kglib vs upstream graphify build/query outputs; (2) smoke test — small md corpus end-to-end; (3) a partial port of upstream unit tests (detect/cache/build/query docs-only cases) done up front so the harness exists. Tests live in `taoskg/tests/`, run via `uv run --with pytest pytest`.
