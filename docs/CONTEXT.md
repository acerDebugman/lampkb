# kg skill — context

Glossary and decisions for the `kg` skill (`skills/kg/SKILL.md`) and its bundled pipeline (`skills/kg/scripts/`).

## Glossary

- **kg**: a self-contained skill that turns a document corpus into a persistent knowledge graph. Derived from graphify's agents-platform skill; the pipeline logic is vendored and slimmed from graphify into `skills/kg/scripts/kglib/`.
- **Skill-load-time file read**: the host's skill system injecting companion files into context mid-run. The target host does NOT have this for *instructions* — which is why SKILL.md is a single self-contained file.
- **Runtime execution**: the host running bash. All deterministic logic is executed as `uv run "$KG_SCRIPTS/kg.py" <subcommand>`; scripts are a black-box CLI, never read into context.
- **Self-extraction**: semantic extraction performed by the host agent itself (read documents in batches → emit extraction JSON per the inlined spec). The host is the LLM; there are no subagents and no external LLM API.
- **kg-out/**: the output directory for all artifacts. Hardcoded as the default in `kglib/paths.py`; no environment variable. All sidecar files inside it are named `.kg_*` (e.g. `.kg_detect.json`, `.kg_batches.json`, `.kg_labels.json`).
- **KG_SCRIPTS**: absolute path of the `skills/kg/scripts/` directory, resolved by the agent at the start of each invocation.
- **.kgignore**: corpus-side ignore file (renamed from upstream's `.graphifyignore`).

### 抽取分类法 (Extraction taxonomy)

- **实体类型 (entity_type)**: 节点的分类字段，闭集 9 值，存英文 key：`concept`(概念)、`principle`(原理)、`method`(方法)、`rule`(规则)、`procedure`(操作)、`fact`(事实)、`scenario`(场景)、`keypoint`(要点)、`document`(文档)。语义定义见 ADR-0001 / SKILL.md 抽取规范。
  _Avoid_: file_type（旧字段名）、paper、rationale、code、image（已废弃的旧类型值）
- **关系 (relation)**: 边的语义类型，闭集 11 值，直存中文：阐述、归属、组成、前置、解决、导致、限制、顺序、推导、适用、影响。均为二元关系，无超边。
  _Avoid_: references、cites、conceptually_related_to、semantically_similar_to、rationale_for、participate_in、form（已废弃的旧关系值）
- **定义 (definition)**: 实体的必备属性（允许空串）。仅当文档中存在对该实体的明确定义（术语表条目、"X 是指…"式表述）时提取原文；不存在时置为空字符串，禁止模型自行总结或编造。

## Decisions

### Session 1 — monolith assembly (from graphify split skill)

- Derived by hand from `graphify/skill-agents.md` as a one-off snapshot; NOT registered in graphify's `tools/skillgen`, not auto-synced with upstream.
- Documents only: no AST/code pass, no video transcription, no image/vision rules.
- No subagents, no Gemini, no API keys; self-extraction batches capped at ≤10 files or ~30k words.
- Frontmatter `name: kg`; slash-command `/kg`; output dir `kg-out/`.

### Session 2 — lampkb standalone project

- **Full de-coupling from graphify**: neither the `graphify` CLI nor the `graphifyy` library is a dependency. Needed logic is vendored into `skills/kg/scripts/kglib/` (copy-then-slim, preserving #issue-tagged behavior contracts such as the #479 shrink-guard, replace-on-re-extract, and manifest stamping rules #1417/#1908/#1948/#2015). Vendored files carry an attribution header. (Issue-number comments were later stripped from the code; upstream provenance for ported tests remains in `tests/ported/README.md`.)
- **Layout**: single entry `skills/kg/scripts/kg.py` (PEP 723 header declares `networkx`, `rapidfuzz`, `datasketch`, `pypdf`; `requires-python >= 3.10`) + `skills/kg/scripts/kglib/` package located via `__file__`. Zero install; the skill directory is portable as a unit.
- **Feature boundary**: full build, `--update`, `--cluster-only`, `query`/`path`/`explain` + save-result/reflect, benchmark, PDF text extraction. Dropped: graphml/neo4j/falkordb/mcp/wiki exports, `--svg`, office docs (docx/xlsx), all tree-sitter/code/image/video paths. Dependency set is exactly the four above.
- **Subcommand surface**: `prepare` (detect + cache check + batch planning), `merge-extraction`, `build`, `diagnose`, `relabel`, `export-html`, `finalize`, `update-detect` (also cache-checks and batch-plans the changed subset), `update-merge`, `diff`, `cluster-only`, `query`, `path`, `explain`, `save-result`, `reflect`, `vocab`, `benchmark`. LLM-judgment work stays in SKILL.md: the extraction loop, community labeling, query token selection, answer composition.
- **Config surface**: `GRAPHIFY_OUT` env var deleted (default `kg-out` baked in); `.graphify_python` interpreter file and interpreter-detection bash deleted (uv owns the environment); `.graphify_root` mechanism deleted (all flows take explicit paths; `update-merge` always passes `root=`).
- **Branding**: all kg-out sidecars renamed `.graphify_*` → `.kg_*`; corpus ignore file `.graphifyignore` → `.kgignore` (user-facing behavior change: legacy `.graphifyignore` files are no longer read); default opt-in query log renamed to `~/.cache/kg-queries.log` (env vars `KG_QUERY_LOG*`); remaining `GRAPHIFY_*` env vars renamed `KG_*`. Sanctioned leftovers: vendored-from attribution headers, provenance prose.
- **Verification**: (1) deterministic equivalence tests — fixed extraction JSON fixture → kglib vs upstream graphify build/query outputs identical; (2) smoke tests — full pipeline incl. update/shrink-guard paths; (3) upstream unit tests ported (202 cases under `tests/ported/`, provenance in `tests/ported/README.md`). Suite: 216 tests, `uv run --with pytest pytest tests/` from the lampkb root.

### Session 3 — 抽取分类法替换（2026-09-10，详见 docs/adr/0001）

- 实体/关系分类法整体替换为面向中文文档语料的新体系：9 种实体类型（英文 key）+ 11 种关系（中文值），实体新增必备属性 `definition`（无定义置空串，禁止编造）。
- 字段 `file_type` 更名为 `entity_type`；旧类型值（paper/rationale/code/image）与旧关系值全部废弃，不做旧数据兼容、不做同义词映射（开发阶段）；关系漂移归一化表从空开始。
- `validate.py` 新增关系白名单校验；`definition` 纳入节点必填字段（空串合法）。
- 删除：超边（hyperedge）机制整条流水线；calls/imports 等代码场景守卫；`semantically_similar_to`/`conceptually_related_to` 等旧关系特判；`tests/test_equivalence.py`——自本次改造起与上游 graphify 彻底分道扬镳。
- 存量产物（kg-out/、kg-all3-0820/）不迁移、不重跑；export HTML 节点卡片增加 definition 展示（非空时）。
