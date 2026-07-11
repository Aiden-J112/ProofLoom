# ProofLoom v0.1 产品需求文档

> 工作名称：ProofLoom  
> 产品定位：Evidence-grounded AI learning workspace  
> 文档状态：可交付执行  
> 目标版本：v0.1

## 0. 执行摘要

ProofLoom 是一个本地优先、证据驱动的 AI 学习工作台。用户导入可信 Markdown 资料，系统将资料切分为可定位的来源片段，使用 LLM 生成候选实体与候选断言，再通过程序校验和人工审核形成可信图谱。

v0.1 完整交付 Build 空间，并交付轻量 Explore 空间：用户能从 Markdown 走到已审核 JSON 图，并从任意图关系回到原文证据。问答、出题、评分和修订 Agent 是后续工作台能力，不属于 v0.1 发布门槛。

## 1. 问题陈述

### 1.1 当前问题与痛点

现有的 LLM 文档问答、摘要和知识图谱生成工具常常存在以下问题：

- 模型生成的节点和关系被直接当作事实，缺少权威边界；
- 结论难以稳定回到原文的文件、标题和片段；
- 人工纠错通常直接覆盖数据，无法衡量 LLM 的真实抽取质量；
- 别名、重复实体和职责相近概念容易导致图谱污染；
- 在线模型不可用或没有 API key 时，Demo 和本地开发容易中断；
- 大量节点和可视化本身无法证明图谱真的可信、可用。

### 1.2 影响对象与范围

首版面向 **Owner-Learner**：同一个人既整理并审核自己信任的资料，又使用结果学习。首版不引入教师、学生、管理员等多角色权限系统。

### 1.3 不解决的成本

如果不建立证据与审核层，后续问答、出题和评分都只能依赖未经治理的 LLM 输出。一旦底层知识关系错误，下游所有功能会一致且难以解释地出错，并且无法将人工审核沉淀为长期资产。

## 2. 解决方案

### 2.1 方案概述

ProofLoom 将知识构建分为两层：

1. **Assertion Ledger**：保存候选、已接受、已拒绝和已失效断言，以及证据、抽取元数据和审核历史；
2. **Query Graph**：只投影已接受且未失效的断言，优化遍历和可视化。

原始 Markdown 是权威来源；LLM 只能提交候选断言，不能直接创建权威知识。

### 2.2 工作台长期形态

```text
Build
导入资料 → 抽取候选知识 → 人工审核 → 形成可信图谱

Explore
查看图谱 → 关系证据回溯 → 可溯源问答 Agent

Learn
选择主题 → 出题 Agent → 作答 → 评分 Agent → 修订与复习
```

Agent 是工作台内的可组合能力，不是产品形态的全部定义。

### 2.3 v0.1 关键流程

```text
用户创建本地 Knowledge Project
  → 指定 Markdown 文件或目录
  → 程序按结构生成 Source Fragment
  → LLM API 或 fixture 生成 Candidate Assertion
  → 程序执行 Schema、实体词典和关系词表校验
  → 用户逐条接受、拒绝、替代或标记需领域复核
  → 追加 Review Event
  → 投影已接受断言为 JSON Query Graph
  → 用户查看图并从边返回原文片段
```

### 2.4 v0.1 范围

#### Build（完整交付）

- 创建与打开本地 Knowledge Project；
- 导入 UTF-8 Markdown 文件/目录；
- 按标题路径、自然段、完整列表和完整代码块切片；
- 维护封闭实体词典；
- 使用 OpenAI-compatible API 或 fixture 生成候选断言；
- 校验候选断言；
- 逐条人工审核；
- 保存追加式审核事件；
- 检测来源变更并使相关断言失效；
- 生成 JSON Query Graph。

#### Explore（轻量交付）

- 查看节点与关系图；
- 按节点类型或关系类型过滤；
- 点击关系查看 `assertion_id`、主要/辅助证据和原文定位；
- 展示已接受、已拒绝、已替代和待领域复核的基本统计。

### 2.5 非目标（Out of Scope）

- 正式图谱问答 Agent；
- 出题、评分和修订 Agent；
- 学习记录、掌握度模型和自适应复习；
- Neo4j 强制依赖；
- RDF/OWL 和通用本体编辑器；
- PDF、Word、网页抓取等非 Markdown 导入；
- 多用户、登录、角色和权限系统；
- 云端托管、协作审核和实时同步；
- 自动接受 LLM 生成的权威断言；
- 随开源仓库分发 Harness 教程原文或其派生内容。

## 3. 用户故事与验收标准

### US-01 创建本地知识项目

**作为** Owner-Learner，**我希望**创建一个本地 Knowledge Project，**从而**将源文档、断言和图数据隔离在一个可携带的目录中。

最小验收：

