# 抽取分类法替换设计（9 实体类型 + 11 关系 + definition 属性）

日期：2026-09-10
状态：已与用户对齐（grill-with-docs 两轮问答 + 设计确认）
关联文档：`docs/CONTEXT.md`（词条与 Session 3 决策）、`docs/adr/0001-extraction-taxonomy-replacement.md`

## 背景

本项目（lampkb）的 kg skill 从 graphify 继承了面向"代码+文档混合语料"的分类法：实体 `document/paper/rationale/concept`，关系 `references/cites/conceptually_related_to/semantically_similar_to/rationale_for`，另有超边 `participate_in/form`。本项目语料为纯中文文档（如 IDMP 用户手册），旧分类法不匹配，需整体替换。

## 目标分类法

### 实体类型（9 种，`entity_type` 字段存英文 key）

| key | 中文 | 定义 | 判定规则 | 示例（IDMP 用户手册） |
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

### 关系（11 种，`relation` 字段直存中文值，均为二元关系）

| 关系 | 语义 | 典型问题 |
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

### 实体新属性：definition

- 仅当文档中存在对该实体的明确定义（术语表条目、"X 是指/是…"式表述）时提取原文。
- 不存在时置为空字符串 `""`；禁止模型自行总结或编造。
- 校验层面：字段必须存在（缺失报错），空串合法。

## 已锁定的关键决策

1. 实体类型存英文 key，关系直存中文值（ADR-0001 记录了被拒的替代方案）。
2. 不做旧数据兼容：旧类型值（paper/rationale/code/image）与旧关系值全部废弃，无同义词映射；漂移归一化表从空开始。存量产物（`kg-out/`、`kg-all3-0820/kg-out/`）不迁移、不重跑。
3. 字段 `file_type` 更名为 `entity_type`，一次性改透。
4. `validate.py` 新增关系白名单校验；`definition` 纳入节点必填字段。
5. 删除超边机制整条流水线；删除 calls/imports/re_exports 等代码场景守卫；删除 `semantically_similar_to`/`conceptually_related_to` 等旧关系特判。
6. confidence 枚举（EXTRACTED/INFERRED/AMBIGUOUS）原样保留。
7. `tests/test_equivalence.py` 删除，与上游 graphify 彻底分道扬镳。
8. definition 展示：仅 export.py 的 HTML 节点卡片（非空时渲染）；GRAPH_REPORT.md 不动；serve.py 不感知分类法，不动。

## 改动清单

### 1. 抽取规范（`skills/kg/SKILL.md` Step 3，约 113–192 行重写）

- 实体类型表：9 种，含 中文名/英文 key/定义/判定规则/示例（内容取自上表）。
- definition 属性规则：明确定义才提取、否则 `""`、禁止编造。
- 关系表：11 种中文关系，附语义与典型问题。
- 节点 schema：`{id, label, entity_type, definition, source_file, source_location, source_url, captured_at, author, contributor}`。
- 边 schema：`relation` 枚举改为 11 个中文值。
- 删除：超边（hyperedge）小节、"rationale 存为属性不建节点"规则。
- confidence 规则原样保留。

### 2. kglib 改造（`skills/kg/scripts/kglib/`）

- `validate.py`
  - `VALID_FILE_TYPES` → `VALID_ENTITY_TYPES = {concept, principle, method, rule, procedure, fact, scenario, keypoint, document}`。
  - 新增 `VALID_RELATIONS = {阐述, 归属, 组成, 前置, 解决, 导致, 限制, 顺序, 推导, 适用, 影响}`，启用关系白名单校验。
  - `REQUIRED_NODE_FIELDS = {id, label, entity_type, definition, source_file}`（definition 空串合法）。
- `build.py`
  - `_FILE_TYPE_SYNONYMS` → `_ENTITY_TYPE_SYNONYMS`，清空重来，仅收录新类型的中英文漂移（如 `概念`→`concept`、`方法`→`method`）。
  - 新增关系漂移归一化表（空表起步，发现漂移再加）。
  - `file_type` → `entity_type` 全量更名；缺失 entity_type 默认 `concept` 的兜底保留；非法值交由 validate 报错（不再静默归一为 concept）。
  - 删除 `_GENERIC_RELATIONS` 中已废弃值（无候选则置空并简化"通用边折叠"逻辑）、calls/imports/re_exports 幻影边与自环守卫、超边构建逻辑。
- `dedup.py`：`_FILE_ANCHORED_NONCODE` 去掉 `rationale`（剩 `document`）；`file_type` 比较更名为 `entity_type`；`code` 相关分支删除。
- `analyze.py` / `report.py`：删除 `semantically_similar_to` 特殊处理（surprising connection）、`rationale` 排除项、`code` 节点计数、`imports/imports_from` 等旧关系引用。
- `export.py`：`file_type` → `entity_type` 渲染；节点卡片在 definition 非空时显示。
- `serve.py`：不动。
- 超边机制整条流水线删除（build/merge/export/validate 及 graph.json 中 `hyperedges` 结构）。

### 3. 测试更新

- `tests/fixtures/extraction_docs.json`：用新分类法重写（9 类型、11 中文关系、definition 字段）。
- `tests/write_smoke_chunks.py`、`tests/test_smoke.py`：同步新 schema。
- `tests/ported/`：`test_build_ported.py` 中 file_type 归一化/同义词用例改写为 entity_type 新语义；fixture 计数类断言更新；`test_build_merge_ported.py`、`test_query_*_ported.py`、`test_serve_*_ported.py` 中引用旧关系名的测试数据替换。
- `tests/test_equivalence.py`：删除；`tests/ported/README.md` 加注说明分类法已独立演进。

### 4. 验证

- `uv run --with pytest pytest tests/` 全绿。
- 用 `samples/` 或 `corpus/` 小子集跑一次真实抽取 → build → export-html 端到端冒烟，确认 graph.json 中新 schema 落地、HTML 展示 definition。

### 5. 文档

- `docs/CONTEXT.md`、`docs/adr/0001-extraction-taxonomy-replacement.md`：已写入。
- `skills/kg/SKILL.md`：作为规范主体在实施时重写。

## 非目标（Out of scope）

- 存量图谱数据迁移或重跑（改造完成后另行触发）。
- confidence 体系改动。
- serve.py 查询引擎改动。
- GRAPH_REPORT.md 内容改动。
