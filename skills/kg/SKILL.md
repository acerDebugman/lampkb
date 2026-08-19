---
name: kg
description: "Use for questions about a document corpus — concepts, structure, and cross-document relationships — backed by a persistent knowledge graph. Documents only (no code, images, or video); the host agent performs semantic extraction itself by reading files inline. Self-contained: pipeline runs via `uv run` on bundled scripts, no graphify CLI or library needed."
---
# /kg

Turn any folder of documents into a navigable knowledge graph with community detection, an honest audit trail, and three outputs: interactive HTML, GraphRAG-ready JSON, and a plain-language GRAPH_REPORT.md.

This is a self-contained skill: all deterministic logic lives in bundled Python scripts executed via `uv`; all LLM work (semantic extraction, labeling, answering) is done by you, the host agent.

## Usage

```
/kg                                             # full pipeline on current directory → HTML viz
/kg <path>                                      # full pipeline on specific path
/kg <path> --mode deep                          # thorough extraction, richer INFERRED edges
/kg <path> --update                             # incremental - re-extract only new/changed files
/kg <path> --directed                           # build directed graph (preserves edge direction: source→target)
/kg <path> --cluster-only                       # rerun clustering on existing graph
/kg <path> --no-viz                             # skip visualization, just report + JSON
/kg query "<question>"                          # BFS traversal - broad context
/kg query "<question>" --dfs                    # DFS - trace a specific path
/kg query "<question>" --budget 1500            # cap answer at N tokens
/kg path "AuthModule" "Database"                # shortest path between two concepts
/kg explain "SwinTransformer"                   # plain-language explanation of a node
```

## What kg is for

Drop any folder of documents (notes, markdown, papers, text) into kg and get a queryable knowledge graph. Persistent across sessions, honest audit trail (EXTRACTED/INFERRED/AMBIGUOUS), community detection surfaces cross-document connections you wouldn't think to ask about.

## Host model — read this first

This skill assumes a host with these exact capabilities:

- **uv is installed.** Every pipeline step runs as `uv run "$KG_SCRIPTS/kg.py" <subcommand>`. uv resolves the scripts' declared dependencies (networkx, rapidfuzz, datasketch, pypdf) automatically into an ephemeral environment — there is no install step, no venv to manage, and no `graphify` CLI or library involved.
- **Bundled scripts are executed, never read.** All deterministic logic lives in `scripts/` beside this SKILL.md. You never need to open them — treat them as a black-box CLI.
- **You read corpus documents yourself**: for semantic extraction (Step 3) YOU are the LLM. You read each document with your own file-reading tool and produce extraction JSON. There are no subagents — never attempt to dispatch one.
- **Documents only**: code, image, audio, and video files are ignored by this skill. If the corpus is mostly those, stop and tell the user this skill is documents-only. PDFs are supported (text-layer extraction via pypdf).
- **No API key needed, ever.** Never ask the user for one. Semantic extraction is done by you, not by an external LLM.

**Resolve KG_SCRIPTS first**: at the start of any invocation, set `KG_SCRIPTS` to the absolute path of the `scripts/` directory beside this SKILL.md (i.e. `<dir containing this SKILL.md>/scripts`). Every command below uses it.

**Output directory is `kg-out/`** under the current working directory. All artifacts, sidecars, cache, and exports live there. The scripts default to it — no environment variables to set.

## What You Must Do When Invoked

If the user invoked `/kg --help` or `/kg -h` (with no other arguments), print the contents of the `## Usage` section above verbatim and stop. Do not run any commands, do not detect files, do not default the path to `.`. Just print the Usage block and return.

**Fast path — existing graph:** Before doing anything else, check whether `kg-out/graph.json` exists (relative to the current working directory). If it exists AND the user's request is a natural-language question about the corpus (e.g. "How does X relate to Y?", "What does the corpus say about Z?") and NOT an explicit rebuild command (`--update`, `--cluster-only`, or a bare path that implies fresh extraction): **skip Steps 1–5 entirely and jump straight to `## For /kg query`.** Run the query flow immediately. Do not run prepare. The graph is already built — use it.

