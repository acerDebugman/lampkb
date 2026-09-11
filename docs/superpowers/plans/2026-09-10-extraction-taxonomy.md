# 抽取分类法替换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 kg 抽取流水线的实体/关系分类法替换为 9 实体类型（英文 key）+ 11 中文关系，实体新增 definition 属性，file_type 更名 entity_type，删除超边机制与旧关系特判。

**Architecture:** 提示词规范（SKILL.md Step 3）与 kglib 校验白名单是分类法的两个定义点；下游 build/dedup/analyze/report/export 跟随 schema 变更；测试全量更新。

**Tech Stack:** Python 3.10+（uv 运行，PEP 723 脚本）、NetworkX、pytest。

**Spec:** docs/superpowers/specs/2026-09-10-extraction-taxonomy-design.md

## Global Constraints
- 实体类型闭集 9 值（英文 key）：concept, principle, method, rule, procedure, fact, scenario, keypoint, document
- 关系闭集 11 值（中文）：阐述, 归属, 组成, 前置, 解决, 导致, 限制, 顺序, 推导, 适用, 影响
- 节点必填字段：{id, label, entity_type, definition, source_file}；definition 空串合法、缺失报错
- 不做旧数据兼容：无旧类型/旧关系同义词映射；kg-out/、kg-all3-0820/ 存量产物不动
- confidence 枚举 EXTRACTED/INFERRED/AMBIGUOUS 原样保留
- 测试命令统一为：uv run --with pytest pytest tests/（在仓库根目录执行）
- 全程说中文或英文均可，但提交信息用英文 conventional commit

## 环境前置说明（实施前必读）

- 基线验证时间 2026-09-10：`uv run --with pytest python -m pytest tests/ -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess` → **215 passed**。注意：当前仓库 `.venv/bin/pytest` 的 shebang 错误地指向兄弟仓库 `taoskg/.venv/bin/python`（venv 是从别处拷贝来的），直接跑 `uv run --with pytest pytest ...` 会用错解释器导致 `ModuleNotFoundError: datasketch`。实施前先在仓库根执行一次 `uv sync --reinstall` 修复 shebang；若仍异常，所有步骤中的测试命令临时替换为 `uv run --with pytest python -m pytest ...`（语义等价）。下文命令一律写规范形式 `uv run --with pytest pytest ...`。
- spec 未覆盖但必须改的代码点（已在对应 Task 中纳入）：`kglib/cache.py`（semantic cache 的 hyperedges 桶）、`kglib/flow.py`（manifest 盖印统计 hyperedges、`explain` 显示 file_type）、`kg.py` 的 cache 调用点（check_semantic_cache 返回 4 元组）。这些不属于 spec 改动清单的显式条目，但 hyperedge 机制不删透会留脏数据通路。
- serve.py 不感知分类法，按计划不动；GRAPH_REPORT.md 的内容结构不动（report.py 中仅删除失效特判）。

---

## Task 1: validate.py — 新白名单 + 必填字段 + 关系校验

这是接口定义点，后续所有任务依赖这里定下的常量名：`VALID_ENTITY_TYPES` / `VALID_RELATIONS` / `REQUIRED_NODE_FIELDS` / `REQUIRED_EDGE_FIELDS`。

**Files:**
- Modify: `skills/kg/scripts/kglib/validate.py`（全文重写，96 行 → 约 105 行）
- Create: `tests/test_validate_taxonomy.py`

**Interfaces:**
- Produces:
  - `VALID_ENTITY_TYPES: set[str]` — 9 个英文 key
  - `VALID_RELATIONS: set[str]` — 11 个中文关系
  - `REQUIRED_NODE_FIELDS = {"id", "label", "entity_type", "definition", "source_file"}`
  - `REQUIRED_EDGE_FIELDS = {"source", "target", "relation", "confidence", "source_file"}`（不变）
  - `validate_extraction(data: dict) -> list[str]`（签名不变）
  - `assert_valid(data: dict) -> None`（签名不变）
- Consumes: 无新依赖。`VALID_FILE_TYPES` 常量删除，全局无其他引用（已 grep 确认仅 validate.py 自身与测试间接使用）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_validate_taxonomy.py`，完整内容：

```python
"""Whitelist + required-field tests for the 9-entity-type / 11-relation taxonomy."""
import pytest

from kglib.validate import (
    REQUIRED_EDGE_FIELDS,
    REQUIRED_NODE_FIELDS,
    VALID_ENTITY_TYPES,
    VALID_RELATIONS,
    assert_valid,
    validate_extraction,
)


def _node(**over):
    n = {"id": "a", "label": "A", "entity_type": "concept",
         "definition": "", "source_file": "a.md"}
    n.update(over)
    return n


def _edge(**over):
    e = {"source": "a", "target": "b", "relation": "归属",
         "confidence": "EXTRACTED", "source_file": "a.md"}
    e.update(over)
    return e


def test_constant_sets_are_exact():
    assert VALID_ENTITY_TYPES == {
        "concept", "principle", "method", "rule", "procedure",
        "fact", "scenario", "keypoint", "document",
    }
    assert VALID_RELATIONS == {
        "阐述", "归属", "组成", "前置", "解决", "导致",
        "限制", "顺序", "推导", "适用", "影响",
    }
    assert REQUIRED_NODE_FIELDS == {"id", "label", "entity_type", "definition", "source_file"}
    assert REQUIRED_EDGE_FIELDS == {"source", "target", "relation", "confidence", "source_file"}


def test_valid_minimal_extraction():
    data = {"nodes": [_node(), _node(id="b", label="B")], "edges": [_edge()]}
    assert validate_extraction(data) == []


def test_all_nine_entity_types_accepted():
    for t in sorted(VALID_ENTITY_TYPES):
        data = {"nodes": [_node(entity_type=t)], "edges": []}
        assert validate_extraction(data) == [], t


def test_all_eleven_relations_accepted():
    for r in sorted(VALID_RELATIONS):
        data = {"nodes": [_node(), _node(id="b", label="B")],
                "edges": [_edge(relation=r)]}
        assert validate_extraction(data) == [], r


def test_invalid_entity_type_rejected():
    data = {"nodes": [_node(entity_type="paper")], "edges": []}
    errs = validate_extraction(data)
    assert any("invalid entity_type" in e and "'paper'" in e for e in errs)


def test_invalid_relation_rejected():
    data = {"nodes": [_node(), _node(id="b", label="B")],
            "edges": [_edge(relation="references")]}
    errs = validate_extraction(data)
    assert any("invalid relation" in e and "'references'" in e for e in errs)


def test_definition_empty_string_ok_missing_field_errors():
    assert validate_extraction({"nodes": [_node()], "edges": []}) == []
    data = {"nodes": [_node()], "edges": []}
    del data["nodes"][0]["definition"]
    errs = validate_extraction(data)
    assert any("missing required field 'definition'" in e for e in errs)


def test_confidence_enum_unchanged():
    data = {"nodes": [_node(), _node(id="b", label="B")],
            "edges": [_edge(confidence="GUESSED")]}
    errs = validate_extraction(data)
    assert any("invalid confidence" in e for e in errs)


def test_assert_valid_raises_with_all_errors():
    with pytest.raises(ValueError, match="error"):
        assert_valid({"nodes": [_node(entity_type="image")], "edges": []})
```

运行确认失败：`uv run --with pytest pytest tests/test_validate_taxonomy.py -q`（应全部 FAIL，报 `ImportError: cannot import name 'VALID_ENTITY_TYPES'`）。

- [ ] **Step 2: 重写 validate.py**

将 `skills/kg/scripts/kglib/validate.py` 整体替换为：

```python
# validate extraction JSON against the kg taxonomy schema before graph assembly
from __future__ import annotations

VALID_ENTITY_TYPES = {
    "concept", "principle", "method", "rule", "procedure",
    "fact", "scenario", "keypoint", "document",
}
VALID_RELATIONS = {
    "阐述", "归属", "组成", "前置", "解决", "导致",
    "限制", "顺序", "推导", "适用", "影响",
}
VALID_CONFIDENCES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
REQUIRED_NODE_FIELDS = {"id", "label", "entity_type", "definition", "source_file"}
REQUIRED_EDGE_FIELDS = {"source", "target", "relation", "confidence", "source_file"}


def validate_extraction(data: dict) -> list[str]:
    """
    Validate an extraction JSON dict against the kg taxonomy schema.
    Returns a list of error strings - empty list means valid.
    """
    if not isinstance(data, dict):
        return ["Extraction must be a JSON object"]

    errors: list[str] = []

    # Collected during the node pass so the edge pass can reuse it. Only
    # hashable ids land here; a non-hashable id (e.g. a list emitted by a
    # malformed LLM extraction) is reported as an error rather than crashing
    # the validator on set construction.
    node_ids: set = set()

    # Nodes
    if "nodes" not in data:
        errors.append("Missing required key 'nodes'")
    elif not isinstance(data["nodes"], list):
        errors.append("'nodes' must be a list")
    else:
        for i, node in enumerate(data["nodes"]):
            if not isinstance(node, dict):
                errors.append(f"Node {i} must be an object")
                continue
            for field in REQUIRED_NODE_FIELDS:
                if field not in node:
                    errors.append(f"Node {i} (id={node.get('id', '?')!r}) missing required field '{field}'")
            if "id" in node:
                try:
                    hash(node["id"])
                except TypeError:
                    errors.append(
                        f"Node {i} has non-hashable id {node['id']!r} - id must be a string"
                    )
                else:
                    node_ids.add(node["id"])
            if "entity_type" in node and node["entity_type"] not in VALID_ENTITY_TYPES:
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) has invalid entity_type "
                    f"'{node['entity_type']}' - must be one of {sorted(VALID_ENTITY_TYPES)}"
                )

    # Edges - accept "links" (NetworkX <= 3.1) as fallback for "edges"
    edge_list = data.get("edges") if "edges" in data else data.get("links")
    if edge_list is None:
        errors.append("Missing required key 'edges'")
    elif not isinstance(edge_list, list):
        errors.append("'edges' must be a list")
    else:
        for i, edge in enumerate(edge_list):
            if not isinstance(edge, dict):
                errors.append(f"Edge {i} must be an object")
                continue
            for field in REQUIRED_EDGE_FIELDS:
                if field not in edge:
                    errors.append(f"Edge {i} missing required field '{field}'")
            if "relation" in edge and edge["relation"] not in VALID_RELATIONS:
                errors.append(
                    f"Edge {i} has invalid relation '{edge['relation']}' "
                    f"- must be one of {sorted(VALID_RELATIONS)}"
                )
            if "confidence" in edge and edge["confidence"] not in VALID_CONFIDENCES:
                errors.append(
                    f"Edge {i} has invalid confidence '{edge['confidence']}' "
                    f"- must be one of {sorted(VALID_CONFIDENCES)}"
                )
            for endpoint in ("source", "target"):
                if endpoint not in edge:
                    continue
                val = edge[endpoint]
                try:
                    unmatched = bool(node_ids) and val not in node_ids
                except TypeError:
                    errors.append(
                        f"Edge {i} {endpoint} {val!r} is non-hashable - must be a string"
                    )
                    continue
                if unmatched:
                    errors.append(f"Edge {i} {endpoint} '{val}' does not match any node id")

    return errors