- 可选择或创建项目目录；
- 目录中包含 Schema 版本和项目元数据；
- 重启程序后可重新打开项目；
- 不要求用户手工编辑 JSON。

### US-02 导入并结构化切分 Markdown

**作为** Owner-Learner，**我希望**导入 Markdown 资料，**从而**获得可稳定定位的证据片段。

最小验收：

- 保留文件路径、标题路径、片段顺序和原文；
- 列表和代码块不被机械拦腰切断；
- 每个片段包含稳定 ID 和规范化内容哈希；
- 未变更的文档重复扫描得到相同片段 ID。

### US-03 维护受控实体词典

**作为** Owner-Learner，**我希望**审核实体的规范名称、类型和别名，**从而**避免重复节点和错误合并。

最小验收：

- 实体类型限定为 `Component | Artifact | Pattern | Concept`；
- 实体使用不随显示名变化的稳定 ID；
- 未识别名称只生成候选实体，不自动成为正式节点；
- 同一别名不能静默绑定到多个已接受实体。

### US-04 使用 API 或 fixture 抽取候选断言

**作为** Owner-Learner，**我希望**选择在线模型或离线 fixture，**从而**在不同环境下都能运行知识构建流程。

最小验收：

- `OpenAICompatibleExtractor` 通过环境变量读取 endpoint、model 和 API key；
- 默认可配置 OpenAI，并可切换 DeepSeek 等兼容接口；
- `FixtureExtractor` 不发起网络请求；
- 两种实现产出相同候选断言合同；
- 候选记录 `provider`、`model`、`prompt_version`、`schema_version`、`generated_at` 和 `mode`。

### US-05 校验候选断言

**作为** Owner-Learner，**我希望**系统在审核前自动拦截结构错误，**从而**把人工注意力留给语义判断。

最小验收：

- 候选符合 JSON Schema；
- 主体和客体 ID 存在于已接受实体词典；
- 关系仅限 `COMPOSED_OF | PROMPTS | CALLS_TOOL | PRODUCES | VERIFIES | BLOCKS`；
- 主客体类型符合关系合同；
- 每条断言必须有一个主要证据片段，可有零到多个辅助片段；
- 校验失败返回可理解的字段级原因，不写入查询图。

### US-06 人工审核候选断言

**作为** Owner-Learner，**我希望**并排查看候选关系与原文证据，**从而**不编辑 JSON 也能做知识决策。

最小验收：

- 审核页显示主体、关系、客体、主要/辅助证据、文件和标题路径；
- 操作包括 `accept`、`reject`、`replace` 和 `needs_domain_review`；
- 审核操作产生追加式 Review Event，不删除原始 LLM 输出；
- 改变主体、关系或客体时，原断言被拒绝，系统创建新断言并记录 `replaces_assertion_id`；
- `needs_domain_review` 不进入查询图；
- v0.1 审核人标识可固定为 `local-user`。

### US-07 投影 JSON 查询图

**作为** Owner-Learner，**我希望**从断言账本生成简洁图数据，**从而**无需图数据库也能遍历和展示知识关系。

最小验收：

- 仅投影已接受、证据有效且未失效断言；
- 节点来自已接受实体词典；
- 每条边包含 `assertion_id`；
- 重复投影不产生重复边；
- 不投影已拒绝、已替代、已失效或待领域复核断言。

### US-08 浏览图并回溯证据

**作为** Owner-Learner，**我希望**点击任意图关系查看原文，**从而**判断该关系为什么可信。

最小验收：

- 图可区分节点类型和关系类型；
- 点击边后可通过 `assertion_id` 打开证据面板；
- 证据面板显示原文、文件、标题路径和该断言的审核状态；
- 图不以节点数量作为成功标准，而以证据回溯完整性作为核心标准。

### US-09 检测源文档变化

**作为** Owner-Learner，**我希望**在原文变更后自动撤下可能过期的关系，**从而**避免查询图静默失真。

最小验收：

- 重新扫描比较 `content_hash`；
- 变更片段标记为 `changed`；
- 引用它的已接受断言标记为 `stale`；
- `stale` 断言从查询图撤下，但原记录和审核历史保留；
- 新片段重新抽取后需要人工复核。

## 4. 实现决策

### 4.1 高层架构

```text
Local Web UI
├── Project / Import
├── Entity Dictionary
├── Assertion Review
└── Graph Explorer

Application Core
├── Source Parser
├── Assertion Extractor
├── Assertion Validator
├── Assertion Ledger
├── Review Service
└── Graph Projector

Adapters
├── OpenAI-compatible HTTP
├── Fixture
└── JSON Graph
```

### 4.2 模块边界