If no path was given, use `.` (current directory). Do not ask the user for a path.

Follow these steps in order. Do not skip steps.

### Step 1 - Ensure uv is available

```bash
command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
```

If uv is missing, stop and show the install URL. Do not attempt pip fallbacks — the scripts declare their own dependencies and uv is the runner.

### Step 2 - Prepare (detect + cache check + batch planning)

```bash
uv run "$KG_SCRIPTS/kg.py" prepare --root INPUT_PATH
```

This prints a JSON summary: total files/words, per-category counts, skipped sensitive files, cache hits, and how many batches the uncached documents were split into. It writes `kg-out/.kg_detect.json`, `kg-out/.kg_cached.json`, `kg-out/.kg_uncached.txt`, and `kg-out/.kg_batches.json`.

Replace INPUT_PATH with the actual path the user provided. Present a clean summary instead of dumping the JSON:

```
Corpus: X files · ~Y words
  docs:     N files (.md .txt ...)
  papers:   N files (.pdf ...)
Cache: C files hit, U files need extraction → B batches
```

Omit any category with 0 files. If the summary shows `code`, `image`, or `video` files, mention the count and state they are ignored (this skill is documents-only). If the corpus is *mostly* code/images/video, stop and tell the user.

Then act on it:

- If `total_files` is 0: stop with "No supported files found in [path]."
- If `skipped_sensitive` is non-empty: report the count and list the skipped file names, so a wrongly-flagged source or doc is visible and can be renamed or moved.
- If `total_words` > 2,000,000 OR `total_files` > 500: show the warning, compute the top 5 first-level subdirectories by file count (read `scan_root` from `kg-out/.kg_detect.json`; exclude anything under `kg-out/`; files directly in the root count as `(root)`). If everything is in `(root)`, do not ask to narrow — suggest `--no-cluster` and proceed. Otherwise show the top 5 with counts and ask which subfolder to run on; wait for the answer, then re-run `prepare --root <subfolder>`.
- Otherwise: proceed directly to Step 3.

### Step 3 - Extract entities and relationships (self-extraction)

**Before starting:** note whether `--mode deep` was given. You must apply `DEEP_MODE=true` to every batch if it was. Track this from the original invocation - do not lose it.

Only `document` and `paper` files participate. There is no AST pass and no subagent dispatch — **you are the extractor**.

**If the prepare summary said 0 files need extraction** (everything cached), skip the loop below and go straight to `merge-extraction`.

**Self-extraction loop.** Read `kg-out/.kg_batches.json` — a list of batches, each a list of file paths (at most ~10 files or ~30k words each, same-directory grouped). For each batch, in order:

1. Read every file in the batch with your file-reading tool. Read them fully — do not skim. Your context is the hard limit: if a batch is too big to read comfortably, split it and renumber.
2. Apply the extraction spec below exactly.
3. Write the resulting JSON to `kg-out/.kg_chunk_NN.json` (NN zero-padded: 01, 02, ...) using an **absolute** path.
4. Verify the file exists and parses as JSON before moving to the next batch. If a batch fails or produces invalid JSON, retry it once; if it still fails, record it as failed and continue.

If more than half the batches failed, stop and tell the user — do not build a graph from less than half the corpus.

The extraction spec (apply verbatim; substitute FILE_LIST, CHUNK_NUM, TOTAL_CHUNKS, DEEP_MODE, CHUNK_PATH):