def assert_valid(data: dict) -> None:
    """Raise ValueError with all errors if extraction is invalid."""
    errors = validate_extraction(data)
    if errors:
        msg = f"Extraction JSON has {len(errors)} error(s):\n" + "\n".join(f"  • {e}" for e in errors)
        raise ValueError(msg)
```

- [ ] **Step 3: 跑新测试**

`uv run --with pytest pytest tests/test_validate_taxonomy.py -q` → 全绿。

- [ ] **Step 4: 全量测试，确认预期红绿**

`uv run --with pytest pytest tests/ -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：新文件 9 个测试绿；以下 ported 测试转红（旧 schema 断言与新必填 definition/entity_type 冲突），均属预期，Task 8 修复：
- `tests/ported/test_build_ported.py::test_legacy_node_name_path_aliases_folded`（断言无 missing-field 警告，现 definition 必填）
- `test_none_file_type_defaults_to_concept` / `test_missing_file_type_defaults_to_concept` 中 `"missing required field 'file_type'" not in err` 类断言的语义已变（这两个用例在 Task 8 整体改写；若此处仍为绿也无妨，以实际为准并记录在 Task 8 开头）。

其余测试（含 smoke）应保持绿：build 对校验错误只警告不拒绝。

- [ ] **Step 5: commit**

```bash
git add skills/kg/scripts/kglib/validate.py tests/test_validate_taxonomy.py
git commit -m "feat(kglib): replace file_type whitelist with 9-type entity_type + 11 Chinese relations, require definition"
```

---

## Task 2: build.py — file_type→entity_type 更名 + 新同义词表 + 非法值改报错

**Files:**
- Modify: `skills/kg/scripts/kglib/build.py`
- Modify: `tests/ported/test_build_ported.py`（仅 384–452 的 file_type canonicalization 小节整体改写，其余 file_type 更名留到 Task 8 统一做）

**Interfaces:**
- Consumes: `kglib.validate.VALID_ENTITY_TYPES`（Task 1）
- Produces:
  - `kglib.build._ENTITY_TYPE_SYNONYMS: dict[str, str]`（替代 `_FILE_TYPE_SYNONYMS`）
  - `kglib.build._RELATION_SYNONYMS: dict[str, str]`（新增，空表起步）
  - `build_from_json()` 节点规范化行为变更：缺失/空 `entity_type` 默认 `concept`（保留兜底）；同义词表命中则归一；**未命中的非法值原样保留，交由 validate_extraction 报错**（删除静默归一为 concept 的 `.get(ft, "concept")` 兜底）
  - 不做 `file_type` → `entity_type` 的旧字段别名折叠（无旧数据兼容）

- [ ] **Step 1: 改写失败测试**

将 `tests/ported/test_build_ported.py` 第 384–452 行（`# --- file_type canonicalization ---` 小节，含 `test_none_file_type_defaults_to_concept`、`test_missing_file_type_defaults_to_concept`、`test_real_invalid_file_type_coerced_to_concept`、`test_file_type_synonym_mapping` 共 4 个用例）整体替换为：

```python
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
```

运行：`uv run --with pytest pytest tests/ported/test_build_ported.py -q -k "entity_type"` → 4 个全 FAIL。

- [ ] **Step 2: 替换同义词表（build.py 86–103）**

旧代码（86–103 行）：

```python
# Synonym mapper for known invalid file_type values that LLM subagents commonly
# emit. Keeps semantic intent close (markdown→document, tool→code) and falls
# back to "concept" for any other invalid value.
_FILE_TYPE_SYNONYMS = {
    "markdown": "document",
    "text": "document",
    "tool": "code",
    "library": "code",
    "pattern": "concept",
    "principle": "concept",
    "constraint": "concept",
    "tech": "concept",
    "technology": "concept",
    "data-source": "concept",
    "data_source": "concept",
    "gotcha": "concept",
    "framework": "concept",
}
```

替换为：

```python
# Synonym mapper for entity_type drift. The extraction prompt's type table
# prints Chinese names next to the English keys, so the common drift is emitting
# the Chinese name (概念→concept) or a near-miss English form (scenarios→scenario).
# Anything not in VALID_ENTITY_TYPES and not mapped here is left untouched so
# validate_extraction reports it — no silent coercion to "concept".
_ENTITY_TYPE_SYNONYMS = {
    "概念": "concept",
    "原理": "principle",
    "方法": "method",
    "规则": "rule",
    "操作": "procedure",
    "步骤": "procedure",
    "事实": "fact",
    "场景": "scenario",
    "要点": "keypoint",
    "文档": "document",
    "concepts": "concept",
    "methods": "method",
    "rules": "rule",
    "facts": "fact",
    "scenarios": "scenario",
}

# Relation drift normalization for the 11 Chinese relations. Starts empty;
# add observed drift forms here (e.g. an English translation the model emits)
# as they appear in real extractions.
_RELATION_SYNONYMS: dict[str, str] = {}
```

- [ ] **Step 3: 节点规范化逻辑更名 + 非法值不再兜底（build.py 854–862）**

旧代码：

```python
        # Default missing/None file_type to "concept" so legacy graph.json
        # entries (and stub nodes preserved by `_rebuild_code` from older
        # graphify versions that didn't always populate file_type) don't
        # trigger spurious "invalid file_type 'None'" validator warnings.
        if node.get("file_type") in (None, ""):
            node["file_type"] = "concept"
        ft = node.get("file_type", "")
        if ft and ft not in {"code", "document", "paper", "image", "rationale", "concept"}:
            node["file_type"] = _FILE_TYPE_SYNONYMS.get(ft, "concept")
```

替换为：

```python
        # Default missing/None entity_type to "concept" so sparse fragments
        # don't trigger spurious "invalid entity_type 'None'" validator
        # warnings. Any other invalid value is left as-is for
        # validate_extraction to report — silently coercing it to "concept"
        # used to hide prompt drift.
        if node.get("entity_type") in (None, ""):
            node["entity_type"] = "concept"
        et = node.get("entity_type", "")
        if et:
            node["entity_type"] = _ENTITY_TYPE_SYNONYMS.get(et, et)
```

- [ ] **Step 4: 边关系漂移归一化（build.py 872–878 附近）**

在 `_fold_edge_aliases` 调用循环中加入关系归一化。旧代码：

```python
    for edge in extraction.get("edges", []):
        if isinstance(edge, dict):
            _fold_edge_aliases(edge)
```

替换为：

```python
    for edge in extraction.get("edges", []):
        if isinstance(edge, dict):
            _fold_edge_aliases(edge)
            rel = edge.get("relation")
            if isinstance(rel, str) and rel in _RELATION_SYNONYMS:
                edge["relation"] = _RELATION_SYNONYMS[rel]
```

- [ ] **Step 5: 其余 file_type 引用点更名（build.py 内）**

精确 4 处（grep `file_type` skills/kg/scripts/kglib/build.py 核对）：

1. 第 779–781 行 `_doc_twin_remap` docstring：`Gated to ``file_type == "document"`` on BOTH twins` → `Gated to ``entity_type == "document"`` on BOTH twins`；同句注释里的 `references/hyperedges` 改为 `edges`（hyperedge 将在 Task 4 删除，这里先改半句）。
2. 第 797 行：`if node.get("file_type") != "document" or bare.get("file_type") != "document":` → `if node.get("entity_type") != "document" or bare.get("entity_type") != "document":`
3. 第 1415 行 `deduplicate_by_label` docstring：`It also merges by label alone with no ``file_type`` guard` → `no ``entity_type`` guard`。
4. 第 1956 行（build_merge 的 `_in_new_graph` doc-twin 豁免）：`and n.get("file_type") == "document"` → `and n.get("entity_type") == "document"`。

可用命令 + 人工核对：

```bash
grep -n "file_type" skills/kg/scripts/kglib/build.py   # 应只剩 0 处（含注释）
```

- [ ] **Step 6: 跑测试**

`uv run --with pytest pytest tests/ported/test_build_ported.py tests/test_validate_taxonomy.py -q`

预期：Step 1 的 4 个新用例转绿；其余引用 `file_type` 的用例大量转红（节点 dict 里还是旧键），属预期，Task 8 统一修复。smoke 测试保持绿（build 对旧 fixture 仅警告）。

- [ ] **Step 7: commit**

```bash
git add skills/kg/scripts/kglib/build.py tests/ported/test_build_ported.py
git commit -m "feat(kglib): rename file_type to entity_type in build, new drift synonym tables, invalid values now reported"
```

---

## Task 3: build.py — 删除代码场景守卫与 _GENERIC_RELATIONS 折叠

**Files:**
- Modify: `skills/kg/scripts/kglib/build.py`

**Interfaces:**
- Consumes: 无
- Produces: 删除 `_GENERIC_RELATIONS`、`_EDGE_LANG_FAMILY` 两个模块级常量；`build_from_json` 边循环不再做跨语言幻影边过滤、import 自环过滤、generic 边折叠。已 grep 确认这两个常量仅 build.py 内部使用。

- [ ] **Step 1: 删除 `_GENERIC_RELATIONS`（56–64 行）与 `_EDGE_LANG_FAMILY`（66–83 行）**

删除第 56–64 行整段（注释 + `_GENERIC_RELATIONS: frozenset[str] = frozenset({"references", "uses", "mentions"})`）和第 66–83 行整段（注释 + `_EDGE_LANG_FAMILY` dict）。新分类下无代码边、无"通用关系"概念，两个常量无候选值。

- [ ] **Step 2: 删除边循环中的跨语言幻影边守卫（约 1210–1234 行）与 import 自环守卫（约 1235–1244 行）**

旧代码（两段连续）：

