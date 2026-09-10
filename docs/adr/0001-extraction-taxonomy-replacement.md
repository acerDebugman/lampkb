# 抽取分类法替换：9 实体类型 + 11 关系，并与上游 graphify 决裂

本项目从 graphify 继承的分类法（实体 `document/paper/rationale/concept`，关系 `references/cites/...`，另含超边 `participate_in/form`）面向"代码+文档混合语料"，与本项目纯中文文档语料（如 IDMP 用户手册）不匹配。2026-09-10 起替换为面向文档知识抽取的新体系：9 种实体类型（concept/principle/method/rule/procedure/fact/scenario/keypoint/document，存英文 key）与 11 种二元关系（阐述/归属/组成/前置/解决/导致/限制/顺序/推导/适用/影响，直存中文值），实体新增必备属性 `definition`（仅提取文档中明确定义的原文，无则空串，禁止编造）。

## Considered Options

- **实体类型值用中文**：与关系值风格统一，但实体分类字段在代码中参与大量比较与归一化，英文 key 与既有基础设施（同义词归一化、白名单校验）更一致，且抽取提示词会给出中英文对照，LLM 输出稳定性更好。
- **关系值翻译成英文**：关系值在图谱展示和查询中直接面向中文用户，直存中文最忠实于语义定义；且下游关系特判逻辑本次全部重写，无英文存量负担。
- **保留与上游 graphify 的等价性测试**：等价性测试的前提是两边 schema 一致；分类法是 fork 的核心差异点，保留等价性测试等于永久冻结分类法。故删除 `tests/test_equivalence.py`，项目自此独立演进。

## Consequences

- 字段 `file_type` 更名为 `entity_type`，波及 validate/build/dedup/export 与全部测试 fixture。
- 超边机制（hyperedge）整条流水线删除：新关系集全为二元关系，无超边语义。
- 代码场景守卫（calls/imports/re_exports 幻影边清理）与旧关系特判（generic 折叠、semantically_similar_to 特殊处理）一并删除。
- 不做旧数据兼容：`kg-out/`、`kg-all3-0820/kg-out/` 存量图谱保留为历史产物，新分类法数据需用新提示词重新抽取。
- `validate.py` 新增关系白名单；`definition` 纳入节点必填字段（空串合法、缺失报错）。