```
Extract a knowledge graph fragment from the documents listed below.
Output ONLY valid JSON matching the schema at the end - no explanation, no markdown fences, no preamble.

Files (batch CHUNK_NUM of TOTAL_CHUNKS):
FILE_LIST

Rules:
- EXTRACTED: relationship explicit in source (citation, "see §3.2", named cross-reference)
- INFERRED: reasonable inference (shared concept, implied dependency)
- AMBIGUOUS: uncertain - flag for review, do not omit

Doc/paper files: extract named concepts, entities, citations. For rationale (WHY decisions were
  made, trade-offs, design intent): store as a `rationale` attribute on the relevant concept
  node — do NOT create a separate rationale node or fragment node. Only create a node for
  something that is itself a named entity or concept. Use `file_type:"rationale"` for
  concept-like nodes (ideas, principles, mechanisms, design patterns). `file_type` MUST be
  one of exactly these four values: `document`, `paper`, `rationale`, `concept`.
  Any other value is invalid and will be rejected.

DEEP_MODE (if --mode deep was given): be aggressive with INFERRED edges - indirect deps,
  shared assumptions, latent couplings. Mark uncertain ones AMBIGUOUS instead of omitting.

Semantic similarity: if two concepts in this batch solve the same problem or represent the
  same idea without any structural link (no citation, no cross-reference), add a
  `semantically_similar_to` edge marked INFERRED with a confidence_score reflecting how
  similar they are (0.6-0.95). Examples:
- Two sections in different documents that describe the same mechanism in different words
- A concept in a paper and a principle in a design doc that are the same idea
- Two definitions of the same term that never reference each other
Only add these when the similarity is genuinely non-obvious and cross-cutting. Do not add
them for trivially similar things.

Hyperedges: if 3 or more nodes clearly participate together in a shared concept, flow, or
  pattern that is not captured by pairwise edges alone, add a hyperedge to a top-level
  `hyperedges` array. Examples:
- All concepts from a paper section that form one coherent idea
- All principles that together make up a methodology
Use sparingly — only when the group relationship adds information beyond the pairwise
edges. Maximum 3 hyperedges per batch.

If a file has YAML frontmatter (--- ... ---), copy source_url, captured_at, author,
  contributor onto every node from that file.

confidence_score is REQUIRED on every edge - never omit it, never use 0.5 as a default:
- EXTRACTED edges: confidence_score = 1.0 always
- INFERRED edges: pick exactly ONE value from this set — never 0.5:
    0.95  direct structural evidence (named cross-file reference).
    0.85  strong inference (clear conceptual alignment, no direct link).
    0.75  reasonable inference (shared problem domain + similar shape, requires interpretation).
    0.65  weak inference (thematically related, no shape evidence).
    0.55  speculative but plausible (surface-level co-occurrence only).
  If no value above fits, mark the edge AMBIGUOUS rather than picking 0.4 or below.
- AMBIGUOUS edges: 0.1-0.3

Node ID format: lowercase, only `[a-z0-9_]`, no dots or slashes. Format: `{stem}_{entity}`
  where stem is the **full root-relative path with the extension dropped**, every path
  segment kept and joined with `_` (each segment lowercased with non-alphanumeric chars
  replaced by `_`), and entity is the concept name similarly normalized. Use every
  directory level, not just the immediate parent. Examples: `docs/v1/api/README.md` +
  `Rate Limiting` → `docs_v1_api_readme_rate_limiting`; top-level file `notes.md` +
  `CAP theorem` → `notes_cap_theorem`. CRITICAL: never append batch numbers, sequence
  numbers, or any suffix to an ID. IDs must be deterministic from the label alone — the
  same entity must always produce the same ID regardless of which batch processes it.

Generate the extraction JSON matching this schema exactly:
{"nodes":[{"id":"docs_api_rate_limiting","label":"Human Readable Name","file_type":"document|paper|rationale|concept","source_file":"<FILE_LIST path verbatim>","source_location":null,"source_url":null,"captured_at":null,"author":null,"contributor":null}],"edges":[{"source":"node_id","target":"node_id","relation":"references|cites|conceptually_related_to|semantically_similar_to|rationale_for","confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,"source_file":"<FILE_LIST path verbatim>","source_location":null,"weight":1.0}],"hyperedges":[{"id":"snake_case_id","label":"Human Readable Label","nodes":["node_id1","node_id2","node_id3"],"relation":"participate_in|form","confidence":"EXTRACTED|INFERRED","confidence_score":0.75,"source_file":"<FILE_LIST path verbatim>"}],"input_tokens":0,"output_tokens":0}

source_file RULE (every node, edge, and hyperedge): set source_file to the path of the
  originating file EXACTLY as it appears in FILE_LIST — verbatim and absolute. Do NOT
  shorten to a basename, do NOT re-relativize, do NOT strip any directory prefix. Copy
  the FILE_LIST entry character-for-character. This keeps the full build and incremental
  --update on the same base, so replace-on-re-extract matches the existing node instead
  of accumulating a duplicate.

Write the JSON to this exact absolute path:
CHUNK_PATH
```