```python
        # Drop cross-language phantom edges — the same short names (render, parse,
        # time, ...) recur across language boundaries, so an unresolved target can
        # bind to a same-named node in another language. The extraction spec forbids
        # this for `calls`; it is equally invalid for `imports`/`references` (a
        # Python `import time` must not bind to a `time.ts`).
        _edge_rel = attrs.get("relation")
        if _edge_rel in ("calls", "imports", "imports_from", "references"):
            src_ext = Path(G.nodes[src].get("source_file") or "").suffix.lower()
            tgt_ext = Path(G.nodes[tgt].get("source_file") or "").suffix.lower()
            src_fam = _EDGE_LANG_FAMILY.get(src_ext)
            tgt_fam = _EDGE_LANG_FAMILY.get(tgt_ext)
            if _edge_rel == "calls":
                # Unchanged cross-language behavior: only INFERRED calls, and drop as
                # soon as either family differs (an unknown ext counts as different).
                if (
                    attrs.get("confidence") == "INFERRED"
                    and src_ext and tgt_ext and src_fam != tgt_fam
                ):
                    continue
            else:
                # imports/references: drop only when BOTH endpoints are known code
                # languages of different families, so a config->code reference
                # (unknown ext, e.g. a manifest) is never mistaken for a phantom.
                if src_fam is not None and tgt_fam is not None and src_fam != tgt_fam:
                    continue
        # A file-level import or re-export cannot carry useful connectivity when
        # both endpoints resolve to the same node.  This most often happens when
        # the target is an unresolved bare module name (``builtins``, ``poseidon``)
        # that the legacy-ID alias index above mistakes for the importing file's
        # own old stem.  It also covers a nested module importing its parent file:
        # at file-node granularity that relationship necessarily collapses.  Keep
        # other self-edges, notably recursive ``calls``, because those are real
        # program structure rather than import-resolution artifacts.
        if src == tgt and _edge_rel in ("imports", "imports_from", "re_exports"):
            continue
```

整段删除。注意：`_edge_rel` 变量仅这两段使用，一并删除其赋值。

- [ ] **Step 3: 删除 generic 边折叠块（约 1262–1280 行）**

旧代码：

```python
        # A pair that already carries a SPECIFIC relation must not be downgraded
        # to a generic one. Only one edge survives per pair here, and the sort
        # above orders same-pair edges by relation name, so "last write wins"
        # resolved the winner alphabetically — which put `references` after
        # `calls` and `uses` after everything. On graphify's own corpus that
        # rewrote all 144 pairs where the extraction found both `calls` and
        # `references` into plain `references`, and callflow's relation filter
        # does not include `references`, so those call sites left the call graph
        # entirely. Alphabetical order carries no meaning; keeping the specific
        # fact does. The reverse (specific arriving after generic) still
        # overwrites, so the outcome no longer depends on edge order at all.
        if G.has_edge(src, tgt):
            existing_rel = edge_data(G, src, tgt).get("relation")
            if (
                attrs.get("relation") in _GENERIC_RELATIONS
                and existing_rel is not None
                and existing_rel not in _GENERIC_RELATIONS
            ):
                continue
```

整段删除。保留其上方"first-seen direction wins"的反向重复边去重块（与分类法无关）。

- [ ] **Step 4: 跑测试 + commit**

`uv run --with pytest pytest tests/ -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：无新增红（这些守卫只针对 calls/imports/references 等已废弃关系；ported 测试中的跨语言幻影守卫用例在 vendoring 时已 drop，见 test_build_ported.py 头注释）。若 test_build_ported.py 中有引用这些守卫的用例变红，记录并在 Task 8 删除/改写。

```bash
git add skills/kg/scripts/kglib/build.py
git commit -m "refactor(kglib): drop code-scenario edge guards and generic-relation folding from build"
```

---

## Task 4: 删除超边机制（build.py + dedup.py + cache.py + flow.py + kg.py + export.py + report.py）

超边是横跨 7 个文件的一条流水线，一次删透。本 Task 同时更新直接耦合的测试（cache/build_merge 的 hyperedge 用例），避免留下 import 已删符号的破测试。

**Files:**
- Modify: `skills/kg/scripts/kglib/build.py`、`dedup.py`、`cache.py`、`flow.py`、`export.py`、`report.py`、`skills/kg/scripts/kg.py`
- Modify: `tests/ported/test_build_merge_ported.py`、`tests/ported/test_cache_ported.py`、`tests/ported/test_build_ported.py`（仅删 hyperedge 用例）

**Interfaces:**
- Consumes: 无
- Produces（签名变更，调用点全部在本 Task 内同步）:
  - `kglib.cache.check_semantic_cache(...) -> tuple[list[dict], list[dict], list[str]]`（4 元组 → 3 元组，去掉 cached_hyperedges）
  - `kglib.cache.save_semantic_cache(nodes, edges, *, root=..., ...)`（删除第 3 位置参数 `hyperedges`）
  - `kglib.dedup.deduplicate_entities(nodes, edges, *, communities, dedup_llm_backend=None, root=None)`（删除 `hyperedges` kwarg）
  - `kglib.build._load_existing_graph(graph_path) -> tuple[list, list, bool] | None`（4 元组 → 3 元组）
  - 删除 `kglib.export.attach_hyperedges`、`kglib.export._hyperedge_script`、`kglib.build._normalize_hyperedge_members`、`_coerce_hyperedge_member_refs`、`_HE_MEMBER_ALIASES`、`kglib.dedup._remap_hyperedge_members`
  - graph.json 不再含 `hyperedges` 顶层键与 `graph.hyperedges` 嵌套键

- [ ] **Step 1: 先删/改测试（失败优先）**

1. `tests/ported/test_build_merge_ported.py`：
   - 删除 163–199 行整节（`# ── Hyperedge preservation across incremental updates ──` 标题 + `_seed_two_file_graph` + `_he_ids` + `test_update_preserves_hyperedges_of_unchanged_files`）。
   - `_write_graph`（29–35 行）改为：

     ```python
     def _write_graph(graph_path: Path, nodes, edges=()) -> None:
         graph_path.parent.mkdir(parents=True, exist_ok=True)
         graph_path.write_text(
             json.dumps({"nodes": list(nodes), "edges": list(edges)}),
             encoding="utf-8",
         )
     ```

   - `_seed_two_docs`（115–129 行）中 `hyperedges=[]` 实参删除。
   - 文件头注释第 1–4 行改为：

     ```python
     # Ported from graphify/tests/test_build_merge_shrink_guard.py.
     # graphify -> kglib; the canned graph layout uses kg-out/ instead of
     # graphify-out/ (kg's output-dir name; _infer_merge_root keys on it).
     # Hyperedge carry-over cases dropped with the hyperedge mechanism itself.
     ```

2. `tests/ported/test_cache_ported.py`：
   - `test_save_semantic_cache_rejects_out_of_scope_source_file`（87–130 行）：删除 `hyperedges = [...]`（109–111 行）；`save_semantic_cache(nodes, edges, hyperedges, root=...)` 调用改为 `save_semantic_cache(nodes, edges, root=tmp_path, allowed_source_files=["intended.md"], cache_root=tmp_path)`；删除末尾 `assert protected_cache["hyperedges"] == []`（130 行）。
   - `test_semantic_cache_check_returns_uncached`（133–145 行）：两处解包改为 3 元组 —— `cached_nodes, cached_edges, uncached = check_semantic_cache(...)` 与 `_n, _e, uncached = check_semantic_cache(...)`。
3. `tests/ported/test_build_ported.py`：删除 792–813 行（`# --- hyperedge member revalidation ---` 标题 + `test_build_from_json_prunes_dangling_hyperedge_members`）。

运行 `uv run --with pytest pytest tests/ported/test_build_merge_ported.py tests/ported/test_cache_ported.py -q` → 红（`TypeError: save_semantic_cache() takes ...`、`too many values to unpack` 等）。

- [ ] **Step 2: dedup.py 删除超边 rewiring**

- 删除 `_remap_hyperedge_members` 函数（464–501 行整段）。
- `deduplicate_entities` 签名删除 `hyperedges: "list[dict] | None" = None,`（511 行）；docstring 删除 522–524 行的 hyperedges 参数说明；删除 831–838 行的调用块（注释 + `if hyperedges: _remap_hyperedge_members(hyperedges, remap)`）。

- [ ] **Step 3: cache.py 删除 hyperedges 桶**

- `_relativize_source_files_in`（528 行）与 `_absolutize_source_files_in`（786 行）：`for bucket in ("nodes", "edges", "hyperedges", "raw_calls"):` → `for bucket in ("nodes", "edges", "raw_calls"):`（两处同样的 sed）；524–527 行注释中 `nodes/edges/hyperedges` → `nodes/edges`。
- `check_semantic_cache`：
  - docstring 1006 行 `Returns (cached_nodes, cached_edges, cached_hyperedges, uncached_files).` → `Returns (cached_nodes, cached_edges, uncached_files).`
  - 删除 1037 行 `cached_hyperedges: list[dict] = []`、1051 行 `cached_hyperedges.extend(...)`；
  - 1079 行 `return cached_nodes, cached_edges, cached_hyperedges, uncached` → `return cached_nodes, cached_edges, uncached`。
- `_group_has_partial_marker`（1082–1083 行）docstring：`node/edge/hyperedge` → `node/edge`。
- `save_semantic_cache`：
  - 删除签名中的 `hyperedges: list[dict] | None = None,`（1102 行，第 3 位置参数——调用点见 Step 6 已同步）；
  - 1183 行 `by_file: dict[str, dict] = defaultdict(lambda: {"nodes": [], "edges": [], "hyperedges": []})` → `defaultdict(lambda: {"nodes": [], "edges": []})`；
  - 删除 1194–1198 行 hyperedge 分组循环；
  - 1234–1243 行注释中 `edge/hyperedge`、`hyperedge whose member (whole-hyperedge drop, mirroring that filter)` 等措辞精简为仅 edge 版本；
  - 删除 1271–1275 行 `hyperedge_dangles` 函数与 1281–1283 行 `result["hyperedges"] = [...]` 过滤；
  - merge_existing 的 1315–1319 行 dict 字面量删除 `"hyperedges": (prev.get("hyperedges", []) or []) + result["hyperedges"],` 行。

- [ ] **Step 4: flow.py**

- 68 行：`for coll in ("nodes", "edges", "hyperedges"):` → `for coll in ("nodes", "edges"):`
- docstring 清理：38 行 `node/edge/hyperedge ``source_file``` → `node/edge ``source_file```；删除 44–49 行整段 hyperedge 盖印说明（"Hyperedges are counted as output..."）。

- [ ] **Step 5: build.py 删除全部 hyperedge 代码**