| 模块 | 输入 | 输出 | 禁止职责 |
| --- | --- | --- | --- |
| Source Parser | Markdown | Source Fragment | 不做领域关系判断 |
| Entity Dictionary | 已审核实体/候选名称 | 规范实体映射 | 不自动接受新实体 |
| Assertion Extractor | 片段 + 词典 + 关系词表 | Candidate Assertion | 不写入 Query Graph |
| Assertion Validator | 候选 + Schema/词典/关系合同 | 校验结果 | 不判断领域真假 |
| Assertion Ledger | 断言 + Review Event | 当前状态与完整历史 | 不为查询便利丢弃历史 |
| Review Service | 审核命令 | Review Event/替代断言 | 不静默改写原候选 |
| Graph Projector | 当前有效的已接受断言 | JSON Query Graph | 不成为权威事实账本 |

### 4.3 核心数据合同

#### Entity

```json
{
  "id": "component.verifier",
  "canonical_name": "Verifier",
  "type": "Component",
  "aliases": ["output verifier"],
  "status": "accepted",
  "schema_version": "1"
}
```

#### Source Fragment

```json
{
  "id": "src_05-08_verifier__responsibility__p002",
  "source_file": "05-08-verifier.md",
  "heading_path": ["Verifier", "Responsibilities"],
  "ordinal": 2,
  "kind": "paragraph",
  "content": "...",
  "content_hash": "sha256:...",
  "schema_version": "1"
}
```

#### Candidate Assertion

```json
{
  "id": "ast_01",
  "subject_id": "component.verifier",
  "predicate": "VERIFIES",
  "object_id": "artifact.output",
  "primary_evidence_id": "src_05-08_verifier__responsibility__p002",
  "supporting_evidence_ids": [],
  "status": "candidate",
  "extraction": {
    "provider": "openai",
    "model": "configured-model",
    "prompt_version": "1",
    "schema_version": "1",
    "generated_at": "RFC3339 timestamp",
    "mode": "api"
  }
}
```

#### Review Event

```json
{
  "id": "rev_01",
  "assertion_id": "ast_01",
  "action": "accept",
  "reviewer": "local-user",
  "reviewed_at": "RFC3339 timestamp",
  "replacement_assertion_id": null,
  "note": null,
  "schema_version": "1"
}
```

#### Query Graph

```json
{
  "schema_version": "1",
  "nodes": [
    {"id": "component.verifier", "type": "Component", "name": "Verifier"}
  ],
  "edges": [
    {
      "source": "component.verifier",
      "type": "VERIFIES",
      "target": "artifact.output",
      "assertion_id": "ast_01"
    }
  ]
}
```

JSON Schema 文件是上述示例的唯一机器可执行定义；实现时不允许只依据文档示例硬编码。

### 4.4 建议技术栈

- Python 3.12：核心流程、Markdown 解析、Schema 校验、CLI 与本地后端；
- JSON Schema：数据合同；
- JSON/JSONL：实体词典、断言和追加式 Review Event；
- 本地 Web UI：导入、审核与图探索；
- OpenAI-compatible HTTP：OpenAI 默认接入，DeepSeek 等外部模型可配；
- Fixture Adapter：离线开发、测试与现场演示；
- JSON Graph：v0.1 查询图；
- Neo4j：后续可选投影适配器，不属于 v0.1 强依赖。

执行模型可在不违反模块边界和数据合同的前提下，选择具体 Web 框架与图可视化库。

### 4.5 关键权衡及理由

| 决策 | 放弃的便利 | 获得的价值 |
| --- | --- | --- |
| 原文是权威来源 | 不能直接信任 LLM 输出 | 可追溯、可审核 |
| 断言账本与查询图分层 | 多一次投影 | 治理历史与图查询各自清晰 |
| 封闭实体词典 | 降低自动发现率 | 减少重复节点和错误合并 |
| 追加式审核事件 | 比直接改 JSON 多一层结构 | 可评估模型、可审计、可回放 |
| 结构感知切片 | 解析器比固定 Token 切块更复杂 | 证据对人可读且语义完整 |
| API + fixture 双入口 | 需要维护统一合同 | 无 API key 也能开发、测试和演示 |
| v0.1 先用 JSON Graph | 暂不使用 Neo4j 完整能力 | 降低环境成本，快速验证知识治理闭环 |

### 4.6 开源与资料边界

- 开源仓库发布代码、Schema、提示词、原创或合成 fixture；
- 不发布 Harness 教程原文、教程片段或可替代原文的派生内容；
- 内部 Demo 从用户本地路径读取 Harness 教程；
- 公开示例使用单独编写的小型合成教程；
- `.env`、API key、用户导入原文和本地项目数据必须默认排除在 Git 之外。

### 4.7 风险与缓解