**Then merge:**

```bash
uv run "$KG_SCRIPTS/kg.py" merge-extraction --root INPUT_PATH
```

This validates and merges all batch files (skipping invalid ones with a warning), saves the semantic cache, merges cached+new into `kg-out/.kg_extract.json`, and cleans up temp files. Token counts are 0 — self-extraction burns your context, not a metered API.

### Step 4 - Build graph, cluster, analyze, generate outputs

```bash
uv run "$KG_SCRIPTS/kg.py" build --root INPUT_PATH          # add --directed if given
```

Guards: if it prints `ERROR: Graph is empty` or a shrink-refusal (`refused to shrink kg-out/graph.json`), stop and tell the user what happened — do not proceed to labeling or visualization. If the user confirms the shrink is intentional (e.g. files were deleted), re-run with `--force`.

### Step 4.5 - Graph health check (read-only integrity gate)

```bash
uv run "$KG_SCRIPTS/kg.py" diagnose --root INPUT_PATH       # add --directed if given
```

If a `GRAPH HEALTH WARNING` prints, surface it in the final summary (do not abort — the graph is still usable, but the integrity issue must be visible, per the Honesty Rules).

### Step 5 - Label communities

Read `kg-out/.kg_analysis.json`. For each community key, look at its node labels and write a 2-5 word plain-language name (e.g. "Attention Mechanism", "Training Pipeline", "Data Loading").

```bash
uv run "$KG_SCRIPTS/kg.py" relabel --root INPUT_PATH --labels '{"0": "Attention Mechanism", "1": "Training Pipeline"}'   # add --directed if given
```

Substitute the actual labels dict you constructed. If this prints a shrink-refusal, surface the message — do not force past it.

### Step 6 - Generate HTML

Always (unless `--no-viz`):

```bash
uv run "$KG_SCRIPTS/kg.py" export-html                      # auto-aggregates to community view if > 5000 nodes
```

### Step 8 - Token reduction benchmark (only if total_words > 5000)

If `total_words` from `kg-out/.kg_detect.json` is greater than 5,000, run:

```bash
uv run "$KG_SCRIPTS/kg.py" benchmark
```

Print the output directly in chat. If `total_words <= 5000`, skip silently - the graph value is structural clarity, not token compression, for small corpora.

---

### Step 9 - Finalize (manifest, cost, cleanup) and report

```bash
uv run "$KG_SCRIPTS/kg.py" finalize --root INPUT_PATH
```

This stamps the manifest (only files that actually produced extraction output), updates `kg-out/cost.json`, and deletes intermediate sidecars.

Tell the user:

```
Graph complete. Outputs in PATH_TO_DIR/kg-out/

  graph.html            - interactive graph, open in browser
  GRAPH_REPORT.md       - audit report
  graph.json            - raw graph data
```

Replace PATH_TO_DIR with the actual absolute path of the directory that was processed.

Then paste these sections from GRAPH_REPORT.md directly into the chat:

- God Nodes
- Surprising Connections
- Suggested Questions

Do NOT paste the full report - just those three sections. Keep it concise.

Then immediately offer to explore. Pick the single most interesting suggested question from the report - the one that crosses the most community boundaries or has the most surprising bridge node - and ask:

> "The most interesting question this graph can answer: **[question]**. Want me to trace it?"

If the user says yes, run the `/kg query` flow on the graph and walk them through the answer using the graph structure - which nodes connect, which community boundaries get crossed, what the path reveals. Keep going as long as they want to explore. Each answer should end with a natural follow-up ("this connects to X - want to go deeper?") so the session feels like navigation, not a one-shot report.