逐段删除（行号为改动前基准，建议按从后到前顺序删，或按锚点文本定位）：

1. `_HE_MEMBER_ALIASES` + `_coerce_hyperedge_member_refs` + `_normalize_hyperedge_members`（106–185 行，从 `# Hyperedge member lists are canonically keyed` 注释起到 185 行 `he.pop(alias, None)` 止）。
2. `_coerce_non_string_ids`：删除 279–284 行的 hyperedge 循环；docstring 中 `edge/hyperedge references`、`Endpoints and hyperedge members` 改为 edges/endpoints 措辞；234 行 docstring 中 ``try: hash(m)`` in build_from_json's hyperedge revalidation`` 改为指向边端点校验。
3. `build_from_json`：
   - 删除 816–825 行（nested `graph.hyperedges` fold 块，从 `# Hyperedge persistence is dual-slot` 注释到 `extraction = dict(extraction, hyperedges=...)`）；
   - 删除 864–870 行（`# Canonicalize hyperedge member lists` 块）；
   - 删除 921–928 行（semantic rekey 后的 hyperedge rekey 块）；
   - 删除 953–958 行（doc-twin remap 后的 hyperedge remap 块）；
   - 删除 1282–1336 行（`hyperedges = extraction.get("hyperedges", [])` 到 full-wipeout warning 块），其后直接接 `_disambiguate_file_node_labels(G)`。
4. `build()`（1369–1394 行）：
   - 1369 行 `combined: dict = {"nodes": [], "edges": [], "hyperedges": [], ...}` → `{"nodes": [], "edges": [], "input_tokens": 0, "output_tokens": 0}`；
   - 删除 1373 行 `combined["hyperedges"].extend(...)`；
   - 1388–1394 行 `deduplicate_entities(...)` 调用删除 `hyperedges=combined.get("hyperedges"),` 及其上方两行注释。
5. `_load_existing_graph`（1460–1507 行）：docstring 首行 `Load (nodes, edges, hyperedges, directed)` → `Load (nodes, edges, directed)`；返回元组删除 `list(data.get("hyperedges", [])),`。
6. `merge_raw_extraction`：1546 行解包改 `existing_nodes, existing_edges, _ = loaded`；1596 行 `for seq in (existing_nodes, existing_edges, existing_hyperedges)` → `(existing_nodes, existing_edges)`；删除 1625–1627 行 carried_hyper 块；docstring 中 nodes/edges/hyperedges 措辞（1523、1530 行）改为 nodes/edges，删除 1613–1614 行注释中 hyperedge 句。
7. `build_merge`：1660 行解包改 3 元组；1665 行 `existing_hyperedges = []` 删除；1789 行 `_stored_sfs` 的 seq 三元组改二元组；删除 1801–1826 行 hyperedge carry 块（含 `from kglib.export import attach_hyperedges` 局部 import）；1758 行 `_prune_match` docstring `node/edge/hyperedge` → `node/edge`。
8. 全文件 `grep -n "hyperedge" skills/kg/scripts/kglib/build.py` 应为 0 处（含注释/docstring，逐条人工核对残留措辞）。

- [ ] **Step 6: kg.py 删除 hyperedge 通路**

- 109 行：`cached_nodes, cached_edges, cached_hyperedges, uncached = check_semantic_cache(...)` → `cached_nodes, cached_edges, uncached = check_semantic_cache(all_files, root=root)`
- 113–117 行：

  ```python
  if cached_nodes or cached_edges:
      _write_json(out / ".kg_cached.json",
                  {"nodes": cached_nodes, "edges": cached_edges})
  ```

- `cmd_merge_extraction`（150–225 行）：删除 160 行 `all_hyperedges`；删除 173 行；176–179 行 `new` 字面量去掉 `"hyperedges"`；187 行 `save_semantic_cache(new["nodes"], new["edges"], new["hyperedges"], root=root, ...)` → `save_semantic_cache(new["nodes"], new["edges"], root=root, allowed_source_files=uncached)`；193 行 cached 默认值改 `{"nodes": [], "edges": []}`；删除 196 行 `merged_hyperedges`；203–209 行 merged 字面量去掉 `"hyperedges"`；223–224 行打印改为：

  ```python
  print(f"Extraction: {len(d['nodes'])} nodes, {len(d['edges'])} edges")
  ```

- 610 行空 extraction 字面量：`{"nodes": [], "edges": [], "input_tokens": 0, "output_tokens": 0}`。
- 670 行 merged_out 字面量删除 `"hyperedges": list(G.graph.get("hyperedges", [])),` 行。

- [ ] **Step 7: export.py 删除 hyperedge 输出**

- 删除 `attach_hyperedges`（140–155 行整段）。
- `to_json`：删除 314–345 行整段（`if "hyperedges" not in getattr(G, "graph", {}):` hardening 块 + `hyperedges = sorted(...)` + 两个写入语句）。graph.json 从结构上去掉 hyperedges 键。
- 删除 `_hyperedge_script`（437–502 行整段，从 `def _hyperedge_script` 到 `</script>"""`）。
- `to_html`：删除 807–830 行（`# Remap hyperedges from semantic node IDs to community IDs` 块）；删除 957 行 `hyperedges_json = _js_safe(...)`；989–990 行的 HTML 尾部：

  ```python
  {_html_script(nodes_json, edges_json, legend_json)}
  {_hyperedge_script(hyperedges_json)}
  </body>```

  改为：

  ```python
  {_html_script(nodes_json, edges_json, legend_json)}
  </body>```

- [ ] **Step 8: report.py 删除 hyperedge 小节**

删除 238–246 行：

```python
    hyperedges = G.graph.get("hyperedges", [])
    if hyperedges:
        lines += ["", "## Hyperedges (group relationships)"]
        for h in hyperedges:
            node_labels = ", ".join(h.get("nodes", []))
            conf = h.get("confidence", "INFERRED")
            cscore = h.get("confidence_score")
            conf_tag = f"{conf} {cscore:.2f}" if cscore is not None else conf
            lines.append(f"- **{h.get('label', h.get('id', ''))}** — {node_labels} [{conf_tag}]")
```

- [ ] **Step 9: 跑测试**

`uv run --with pytest pytest tests/ -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：Step 1 修改的三个 ported 文件转绿；smoke 测试绿（chunk 里的 `"hyperedges": []` 键此后被静默忽略，build 不再读取）；`test_equivalence.py` 若存在可运行环境会红（引用 hyperedge 等价断言），它将在 Task 8 删除——若本地无上游 graphify checkout，该文件本就 skip。全仓 `grep -rn "hyperedge" --include="*.py" skills/ | grep -v pyc` 应只剩注释可选项，目标 0 处。

- [ ] **Step 10: commit**

```bash
git add skills/kg/scripts/ tests/ported/test_build_merge_ported.py tests/ported/test_cache_ported.py tests/ported/test_build_ported.py
git commit -m "feat(kglib): remove the hyperedge mechanism end to end (build/dedup/cache/flow/export/report/kg.py)"
```

---

## Task 5: dedup.py / analyze.py / report.py — 旧类型与旧关系特判清理

**Files:**
- Modify: `skills/kg/scripts/kglib/dedup.py`、`analyze.py`、`report.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `kglib.dedup._FILE_ANCHORED_NONCODE = frozenset({"document"})`（删 `rationale`）
  - 删除 `kglib.dedup._is_code`（`file_type=="code"` 类型已不存在）
  - 删除 `kglib.analyze.find_import_cycles`（仅 report.py 调用，纯代码语料特性）
  - 删除 `kglib.analyze._cross_language`（仅被将删的 `_suppress_structural` 使用）
  - `kglib.analyze._surprise_score` 不再做 calls/uses 抑制与 semantically_similar_to 加分

- [ ] **Step 1: 写/改失败测试**

在 `tests/test_validate_taxonomy.py` 末尾追加（该文件 Task 1 已建，作为分类法行为的单一测试落点）：

```python
def test_dedup_file_anchored_set_contains_only_document():
    from kglib.dedup import _FILE_ANCHORED_NONCODE
    assert _FILE_ANCHORED_NONCODE == frozenset({"document"})


def test_analyze_has_no_legacy_relation_special_cases():
    import kglib.analyze as analyze
    assert not hasattr(analyze, "find_import_cycles")
    assert not hasattr(analyze, "_cross_language")


def test_dedup_has_no_code_branch():
    import kglib.dedup as dedup
    assert not hasattr(dedup, "_is_code")
```

运行确认 3 个 FAIL。

- [ ] **Step 2: dedup.py 清理**

1. 193–198 行注释与常量：

   ```python
   # entity_type values whose identity is anchored to their source location, not
   # their label text. document nodes are file/section-derived and must not be
   # label-merged across files; the concept-like types are intentionally excluded
   # -- they are meant to unify across files (protected from over-merge by the
   # numeric/Jaro guards instead).
   _FILE_ANCHORED_NONCODE = frozenset({"document"})
   ```

2. 201–213 行 `_crossfile_fileanchored_blocked`：两处 `node.get("file_type")` / `neighbor.get("file_type")` → `node.get("entity_type")` / `neighbor.get("entity_type")`；docstring 中 `rationale/document nodes are docstring- and heading-derived` → `document nodes are file/section-derived`。
3. 删除 `_is_code` 函数（253–264 行整段）。
4. 删除两个调用点：
   - 600–604 行（pass 1 候选收集处 `# Code symbols are keyed by ID...` 注释 + `if _is_code(node): continue`）；
   - 656–662 行（pass 2 候选收集处 `# Code symbols are excluded from fuzzy matching too...` 注释 + `if _is_code(node): continue`）。
   删除后概念/文档节点照常进入 label 归并，code 类型已不在白名单中，守卫无对象。
5. 632–638 行注释改写为：

   ```python
   # Cross-file residue: union exact matches across files, but only where
   # it is provably safe. The concept-like entity types are the ones meant to
   # unify across files — document is file-anchored. Provenance is required,
   # and the entropy gate mirrors Pass 2 so short generic labels ("API")
   # stay distinct. Sorting by id keeps the winner order-independent.
   ```

   641 行 `if n.get("file_type") == "concept"` → `if n.get("entity_type") == "concept"`。
6. `grep -n "file_type\|_is_code\|rationale" skills/kg/scripts/kglib/dedup.py` 应为 0 处。

- [ ] **Step 3: analyze.py 清理**