| 风险 | 缓解策略 |
| --- | --- |
| LLM 生成错误关系 | 候选状态、Schema 校验、v0.1 全量人工审核 |
| Verifier/Safety 等职责混淆 | 封闭词典、对照证据、替代断言保留原错误 |
| 原文更新导致图过期 | 内容哈希、`stale` 状态、移出查询图、重新审核 |
| 审核人无法判断 | `needs_domain_review`，不强迫二元决策 |
| 在线 API 不可用 | FixtureExtractor |
| 现场演示受网络影响 | 使用标注生成来源的 fixture |
| 开源误提交教程或秘钥 | `.gitignore`、合成示例、发布前内容检查 |
| 范围失控 | v0.1 仅交付 Build + 轻量 Explore，其余写入路线图 |

## 5. 质量门槛

### 5.1 首个内部垂直切片

仅使用本地：

```text
05-08-verifier.md
05-09-safety.md
```

必须满足：

- 两篇文档重复解析产生稳定片段 ID；
- 所有候选通过或明确失败于机器校验；
- 通过校验的候选全部经人工审核；
- 每条已接受断言至少有一个主要证据片段；
- 查询图只包含已接受且有效断言；
- 任意图边可通过 `assertion_id` 回到断言与原文；
- 无 API key 时可用 fixture 跑完整个闭环；
- 可统计候选接受数、拒绝数、替代数和待领域复核数。

### 5.2 开源发布门槛

- 从空目录按 README 可完成本地安装与启动；
- 合成示例可在无 API key 情况下完整运行；
- 自动测试覆盖切片稳定性、Schema 校验、审核状态投影和来源失效；
- 仓库不包含 Harness 教程原文、API key 或用户本地项目数据；
- 错误信息能定位到文件、片段或合同字段；
- 核心命令与界面流程有最小使用说明；
- 项目工作名 ProofLoom 在正式发布前完成商标、仓库名和软件包名复核。

## 6. 执行顺序

执行模型应以下列顺序为主线，每一步先通过本步验收，再扩展下游：

1. 脚手架、项目目录合同与合成示例；
2. JSON Schema 和合同测试；
3. Markdown 结构感知切片与稳定 ID/哈希；
4. 封闭实体词典和候选实体逻辑；
5. `FixtureExtractor` 和候选断言校验；
6. 断言账本、Review Event 和当前状态折叠；
7. JSON Graph Projector；
8. 来源变更、`stale` 和撤下投影；
9. 本地审核界面；
10. 图探索与证据面板；
11. `OpenAICompatibleExtractor`；
12. 内部 Verifier + Safety 数据跑通与全量审核；
13. README、开源内容检查和 v0.1 发布准备。

## 7. 执行模型分工建议

### Luna：机械性与内容性任务

- 按已定合同生成合成 Markdown 示例；
- 按已定 Schema 生成 fixture 候选数据；
- 整理 README 初稿、字段表和示例说明；
- 执行格式检查、测试数据扩充和重复性检查；
- 不自行改变架构边界、核心术语和断言治理语义。

### Terra：常规代码实现

- 脚手架、数据模型、解析器、校验器、账本和投影器；
- 本地 Web 审核与图探索界面；
- API/fixture 适配器、错误处理、自动测试和使用文档；
- 严格按模块边界和质量门槛实现；
- 发现 PRD 矛盾时停止扩展，记录最小复现和需要的架构决策。

### 架构模型：决策与复核

- 处理 PRD 未覆盖的不可逆决策；
- 处理断言身份、审核语义、投影一致性和模块边界问题；
- 复核会导致数据不可兼容或重大返工的变更；
- 不承担已经由 PRD 完整规定的机械实现任务。

## 8. 待确认事项

1. **项目名称**：`ProofLoom` 为当前推荐工作名；正式重命仓库前由项目所有者确认。
2. **开源许可证**：代码库具体 license 尚未选定；建议在首次公开发布前确认。
3. **具体 Web 技术**：PRD 只要求本地 Web UI；执行模型可根据仓库脚手架和最小依赖原则提案，但不得改变核心模块边界。
4. **正式外部品牌使用**：需在对外宣传前进行商标、GitHub 仓库/组织名和软件包名检查。

## 9. 相关决策与文档

- [`CONTEXT.md`](../CONTEXT.md)：领域语言；
- [`ADR-0001`](./adr/0001-separate-assertion-ledger-from-query-graph.md)：断言账本与查询图分层；
- [`ADR-0002`](./adr/0002-product-is-an-ai-learning-workspace.md)：AI 学习工作台定位；
- [`ADR-0003`](./adr/0003-do-not-distribute-harness-source-material.md)：不分发 Harness 教程原文；
- [`Harness 学习 Agent 知识图谱 Demo 方案`](./Harness%20学习%20Agent%20知识图谱%20Demo%20方案.md)：原始 Demo 方案与逐步收敛的技术设计；
- [`技术路线与项目价值`](./Harness%20学习%20Agent%20知识图谱技术路线与项目价值.md)：领导汇报与对外叙事材料。