The graph is the map. Your job after the pipeline is to be the guide.

---

## For --update (incremental re-extraction)

Use when you've added or modified documents since the last run. Only re-extracts changed files.

```bash
uv run "$KG_SCRIPTS/kg.py" update-detect --root INPUT_PATH
```

If it prints "nothing to update" (no new/changed files and no deletions), stop — there is nothing to do. Otherwise it populates `kg-out/.kg_detect.json` (changed subset + full corpus), runs the cache check on the changed document/paper files, and writes fresh `kg-out/.kg_batches.json` for them. Non-document changed files (code/image/video) are ignored.

Then:

1. **If there are changed document/paper files:** run the Step 3 self-extraction loop over the new batches, then `merge-extraction --root INPUT_PATH`. (If there are only deletions, `update-detect` already wrote an empty `.kg_extract.json` — skip the loop and the merge.)
2. **Merge into the existing graph:**

```bash
uv run "$KG_SCRIPTS/kg.py" update-merge --root INPUT_PATH   # add --directed if given
```

This prunes deleted files' nodes, applies replace-on-re-extract for changed files, rewrites `.kg_extract.json` as the full merged graph, and stamps the manifest.

3. **Rebuild outputs and show what changed:**

```bash
uv run "$KG_SCRIPTS/kg.py" build --root INPUT_PATH          # add --directed if given
uv run "$KG_SCRIPTS/kg.py" diff                             # add --directed if given; prints the graph diff, cleans up
```

4. Then run Steps 4.5–9 as normal (diagnose, relabel, export-html, benchmark, finalize).

---

## For --cluster-only

Skip Steps 1–3. Re-run clustering on the existing graph:

```bash
uv run "$KG_SCRIPTS/kg.py" cluster-only
```

This is **self-contained**: it re-clusters, names communities, and regenerates `GRAPH_REPORT.md`, `graph.json`, and `graph.html` from the existing graph. **Do not run Steps 5–9 afterwards** — they read intermediate files that a prior build's finalize step already deleted. When it finishes, present the refreshed `GRAPH_REPORT.md` summary as usual.

---

## For /kg query

When `kg-out/graph.json` already exists and the user asks a question about the corpus, answer from the graph rather than rebuilding it.

Two traversal modes - choose based on the question:

| Mode          | Flag       | Best for                                                           |
| ------------- | ---------- | ------------------------------------------------------------------ |
| BFS (default) | _(none)_ | "What is X connected to?" - broad context, nearest neighbors first |
| DFS           | `--dfs`  | "How does X reach Y?" - trace a specific chain or dependency path  |

First check the graph exists:

```bash
test -f kg-out/graph.json || echo "ERROR: No graph found. Run /kg <path> first to build the graph."
```

If it fails, stop and tell the user to run `/kg <path>` first.

### Query Step 0 — Constrained query expansion (REQUIRED before traversal)

The query engine matches nodes via case-folded substring + IDF — there is **no stemming, no synonyms, no cross-language match** inside it. If the user's question uses different language or different domain vocabulary than the graph's labels (user says "обработчик" / graph says "handler"; user says "authentication" / graph says "Guardian"), the literal matcher returns 0 hits and the answer collapses to noise.

Fix this **without inventing tokens** by expanding the query against the actual graph vocabulary first:

1. Extract the token vocabulary from node labels:

```bash
uv run "$KG_SCRIPTS/kg.py" vocab
```

2. Read `kg-out/.vocab.txt`. Then for the user's question, select **up to 12 tokens from this exact list** that semantically match the query intent. Hard constraints:

   - You MUST pick only tokens present in the vocabulary file. Do NOT invent tokens.
   - If a query concept has no plausible token in the vocab, skip it — do not substitute a near-synonym from training memory.
   - If **no** vocab tokens match the query at all, output an empty list and tell the user the corpus has no relevant vocabulary for this question. Do not fabricate a search.
   - Translate cross-language: Russian "аутентификация" → look for `auth`, `credential`, `token`, `security` IFF present in vocab.
   - Morphology: "handlers" maps to `handler` IFF present; "todos" maps to `todo` IFF present.