1. 删除 `_cross_language`（48 行起的函数整段，约 48–62 行，含其上方 22–47 行的 Swift/Foundation 注释块若仅服务该函数——以锚点为准：`def _cross_language(src_a: str, src_b: str) -> bool:` 到下一个顶层 `def` 之前；保留 `_is_file_node` 等其他函数）。
2. `_surprise_score`（204–275 行）：
   - 删除 226–238 行 `_suppress_structural` 块（注释 + 赋值 + `if _suppress_structural: conf_bonus = 0`），保留 `score += conf_bonus`；
   - 245 行 `if cat_u != cat_v and not _suppress_structural:` → `if cat_u != cat_v:`；
   - 250 行 `if _top_level_dir(u_source) != _top_level_dir(v_source) and not _suppress_structural:` → 去掉 `and not _suppress_structural`；
   - 257 行 `... and not _suppress_structural:` → 同样去掉；
   - 删除 261–264 行 `# 4b. Semantic similarity bonus` 块（`if data.get("relation") == "semantically_similar_to":` 整段）。
3. `_cross_file_surprises`（298 行）与 `_cross_community_surprises`（390 行）：删除 `if relation in ("imports", "imports_from", "contains", "method"): continue` 两行守卫（新关系集里没有结构边需要排除；新关系全为内容关系，保留过滤只会误伤）。
4. 515–521 行 isolated 节点列表：删除 `and G.nodes[n].get("file_type") != "rationale"` 行。
5. 删除 `find_import_cycles`（641 行到文件尾 750 行整段，含 docstring）。
6. `grep -n "semantically_similar_to\|rationale\|imports\|calls\|file_type" skills/kg/scripts/kglib/analyze.py` 人工核对：允许残留的只有与分类法无关的英文注释措辞；`file_type`、`semantically_similar_to`、`rationale`、`imports_from`、`re_exports` 应为 0 处。

- [ ] **Step 4: report.py 清理**

1. 205 行删除 `sem_tag = ...`，207 行 f-string 中 `{conf_tag}]{sem_tag}` → `{conf_tag}]`。
2. 删除 213–236 行 Import Cycles 整节（`# Circular imports surfaced from file-level dependency graph...` 注释 + `_has_code` 计算 + `if _has_code:` 块，含 `from .analyze import find_import_cycles` 局部 import）。
3. 286 行删除 `and G.nodes[n].get("file_type") != "rationale"`。
4. `grep -n "file_type\|rationale\|semantically_similar_to\|imports" skills/kg/scripts/kglib/report.py` 应为 0 处。

- [ ] **Step 5: 跑测试 + commit**

`uv run --with pytest pytest tests/ -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：Step 1 的 3 个新用例转绿；smoke 的 GRAPH_REPORT.md 断言（`## God Nodes`、`## Surprising Connections`）保持绿；无新增红。

```bash
git add skills/kg/scripts/kglib/dedup.py skills/kg/scripts/kglib/analyze.py skills/kg/scripts/kglib/report.py tests/test_validate_taxonomy.py
git commit -m "refactor(kglib): drop legacy type/relation special cases from dedup, analyze, report"
```

---

## Task 6: export.py — entity_type 渲染 + definition 展示

**Files:**
- Modify: `skills/kg/scripts/kglib/export.py`

**Interfaces:**
- Consumes: 节点属性 `entity_type`（Task 2 起由 build 写入）、`definition`
- Produces:
  - `to_html` 输出的 vis node dict：`"file_type"` 键更名 `"entity_type"`，新增 `"definition"` 键（恒存在，空串合法）
  - HTML 节点卡片（showInfo）：`Type:` 行读 `_entity_type`；definition 非空时在 Source 行之后多渲染一行

- [ ] **Step 1: 写失败测试**

在 `tests/test_validate_taxonomy.py` 末尾追加：

```python
def test_to_html_renders_entity_type_and_definition(tmp_path):
    import networkx as nx
    from kglib.export import to_html
    G = nx.Graph()
    G.add_node("n1", label="元素", entity_type="concept",
               definition="元素是 IDMP 中描述资产的基本单元。",
               source_file="a.md", community=0)
    G.add_node("n2", label="规则X", entity_type="rule", definition="",
               source_file="a.md", community=0)
    out = tmp_path / "graph.html"
    to_html(G, {0: ["n1", "n2"]}, str(out))
    html = out.read_text(encoding="utf-8")
    assert "元素是 IDMP 中描述资产的基本单元。" in html
    assert "_entity_type" in html
    assert "_file_type" not in html
```

运行确认 FAIL（当前 HTML 无 definition 文本、仍含 `_file_type`）。

- [ ] **Step 2: vis node dict 更名 + 新字段（export.py 886 行）**

旧代码：

```python
            "file_type": data.get("file_type", ""),
```

替换为：

```python
            "entity_type": data.get("entity_type", ""),
            "definition": sanitize_label(str(data.get("definition") or "")),
```

- [ ] **Step 3: JS 数据映射（export.py 521 行）**

旧代码：

```python
  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,
```

替换为：

```python
  _source_file: n.source_file, _entity_type: n.entity_type, _definition: n.definition, _degree: n.degree,
```

- [ ] **Step 4: 节点卡片渲染（export.py 573–580 行 showInfo）**

旧代码：

```python
  document.getElementById('info-content').innerHTML = `
    <div class="field"><b>${esc(n.label)}</b></div>
    <div class="field">Type: ${esc(n._file_type || 'unknown')}</div>
    <div class="field">Community: ${esc(n._community_name)}</div>
    <div class="field">Source: ${esc(n._source_file || '-')}</div>
    <div class="field">Degree: ${n._degree}</div>
    ${neighborIds.length ? `<div class="field" style="margin-top:8px;color:#aaa;font-size:11px">Neighbors (${neighborIds.length})</div><div id="neighbors-list">${neighborItems}</div>` : ''}
  `;
```

（注意：该段在 Python f-string 内，源码中实际是 `${{esc(n.label)}}` 双花括号形式，Edit 时以文件实际内容为准。）

替换为：

```python
  document.getElementById('info-content').innerHTML = `
    <div class="field"><b>${{esc(n.label)}}</b></div>
    <div class="field">Type: ${{esc(n._entity_type || 'unknown')}}</div>
    <div class="field">Community: ${{esc(n._community_name)}}</div>
    <div class="field">Source: ${{esc(n._source_file || '-')}}</div>
    ${{n._definition ? `<div class="field" style="margin-top:4px;color:#ddd;font-size:12px;white-space:pre-wrap">${{esc(n._definition)}}</div>` : ''}}
    <div class="field">Degree: ${{n._degree}}</div>
    ${{neighborIds.length ? `<div class="field" style="margin-top:8px;color:#aaa;font-size:11px">Neighbors (${{neighborIds.length}})</div><div id="neighbors-list">${{neighborItems}}</div>` : ''}}
  `;
```

definition 空串时该行为空字符串，不渲染——满足"非空时显示"。所有插值经 `esc()` 转义，与既有 XSS 防护一致。

- [ ] **Step 5: 跑测试 + commit**

`uv run --with pytest pytest tests/test_validate_taxonomy.py tests/ported/test_export_ported.py tests/test_smoke.py -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：新用例转绿；`test_to_html_handles_null_source_file_and_label` 保持绿（`definition` 缺失时 `data.get("definition") or ""` 兜底）。

```bash
git add skills/kg/scripts/kglib/export.py tests/test_validate_taxonomy.py
git commit -m "feat(kglib): render entity_type and non-empty definition in HTML node cards"
```

---

## Task 7: tests/fixtures + write_smoke_chunks.py + test_smoke.py 重写为新 schema

**Files:**
- Modify: `tests/fixtures/extraction_docs.json`（整体重写，18 节点 / 18 边 / 覆盖全部 9 类型与 11 关系 / 无 hyperedges 键）
- Modify: `tests/write_smoke_chunks.py`（helper 签名更名、definition 字段、中文关系、删 hyperedges）
- Modify: `tests/test_smoke.py`（`test_update_modify_no_duplicates` 内联 chunk 重写；其余断言不变）

**Interfaces:**
- Consumes: `VALID_ENTITY_TYPES` / `VALID_RELATIONS`（Task 1）、build 的 entity_type 语义（Task 2）
- Produces:
  - `write_smoke_chunks._node(nid, label, entity_type, source_file, definition="", **extra)` — 参数名 `file_type` → `entity_type`，新增 `definition` 关键字参数（默认 `""`）
  - `write_smoke_chunks._edge(...)` 签名不变
  - fixture 规模锚点：18 节点、18 边（Task 8 的计数断言按此更新）

- [ ] **Step 1: 重写 tests/fixtures/extraction_docs.json**

完整新内容（18 节点覆盖全部 9 种 entity_type，18 条边覆盖全部 11 种关系，所有节点带 definition、部分为空串，所有边端点为不同节点对——无向图同对坍缩为一条边，计数稳定）：

```json
{
  "nodes": [
    {"id": "manual_idmp_manual", "label": "IDMP 用户手册", "entity_type": "document", "definition": "", "source_file": "docs/manual.md", "source_location": null, "source_url": "https://example.com/idmp/manual", "captured_at": "2026-09-01", "author": "IDMP Team", "contributor": "alice"},
    {"id": "manual_element", "label": "元素", "entity_type": "concept", "definition": "元素是 IDMP 中描述资产的基本单元。", "source_file": "docs/manual.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "manual_attribute", "label": "属性", "entity_type": "concept", "definition": "", "source_file": "docs/manual.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "manual_template", "label": "模板", "entity_type": "concept", "definition": "模板是可复用的属性集合定义。", "source_file": "docs/manual.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "manual_sampling_frequency", "label": "采样频率上限", "entity_type": "fact", "definition": "", "source_file": "docs/manual.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_analysis_guide", "label": "分析指南", "entity_type": "document", "definition": "", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_level_volume_principle", "label": "液位容积换算原理", "entity_type": "principle", "definition": "液位与容积利用率是同一物理量的换算。", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_root_cause", "label": "根因分析", "entity_type": "method", "definition": "", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_ai_connection_rule", "label": "AI 功能需要有效的 AI 连接配置", "entity_type": "rule", "definition": "", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_ai_query_procedure", "label": "AI 查询操作流程", "entity_type": "procedure", "definition": "点击 AI 图标→选函数→查看结果。", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_quality_scenario", "label": "质量分析场景", "entity_type": "scenario", "definition": "", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "analysis_no_resource_keypoint", "label": "通用分析不创建资源", "entity_type": "keypoint", "definition": "", "source_file": "docs/analysis.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "glossary_glossary", "label": "术语表", "entity_type": "document", "definition": "", "source_file": "docs/glossary.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "glossary_panel", "label": "面板", "entity_type": "concept", "definition": "面板是可视化元素的容器。", "source_file": "docs/glossary.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "glossary_event", "label": "事件", "entity_type": "concept", "definition": "", "source_file": "docs/glossary.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "ops_spc_monitoring", "label": "SPC 监控", "entity_type": "scenario", "definition": "", "source_file": "notes/ops.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "ops_session_independence", "label": "AI Function 会话独立", "entity_type": "keypoint", "definition": "", "source_file": "notes/ops.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null},
    {"id": "faq_realtime_analysis", "label": "实时分析", "entity_type": "concept", "definition": "", "source_file": "notes/faq.md", "source_location": null, "source_url": null, "captured_at": null, "author": null, "contributor": null}
  ],
  "edges": [
    {"source": "manual_element", "target": "analysis_level_volume_principle", "relation": "阐述", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "manual_attribute", "target": "manual_element", "relation": "归属", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "manual_template", "target": "manual_attribute", "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_ai_query_procedure", "target": "analysis_ai_connection_rule", "relation": "前置", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_root_cause", "target": "analysis_quality_scenario", "relation": "解决", "confidence": "INFERRED", "confidence_score": 0.85, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_ai_connection_rule", "target": "ops_session_independence", "relation": "导致", "confidence": "AMBIGUOUS", "confidence_score": 0.25, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "manual_sampling_frequency", "target": "faq_realtime_analysis", "relation": "限制", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_ai_query_procedure", "target": "analysis_root_cause", "relation": "顺序", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_level_volume_principle", "target": "analysis_no_resource_keypoint", "relation": "推导", "confidence": "INFERRED", "confidence_score": 0.75, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_root_cause", "target": "ops_spc_monitoring", "relation": "适用", "confidence": "INFERRED", "confidence_score": 0.65, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "manual_sampling_frequency", "target": "analysis_quality_scenario", "relation": "影响", "confidence": "INFERRED", "confidence_score": 0.85, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "manual_idmp_manual", "target": "manual_element", "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "manual_idmp_manual", "target": "manual_template", "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/manual.md", "source_location": null, "weight": 1.0},
    {"source": "analysis_analysis_guide", "target": "analysis_root_cause", "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/analysis.md", "source_location": null, "weight": 1.0},
    {"source": "glossary_glossary", "target": "glossary_panel", "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/glossary.md", "source_location": null, "weight": 1.0},
    {"source": "glossary_glossary", "target": "glossary_event", "relation": "组成", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/glossary.md", "source_location": null, "weight": 1.0},
    {"source": "glossary_panel", "target": "analysis_quality_scenario", "relation": "适用", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "docs/glossary.md", "source_location": null, "weight": 1.0},
    {"source": "faq_realtime_analysis", "target": "ops_spc_monitoring", "relation": "影响", "confidence": "INFERRED", "confidence_score": 0.65, "source_file": "notes/faq.md", "source_location": null, "weight": 1.0}
  ],
  "input_tokens": 0,
  "output_tokens": 0
}
```

设计不变量（Task 8 断言依据）：18 节点、18 边、18 个节点对全部唯一（无向图不坍缩）、图连通（单连通分量，cluster 稳定）、覆盖 9 类型 × 11 关系全集、5 个节点 definition 非空、其余为空串。

- [ ] **Step 2: 重写 tests/write_smoke_chunks.py**

完整新内容：

```python
"""Standalone writer for the smoke-test extraction chunks.

Extracted from ``tests/conftest.py`` so the hand-written extraction payloads
can be produced outside pytest, e.g.::

    python tests/write_smoke_chunks.py ./corpus

Writes ``<corpus>/kg-out/.kg_chunk_01.json`` and ``.kg_chunk_02.json``
mimicking what the host agent (the LLM extractor) would write.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _node(nid, label, entity_type, source_file, definition="", **extra):
    n = {
        "id": nid, "label": label, "entity_type": entity_type,
        "definition": definition,
        "source_file": source_file, "source_location": None,
        "source_url": None, "captured_at": None, "author": None, "contributor": None,
    }
    n.update(extra)
    return n


def _edge(source, target, relation, confidence, score, source_file):
    return {
        "source": source, "target": target, "relation": relation,
        "confidence": confidence, "confidence_score": score,
        "source_file": source_file, "source_location": None, "weight": 1.0,
    }


def write_smoke_chunks(corpus: Path) -> int:
    """Hand-written extraction chunks covering the docs_corpus fixture.

    Mimics what the host agent (the LLM extractor) would write to
    kg-out/.kg_chunk_NN.json. Returns the number of chunk files written.
    """
    a = str(corpus / "alpha.md")
    b = str(corpus / "beta.md")
    g = str(corpus / "docs" / "gamma.md")
    c = str(corpus / "docs" / "config.md")
    d = str(corpus / "docs" / "delta.md")

    chunk1 = {
        "nodes": [
            _node("alpha_alpha_service", "Alpha Service", "document", a),
            _node("alpha_authentication", "Authentication", "concept", a,
                  definition="Authentication verifies the caller's identity."),
            _node("beta_beta_component", "Beta Component", "document", b),
            _node("beta_session_management", "Session Management", "concept", b),
            _node("beta_token_refresh", "Token Refresh", "procedure", b),
        ],
        "edges": [
            _edge("alpha_alpha_service", "alpha_authentication", "组成", "EXTRACTED", 1.0, a),
            _edge("alpha_alpha_service", "beta_beta_component", "影响", "EXTRACTED", 1.0, a),
            _edge("beta_beta_component", "beta_session_management", "组成", "EXTRACTED", 1.0, b),
            _edge("beta_session_management", "beta_token_refresh", "顺序", "INFERRED", 0.85, b),
        ],
        "input_tokens": 0, "output_tokens": 0,
    }
    chunk2 = {
        "nodes": [
            _node("docs_gamma_gamma_audit_log", "Gamma Audit Log", "document", g),
            _node("docs_config_config_loader", "Config Loader", "document", c),
            _node("docs_delta_delta_metrics", "Delta Metrics", "document", d),
        ],
        "edges": [
            _edge("docs_gamma_gamma_audit_log", "alpha_authentication", "适用", "EXTRACTED", 1.0, g),
            _edge("alpha_alpha_service", "docs_config_config_loader", "前置", "EXTRACTED", 1.0, c),
            _edge("beta_beta_component", "docs_config_config_loader", "前置", "EXTRACTED", 1.0, c),
            _edge("docs_delta_delta_metrics", "docs_config_config_loader", "前置", "EXTRACTED", 1.0, d),
            _edge("docs_delta_delta_metrics", "docs_gamma_gamma_audit_log", "影响", "INFERRED", 0.65, d),
        ],
        "input_tokens": 0, "output_tokens": 0,
    }

    out = corpus / "kg-out"
    out.mkdir(parents=True, exist_ok=True)
    for i, chunk in enumerate((chunk1, chunk2), 1):
        (out / f".kg_chunk_{i:02d}.json").write_text(
            json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
    return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Write the smoke-test extraction chunks into <corpus>/kg-out.")
    parser.add_argument("corpus", type=Path, help="corpus root directory")
    args = parser.parse_args()
    n = write_smoke_chunks(args.corpus.resolve())
    print(f"chunks written: {n}")
```

拓扑与旧版完全一致（8 节点、9 边、Gamma→Config 最短 2 跳、Config Loader 3 条连接），因此 test_smoke.py 的计数与路径断言不变。

- [ ] **Step 3: 重写 test_smoke.py 内联 chunk（127–153 行）**

`test_update_modify_no_duplicates` 中的 chunk 字面量替换为：

```python
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
```

- [ ] **Step 4: 新增 fixture 自检测试**

在 `tests/test_validate_taxonomy.py` 末尾追加：

```python
def test_extraction_docs_fixture_covers_full_taxonomy():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures" / "extraction_docs.json")
                         .read_text(encoding="utf-8"))
    assert validate_extraction(fixture) == []
    assert "hyperedges" not in fixture
    assert {n["entity_type"] for n in fixture["nodes"]} == VALID_ENTITY_TYPES
    assert {e["relation"] for e in fixture["edges"]} == VALID_RELATIONS
    assert all("definition" in n for n in fixture["nodes"])
    assert any(n["definition"] == "" for n in fixture["nodes"])
    assert any(n["definition"] for n in fixture["nodes"])
    assert len(fixture["nodes"]) == 18
    assert len(fixture["edges"]) == 18
```

- [ ] **Step 5: 跑测试**

`uv run --with pytest pytest tests/test_validate_taxonomy.py tests/test_smoke.py -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：fixture 自检与 smoke 全绿（不再有 validation warning）。`tests/ported/test_build_ported.py` 中 fixture 计数用例（`test_build_from_json_node_count` 断言 17、`test_build_from_json_edge_count` 断言 18、`test_nodes_have_label`、`test_edges_have_confidence`、`test_ambiguous_edge_preserved`）转红，属预期，Task 8 修复。

- [ ] **Step 6: commit**

```bash
git add tests/fixtures/extraction_docs.json tests/write_smoke_chunks.py tests/test_smoke.py tests/test_validate_taxonomy.py
git commit -m "test(kg): rewrite extraction fixtures and smoke chunks for the 9-type/11-relation taxonomy"
```

---

## Task 8: tests/ported/ 更新 + 删除 test_equivalence.py + README 加注

**Files:**
- Delete: `tests/test_equivalence.py`
- Modify: `tests/ported/test_build_ported.py`、`tests/ported/test_build_merge_ported.py`、`tests/ported/test_serve_ported.py`、`tests/ported/test_serve_engine_ported.py`、`tests/ported/test_query_cli_ported.py`、`tests/ported/test_query_induced_edges_ported.py`、`tests/ported/README.md`、`tests/conftest.py`

**Interfaces:**
- Consumes: 新 fixture（Task 7）的 18 节点/18 边不变量
- Produces: ported 套件全部改用 entity_type/definition/中文关系；与上游 graphify 的等价性测试层删除

- [ ] **Step 1: 删除 test_equivalence.py + 清理 conftest**

```bash
git rm tests/test_equivalence.py
```

`tests/conftest.py`：删除 24–25 行：

```python
# Upstream graphify checkout (used by Layer 1 equivalence tests only).
UPSTREAM_DIR = LAMPKB_ROOT.parent / "graphify"
```

- [ ] **Step 2: test_build_ported.py — fixture 计数与 id 断言更新**

- 文件头注释第 9–10 行中 `(the upstream fixtures/extraction.json is code-flavored; counts adapted)` 后追加一句：`; rewritten 2026-09-10 for the 9-entity-type/11-relation taxonomy (18 nodes, 18 edges)`。
- 第 12 行注释 `file_type canonicalization (#660/#840)` → `entity_type canonicalization`。
- `test_build_from_json_node_count`（74–76 行）：`assert G.number_of_nodes() == 17` → `== 18`。
- `test_build_from_json_edge_count`（79–81 行）：保持 `== 18`（数值不变，注释无需动）。
- `test_nodes_have_label`（84–86 行）：

  ```python
  def test_nodes_have_label():
      G = build_from_json(load_extraction())
      assert G.nodes["manual_element"]["label"] == "元素"
  ```

- `test_edges_have_confidence`（89–92 行）：

  ```python
  def test_edges_have_confidence():
      G = build_from_json(load_extraction())
      data = G.edges["analysis_root_cause", "analysis_quality_scenario"]
      assert data["confidence"] == "INFERRED"
  ```

- `test_ambiguous_edge_preserved`（95–98 行）：

  ```python
  def test_ambiguous_edge_preserved():
      G = build_from_json(load_extraction())
      data = G.edges["analysis_ai_connection_rule", "ops_session_independence"]
      assert data["confidence"] == "AMBIGUOUS"
  ```

- [ ] **Step 3: test_build_ported.py — 机械更名 + 手工补 definition**

机械更名（精确命令）：

```bash
sed -i 's/"file_type"/"entity_type"/g; s/file_type=/entity_type=/g' tests/ported/test_build_ported.py tests/ported/test_build_merge_ported.py
```

人工核对清单（更名后逐条过）：

1. `test_legacy_node_name_path_aliases_folded`（约 168–183 行）：节点 dict 补 `"definition": ""`，且末尾断言改为只对 label/source_file 生效——该用例在 Task 1 后已红，改为：

   ```python
   def test_legacy_node_name_path_aliases_folded():
       """Nodes carrying `name`/`path` instead of `label`/`source_file` must
       be canonicalized before validation, not enter the graph as label-less
       ghosts."""
       from kglib.validate import validate_extraction
       ext = {"nodes": [{"id": "n1", "name": "Foo", "path": "a/b.md",
                         "entity_type": "concept", "definition": ""}],
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
   ```

2. 其余所有节点 dict 没有 definition 字段 —— build 仅警告不报错，断言不受影响，**可以不加**；但凡是 capsys 断言 err 内容的用例需逐一核对：
   - `test_extraction_warning_breakdown_by_cause`（约 229–247 行）：断言 `"2x missing required field 'label'"` 与 `"3x missing required field 'relation'"` 为子串匹配，新增的 definition 警告不破坏它，**不动**。
3. `test_doc_twin_merge_does_not_touch_non_document_nodes`（约 776–789 行）：`"entity_type": "rationale"` 已非法，改为 `"entity_type": "method"`（用例意图：非 document 类型的 foo_doc 不参与 doc-twin 合并，任何非 document 类型均可）。
4. `test_build_from_json_preserves_first_direction_on_bidirectional_pair`（约 576–609 行）：`"relation": "cites"` ×3 处 → `"relation": "归属"`；`saved_cites` 变量更名 `saved_edges`，断言中 `e.get("relation") == "cites"` → `== "归属"`。
5. 全文件残留旧关系名替换（sed + 人工核对）：

   ```bash
   sed -i 's/"relation": "references"/"relation": "归属"/g; s/"relation": "cites"/"relation": "阐述"/g; s/"relation": "relates_to"/"relation": "影响"/g; s/"relation": "describes"/"relation": "阐述"/g; s/"relation": "links_to"/"relation": "影响"/g; s/"relation": "self"/"relation": "影响"/g' tests/ported/test_build_ported.py
   ```

   注意 `test_dedupe_edges_collapses_exact_parallels`（32–43 行）等纯函数用例不经过 validate，关系值任意，但统一替换保持语义一致。`edge_data`/`edge_datas` 系列用例（612–688 行）直接 `G.add_edge(..., relation="cites")`，不经过 schema 校验，可保留原值——为一致性同样替换：

   ```bash
   sed -i 's/relation="references"/relation="归属"/g; s/relation="cites"/relation="阐述"/g' tests/ported/test_build_ported.py
   ```

   `grep -n "references\|cites\|semantically_similar_to\|conceptually_related_to\|rationale_for\|file_type" tests/ported/test_build_ported.py` 应为 0 处（注释中的上游 issue 引用除外）。

- [ ] **Step 4: test_build_merge_ported.py — _node 补新字段**

`_node` helper（16–26 行）改为：

```python
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
```

`_seed_two_docs` 中两个字面量节点（120–128 行）与新 chunk 字面量（136–139、151–154 行）执行同样的 sed 更名（Step 3 命令已覆盖 `"file_type"` → `"entity_type"`），并各补 `"definition": ""`。`grep -n "file_type\|hyperedge" tests/ported/test_build_merge_ported.py` 应为 0 处。

- [ ] **Step 5: query/serve 系列 ported 测试的关系值替换**

这些测试直接 `G.add_edge(..., relation="references"/"cites")` 构造内存图并断言输出文本，不经过 validate，但必须换成合法关系保持语义真实：

```bash
sed -i 's/relation="references"/relation="归属"/g; s/relation="cites"/relation="阐述"/g' \
    tests/ported/test_serve_ported.py tests/ported/test_serve_engine_ported.py \
    tests/ported/test_query_cli_ported.py tests/ported/test_query_induced_edges_ported.py
sed -i 's/--references/--归属/g; s/"references"/"归属"/g' \
    tests/ported/test_serve_engine_ported.py tests/ported/test_query_cli_ported.py \
    tests/ported/test_query_induced_edges_ported.py
```

人工核对清单：
- `test_query_induced_edges_ported.py` 140 行 `assert _pairs(edges) == {frozenset(("n1", "n2"))}, "a cites edge came back"` —— 断言消息中的 `"a cites edge came back"` 是英文文案可保留，也可顺手改 `"a 阐述 edge came back"`；176、203、221–222 行的 `--references` 文本断言已被第二条 sed 覆盖。
- `test_query_cli_ported.py` 67–68、77–78 行 `--references` 断言同上。
- `grep -n "references\|cites" tests/ported/test_serve_ported.py tests/ported/test_serve_engine_ported.py tests/ported/test_query_cli_ported.py tests/ported/test_query_induced_edges_ported.py` 人工确认剩余 0 处或仅在英文消息文案中。
- `test_querylog_ported.py`、`test_detect_ported.py`、`test_cache_portability_ported.py`、`test_export_ported.py` 经 grep 确认无分类法引用，不动。

- [ ] **Step 6: tests/ported/README.md 加注**

在文件末尾（34 行后）追加：

```markdown

## 2026-09-10: 分类法独立演进

kg 的实体/关系分类法已替换为 9 实体类型（entity_type 英文 key）+ 11 中文关系
（见 `docs/superpowers/specs/2026-09-10-extraction-taxonomy-design.md` 与
`docs/adr/0001-extraction-taxonomy-replacement.md`），超边机制删除。
`tests/test_equivalence.py`（与上游 graphify 的等价性测试层）随之删除——
schema 已分叉，等价性前提不再成立。本目录中的用例数据已全部改用新分类法；
后续与上游的逐行比对不再有意义，仅保留移植注释作为历史溯源。
```

- [ ] **Step 7: 跑全量测试**

`uv run --with pytest pytest tests/ -q --deselect tests/test_smoke.py::test_full_pipeline_subprocess`

预期：**全绿**（约 210 个用例；test_equivalence.py 删除后总数减少）。若有个别 sed 误伤（如英文消息文案中的 references），按失败输出逐点手工修正。

- [ ] **Step 8: commit**

```bash
git add -A tests/
git commit -m "test(kg): port test suite to new taxonomy, drop graphify equivalence layer"
```

---

## Task 9: SKILL.md Step 3 重写（抽取规范主体）

**Files:**
- Modify: `skills/kg/SKILL.md`（113–192 行的 extraction spec 代码块整体替换；100 行 "Only `document` and `paper` files participate" 保留）

**Interfaces:**
- Consumes: spec 的 9 类型表 / 11 关系表 / definition 规则（逐字落入提示词）
- Produces: 新抽取提示词；schema 行含 `entity_type` 与 `definition`，无 `hyperedges` 数组；`source_file RULE` 措辞删去 "and hyperedge"

- [ ] **Step 1: 替换 113–192 行**

旧内容是从 `The extraction spec (apply verbatim; substitute FILE_LIST, CHUNK_NUM, TOTAL_CHUNKS, DEEP_MODE, CHUNK_PATH):`（113 行）到 schema/写盘说明结束的 ``` 围栏（192 行）。整体替换为（以下为最终落盘文本，逐字使用）：

````markdown
The extraction spec (apply verbatim; substitute FILE_LIST, CHUNK_NUM, TOTAL_CHUNKS, DEEP_MODE, CHUNK_PATH):

```
Extract a knowledge graph fragment from the documents listed below.
Output ONLY valid JSON matching the schema at the end - no explanation, no markdown fences, no preamble.

Files (batch CHUNK_NUM of TOTAL_CHUNKS):
FILE_LIST

Entity types: every node's `entity_type` MUST be one of exactly these nine
English keys. Any other value is invalid and will be rejected.

| entity_type | 中文 | 定义 | 判定规则 | 示例 |
|---|---|---|---|---|
| concept | 概念 | 有明确定义的术语/对象/抽象概念 | 文档的术语表章节，或在文档中反复出现的高频词 | 元素、属性、模板、面板、实时分析、事件、MCP |
| principle | 原理 | 机理知识与底层逻辑 | 回答"为什么要这么做"的依据 | 液位与容积利用率是同一物理量的换算 |
| method | 方法 | 一套可执行的做法/方案 | 有步骤、可复用、有名称 | 根因分析、面板解读、流式计算 |
| rule | 规则 | 条件/前提/判断规则 | "必须/不能/仅当/依赖"类表述 | AI 功能需要有效的 AI 连接配置 |
| procedure | 操作 | 多步骤的执行方案 | 包含先后顺序的动作设定 | 点击 AI 图标→选函数→查看结果 |
| fact | 事实 | 客观数值/边界/默认值 | 数字、单位、上限、默认值 | 量程、限值、采样频率 |
| scenario | 场景 | 业务场景的描述 | "用于……场景"类表述 | 质量分析、异常初筛、SPC 监控 |
| keypoint | 要点 | 结论/关键论点 | 强调的总结性陈述 | 通用分析不创建资源；AI Function 会话独立 |
| document | 文档 | 信息来源 | Document files 和章节 | IDMP 用户手册 8.11 |

`definition` attribute (REQUIRED on every node):
- Extract the entity's definition ONLY when the document defines it explicitly —
  a glossary entry, or an "X 是指/是…" statement. Copy the source wording verbatim.
- Otherwise set "definition": "". NEVER summarize, paraphrase, or invent a definition.

Relations: every edge's `relation` MUST be one of exactly these eleven Chinese
values. All relations are binary (source -> target). Any other value is invalid
and will be rejected.

| relation | 语义 | 典型问题 |
|---|---|---|
| 阐述 | 概念 > 依据/原理 | X 为什么是这样 |
| 归属 | 子概念归属父概念 | X 属于 Y |
| 组成 | 部分组成整体 | X 包括 Y/Z |
| 前置 | 目标拥有前置条件 | 做 X 前需要满足 Y |
| 解决 | 方法解决问题 | X 能解决 Y |
| 导致 | 原因带来结果 | X 会带来 Y |
| 限制 | 知识点拥有约束/边界 | X 有什么限制 |
| 顺序 | 前一步>后一步，即包含顺序的多个步骤 | 步骤1>步骤2>步骤3 |
| 推导 | 论据推导出论点 | 因为 X，所以 Y |
| 适用 | 知识点使用的对象/场景 | X 适用于 Y |
| 影响 | 知识点1 改变/波及 知识点2 | X 会影响 Y |

Rules:
- EXTRACTED: relationship explicit in source (citation, "see §3.2", named cross-reference)
- INFERRED: reasonable inference (shared concept, implied dependency)
- AMBIGUOUS: uncertain - flag for review, do not omit

DEEP_MODE (if --mode deep was given): be aggressive with INFERRED edges - indirect deps,
  shared assumptions, latent couplings. Mark uncertain ones AMBIGUOUS instead of omitting.

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
  directory level, not just the immediate parent. Chinese labels: use a short English
  slug for the entity part (元素 → element, 面板 → panel) and keep the Chinese text in
  `label`. Examples: `docs/v1/api/README.md` + `Rate Limiting` →
  `docs_v1_api_readme_rate_limiting`; top-level file `notes.md` + `CAP theorem` →
  `notes_cap_theorem`. CRITICAL: never append batch numbers, sequence numbers, or any
  suffix to an ID. IDs must be deterministic from the label alone — the same entity
  must always produce the same ID regardless of which batch processes it.

Generate the extraction JSON matching this schema exactly:
{"nodes":[{"id":"docs_manual_element","label":"元素","entity_type":"concept","definition":"元素是 IDMP 中描述资产的基本单元。","source_file":"<FILE_LIST path verbatim>","source_location":null,"source_url":null,"captured_at":null,"author":null,"contributor":null}],"edges":[{"source":"node_id","target":"node_id","relation":"阐述|归属|组成|前置|解决|导致|限制|顺序|推导|适用|影响","confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,"source_file":"<FILE_LIST path verbatim>","source_location":null,"weight":1.0}],"input_tokens":0,"output_tokens":0}

source_file RULE (every node and edge): set source_file to the path of the
  originating file EXACTLY as it appears in FILE_LIST — verbatim and absolute. Do NOT
  shorten to a basename, do NOT re-relativize, do NOT strip any directory prefix. Copy
  the FILE_LIST entry character-for-character. This keeps the full build and incremental
  --update on the same base, so replace-on-re-extract matches the existing node instead
  of accumulating a duplicate.

Write the JSON to this exact absolute path:
CHUNK_PATH
```
````

相对旧版删除的内容（核对清单，确认均不再出现）：`Doc/paper files: ... file_type:"rationale" ...` 整段（127–133 行）、`Semantic similarity` 小节（138–146 行）、`Hyperedges` 小节（148–154 行）、schema 行中的 `file_type`/`hyperedges`/`participate_in|form`、`source_file RULE` 里的 "and hyperedge"。confidence 规则与 Node ID 规则原样保留（仅 ID 规则新增一句中文 label 的 slug 指引——新语料为纯中文，id 只允许 `[a-z0-9_]`，必须给模型明确指引，否则批量产出非法 id）。

- [ ] **Step 2: 全文件核对**

```bash
grep -n "hyperedge\|file_type\|rationale\|semantically_similar_to\|conceptually_related_to\|participate_in\|references|cites" skills/kg/SKILL.md
```

应为 0 处（100 行的 "Only `document` and `paper` files participate" 中 "participate" 是英文动词，不是 `participate_in` 关系，保留）。

- [ ] **Step 3: 缓存指纹说明（无需改代码）**

`save_semantic_cache`/`check_semantic_cache` 支持 prompt 指纹命名空间，但 kg.py 当前调用点不传 prompt——提示词改版后旧缓存仍会命中。本计划不引入 prompt 指纹接线（属超范围改动）；实施时在 commit message 与给用户的交付说明中明确：**部署后需删除 `kg-out/cache/` 或重跑全量抽取**（spec 已声明存量产物不迁移）。

- [ ] **Step 4: commit**

```bash
git add skills/kg/SKILL.md
git commit -m "docs(kg): rewrite extraction spec for 9 entity types, 11 Chinese relations, definition attribute"
```

---

## Task 10: 全量测试 + 端到端冒烟

**Files:**
- 无代码改动（验证任务）。冒烟产物写入 `tmp/kg-out/` 或临时目录，不入库。

- [ ] **Step 1: 全量测试**

```bash
uv run --with pytest pytest tests/ -q
```

含 `test_full_pipeline_subprocess`（真实 `uv run kg.py` 子进程链）。预期全绿（约 211 个用例）。

- [ ] **Step 2: 端到端冒烟（无 LLM 的真实流水线）**

`corpus/` 与 `samples/` 当前为空目录，端到端冒烟用 conftest 的 5 文件语料物化到临时目录执行（抽取步由 write_smoke_chunks 模拟 host agent——这是仓库既有的冒烟模式，真实 LLM 抽取在下次语料重跑时发生）：

```bash
SMOKE=$(mktemp -d)
mkdir -p "$SMOKE/docs"
cat > "$SMOKE/alpha.md" <<'EOF'
# Alpha Service

Alpha is the entry point service. It handles authentication requests and
delegates session management to the Beta component. See the Beta notes for
the token refresh flow. Alpha depends on the shared Config Loader.
EOF
cat > "$SMOKE/beta.md" <<'EOF'
# Beta Component

Beta manages sessions. It issues tokens and refreshes them on expiry.
Beta reads its timeouts from the Config Loader and writes audit events
to the Gamma audit log.
EOF
cat > "$SMOKE/docs/gamma.md" <<'EOF'
# Gamma Audit Log

Gamma is the audit log. Every authentication decision made by Alpha is
recorded here. Gamma batches writes for throughput.
EOF
cat > "$SMOKE/docs/config.md" <<'EOF'
# Config Loader

The Config Loader reads YAML configuration and provides typed settings
to Alpha and Beta. It watches the config file for changes and reloads.
EOF
cat > "$SMOKE/docs/delta.md" <<'EOF'
# Delta Metrics

Delta collects metrics from Alpha, Beta and Gamma. It exposes a Prometheus
endpoint. Delta uses the Config Loader for scrape intervals.
EOF
(cd "$SMOKE" && uv run /home/algo/pyspace/wiki-llm/lampkb/skills/kg/scripts/kg.py prepare --root .)
uv run python tests/write_smoke_chunks.py "$SMOKE"
(cd "$SMOKE" && uv run /home/algo/pyspace/wiki-llm/lampkb/skills/kg/scripts/kg.py merge-extraction --root .)
(cd "$SMOKE" && uv run /home/algo/pyspace/wiki-llm/lampkb/skills/kg/scripts/kg.py build --root .)
(cd "$SMOKE" && uv run /home/algo/pyspace/wiki-llm/lampkb/skills/kg/scripts/kg.py export-html)
```

（kg.py 是 PEP 723 脚本，`uv run <绝对路径>` 自带依赖；prepare 必须先跑以生成 batch/cache 底座，再由 write_smoke_chunks 写 chunk——与 test_smoke 的顺序一致。）

- [ ] **Step 3: 验证冒烟产物（新 schema 落地）**

```bash
python - "$SMOKE" <<'EOF'
import json, sys
from pathlib import Path
smoke = Path(sys.argv[1])
g = json.loads((smoke / "kg-out" / "graph.json").read_text(encoding="utf-8"))
assert "hyperedges" not in g, "graph.json must not carry hyperedges"
assert "hyperedges" not in (g.get("graph") or {}), "nested graph attrs must not carry hyperedges"
types = {n.get("entity_type") for n in g["nodes"]}
assert types and types <= {"concept", "principle", "method", "rule", "procedure",
                           "fact", "scenario", "keypoint", "document"}, types
assert all("definition" in n for n in g["nodes"]), "every node carries definition"
assert all("file_type" not in n for n in g["nodes"]), "no legacy file_type key"
rels = {e.get("relation") for e in g["links"]}
assert rels <= {"阐述","归属","组成","前置","解决","导致","限制","顺序","推导","适用","影响"}, rels
html = (smoke / "kg-out" / "graph.html").read_text(encoding="utf-8")
assert "Authentication verifies the caller's identity." in html, "definition rendered in HTML"
assert "_entity_type" in html and "_file_type" not in html
report = (smoke / "kg-out" / "GRAPH_REPORT.md").read_text(encoding="utf-8")
assert "## Hyperedges" not in report and "## Import Cycles" not in report
print("SMOKE OK:", g["directed"], len(g["nodes"]), "nodes", len(g["links"]), "edges")
EOF
```

全部断言通过即冒烟成功。

- [ ] **Step 4: 清理 + 最终 commit（如有文档尾注需要）**

```bash
rm -rf "$SMOKE"
```

确认 `git status` 干净（冒烟产物在 mktemp 目录，不入库）。本任务无新 commit；若前序任务有遗漏文件，补一个 `chore(kg): final taxonomy rollout fixes` 提交。

---

## 交付说明（实施后向用户传达）

- 存量 `kg-out/`、`kg-all3-0820/kg-out/` 为旧分类法产物，保留为历史；新流水线产出需删除 `kg-out/cache/`（提示词改版但缓存未接 prompt 指纹）后用新 SKILL.md 重新抽取。
- 已知不兼容：旧 graph.json（含 file_type/hyperedges/旧关系）喂给新 `build_merge --update` 会触发 validate 警告（旧关系全部非法），节点/边保留但关系值不合法——spec 明确不做兼容，属预期。