3. Print the selection explicitly to the user before running the query, so the expansion is auditable:

```
Query expanded to (from graph vocab, N tokens): [token1, token2, ...]
```

If the list is empty, say so plainly and stop — do not proceed to traversal.

### Query Step 1 — Traversal

Build the **expanded query string** by joining the selected tokens with spaces. Use this string as the query — NOT the original user question. (The original question is preserved only for `save-result` at the end.)

```bash
uv run "$KG_SCRIPTS/kg.py" query "EXPANDED_QUESTION"
# or: uv run "$KG_SCRIPTS/kg.py" query "EXPANDED_QUESTION" --dfs --budget 3000
```

Answer using **only** what the graph output contains. Quote `source_location` when citing a specific fact. If the graph lacks enough information, say so - do not hallucinate edges.

After writing the answer, save it back into the graph so it improves future queries. Include the expanded tokens inside the `--answer` text (e.g. `"Expanded from original query via vocab: [tokens]. Then traversed..."`) so the next `--update` extracts the expansion history as a graph node:

```bash
uv run "$KG_SCRIPTS/kg.py" save-result --question "ORIGINAL_QUESTION" --answer "ANSWER" --type query --nodes NODE1 NODE2
```

Replace `ORIGINAL_QUESTION` with the user's verbatim question, `ANSWER` with your full answer text (containing the expanded-token trace), `NODE1 NODE2` with the list of node labels you cited. This closes the feedback loop: the next `--update` will extract this Q&A as a node in the graph.

**Work memory (self-improving loop).** Add an `--outcome` so future sessions learn from this one — append `--outcome useful|dead_end|corrected` to the `save-result` command (and `--correction "the right answer"` when correcting):

- `useful` — the cited nodes answered the question well (they become *preferred sources*).
- `dead_end` — the question/path led nowhere; don't re-derive it next time.
- `corrected` — the saved answer was wrong; `--correction` records what was right.

At the **start** of graph work, refresh and read the lessons: run `uv run "$KG_SCRIPTS/kg.py" reflect --if-stale` (cheap, deterministic; a no-op when `LESSONS.md` is already fresh), then read `kg-out/reflections/LESSONS.md`. It lists **preferred sources** (start there), **known dead ends** (skip them), and prior **corrections**.

---

## For /kg path

Find the shortest path between two named concepts in the graph:

```bash
uv run "$KG_SCRIPTS/kg.py" path "NODE_A" "NODE_B"
```

If the graph was built with `--directed`, `path` follows edge direction and refuses when no directed path exists; pass `--undirected` to ignore direction (`uv run "$KG_SCRIPTS/kg.py" path "NODE_A" "NODE_B" --undirected`).

Then explain the path in plain language - what each hop means, why it's significant.

After writing the explanation, save it back:

```bash
uv run "$KG_SCRIPTS/kg.py" save-result --question "Path from NODE_A to NODE_B" --answer "ANSWER" --type path_query --nodes NODE_A NODE_B
```

---

## For /kg explain

Give a plain-language explanation of a single node - everything connected to it:

```bash
uv run "$KG_SCRIPTS/kg.py" explain "NODE_NAME"
```

Then write a 3-5 sentence explanation of what this node is, what it connects to, and why those connections are significant. Use the source locations as citations.

After writing the explanation, save it back:

```bash
uv run "$KG_SCRIPTS/kg.py" save-result --question "Explain NODE_NAME" --answer "ANSWER" --type explain --nodes NODE_NAME
```

---

## Honesty Rules

- Never invent an edge. If unsure, use AMBIGUOUS.
- Never claim to have extracted a document you did not actually read in full. Self-extraction means your reads are the audit trail.
- Never skip the corpus check warning.
- Always show token cost in the report — and state plainly that self-extraction tokens are your own context, not metered (cost.json will show 0).
- Never hide cohesion scores behind symbols - show the raw number.
- Never run HTML viz on a graph with more than 5,000 nodes without warning the user (`export-html` auto-aggregates to community view — mention this when it happens).
