# Scientific RAGAgent

**[English](README.md) | 简体中文**

本地科研文献助手，提供持久化的 RAG/Research 对话、可查看的会话记忆，以及
Tauri 桌面入口。它保留了现有的基于证据的知识库和 Supervisor 工作流。
项目采用 MIT 许可证；论文和模型权重分别受各自许可证约束。
不依赖 langgraph-supervisor，不声称未经验证的基准测试成绩。

会话与桌面升级已发布到
[`feature/desktop-conversations`](https://github.com/chouytong/RAGAgent/tree/feature/desktop-conversations)，
建立在现有 `phase-6-evaluation-deployment` RAG/Research 基线上。
在 PR 完成审查和合并之前，请使用这个审查分支。

```bash
git clone --branch feature/desktop-conversations https://github.com/chouytong/RAGAgent.git
cd RAGAgent
# First checkout only; preserve an existing runtime .env.
cp .env.example .env
docker compose up --build
```

打开 [Web 备用入口](http://localhost:8080) 和 [API 文档](http://localhost:8000/docs)。
如需独立应用窗口，先用 Compose 启动本地后端，再执行
`npm --prefix frontend ci` 和 `npm --prefix frontend run desktop:dev`。
桌面壳只连接 `http://127.0.0.1:8000`，不会打包或启动 Python、PostgreSQL、Redis。
Rust 和各平台的 WebView 前置依赖列在
[部署文档](docs/deployment.md#desktop-ui-with-local-backend) 中。
需要 Docker Compose v2 和 Git；建议至少 8 GB 内存、20 GB 可用磁盘空间。
系统可以在未配置 API key 时启动；推理需要配置聊天服务商或本地模型。
本地解析、嵌入和重排模型的权重会在首次使用时下载。
`/api/health` 报告进程是否存活；`/api/ready` 检查数据库、Redis 和队列 worker，
不代表模型或服务商已经具备推理条件。

RAG/Research 会话使用 `interactive` 队列，PDF/arXiv 使用 `ingestion`，评测使用
`evaluation`。Compose 分别启动三个独立 worker；`/api/ready` 检查交互服务所需
数据库、Redis 和交互 worker，`/api/queues` 分别报告三个队列的可用性与待处理数。
扩容及已有作业迁移见[部署文档](docs/deployment.md#dedicated-workload-queues)。

## 架构

```mermaid
flowchart LR
 Desktop[Tauri window] --> React[React + TypeScript Chat UI]
 Web[Web fallback] --> React
 React --> Bridge[Desktop: scoped Rust bridge; fixed loopback]
 Bridge --> API[FastAPI Conversation API + existing APIs]
 React -->|Web: same-origin proxy| API
 API --> Jobs[Durable dispatch → Redis / RQ worker]
 Jobs --> Context[Bounded context + rolling summary + explicit memory]
 Context --> Rewrite[Standalone query; context is not evidence]
 Jobs --> Parse[Docling + section-aware chunks]
 Parse --> DB[(PostgreSQL + pgvector + FTS)]
 Rewrite --> RAG[Typed RAG StateGraph]
 Rewrite --> MA[Supervisor StateGraph]
 Jobs -->|Legacy standalone Run| RAG
 Jobs -->|Legacy standalone Run| MA
 RAG --> Retrieval[Filters → dense + FTS → RRF → cross encoder]
 MA --> Retrieval
 Retrieval --> DB
 RAG --> Verify[Evidence gate + claim verification]
 MA --> Verify
 RAG --> Models[LiteLLM provider abstraction]
 MA --> Models
 Jobs --> Events[Durable execution events]
 Events --> API
```

技术栈包括 Python 3.11+、Pydantic v2、SQLAlchemy 2、Alembic、当前版本的
LangGraph、LiteLLM、Docling、PostgreSQL/pgvector、Redis/RQ，前端使用
React/TypeScript/Vite；依赖由 uv/npm 锁文件固定，配套 Docker Compose、
pytest/Ruff/mypy 和 GitHub Actions。

## 对话与本地记忆

RAG Chat 和 Research Chat 分别保存会话历史，支持自动标题、重命名、删除、
有序消息、Markdown/代码块、引用和可折叠执行详情。
Research 保留 Supervisor Plan、Agent 执行轨迹、Reviewer Result 和局限说明。
重新加载 Web UI 或重启桌面应用后，会从 PostgreSQL 读取会话与消息；
浏览器 `localStorage` 不是会话存储。URL 片段用于标识当前选中的会话。

界面先读取最近 50 条轻量消息，需要时再加载更早历史。SSE 驱动实时进度，
重连或恢复时增量读取新消息并核对执行中的占位消息。Run、执行轨迹和证据详情
在打开时读取。被重试替代的旧回答保留为审计记录，并退出后续问题的上下文。

每一轮会同时创建消息、现有 Run 和持久化调度意图。
worker 将可发布的助手回答与终态 Run/事件一起保存；SSE 重连会重放已保存的执行事件。
重复提交通过客户端 UUID 识别；重试会创建新的 Run，并保留前一次失败供查看。
同一会话一次只能执行一轮，不同会话之间独立。
取消操作会撤销结果发布权限，并尽力停止队列任务；已发出的服务商调用仍可能完成并产生费用。

追问经过 Context Builder：当前问题、有界的最近消息、确定性的滚动抽取式摘要，
以及用户明确添加的记忆，共同将代词或已命名的候选对象解析成独立问题。
原始问题和上下文化后的问题可在执行详情中查看。
旧内容的摘录可能丢失信息；指代不明确时会明确失败，而不是猜测。
`CONVERSATION_CONTEXT_TOKEN_BUDGET=8192` 使用保守的 UTF-8 字节估算来约束
上下文化输入，并非服务商 tokenizer 的计数，也不是整个工作流的预算。

**记忆 ≠ 证据。** 消息、摘要和记忆只用于理解意图与指导检索。
两个独立的 Graph 都会重新检索本轮的原文证据，并保留现有的证据准入、
论断验证和引用验证。例如，历史对话中的“数据集 A 有 500 名参与者”，
不能直接支撑下一轮科研回答。
带元数据过滤条件的结构化约束会与当前过滤条件取交集；冲突时会明确失败。
只有自然语言偏好文本，并不构成强制 SQL 过滤条件。
可在会话的 Memory 面板中添加、查看或删除记忆；
系统不会自动建立隐藏用户画像，也不使用外部记忆服务。

Clear Conversation Memory 删除摘要与记忆记录，并保留消息；
保留下来的历史可能在后续对话中重新生成摘要。
Clear Conversation 删除消息、关联的 Run/事件/调度记录和记忆，但保留空会话。
Delete Conversation 删除上述记录以及会话本身。
知识库论文和无关的评测产物会保留。详见
[会话与记忆生命周期](docs/conversation-memory.md)。

## 本地数据与远程推理

使用默认本地 Compose 服务时，PDF/解析文件、向量索引、会话历史、摘要、
结构化记忆和执行历史保存在本地 PostgreSQL 或命名文件系统卷中。
系统没有云端会话数据库或 SaaS 记忆存储。
备份、下载的评测产物和桌面缓存文档属于独立副本，需要分别删除。

远程聊天服务商会收到必要的问题、用于改写的会话上下文，以及用于分析/审查的检索片段；
托管嵌入服务商会收到待索引文本和检索问题。
远程评测裁判会收到回答与证据载荷。
因此，本地持久化**不等于**推理期间所有数据都留在本机。
如需本地推理，请配置本地聊天、嵌入、重排和解析资源；
除非已有缓存，首次模型下载仍需要网络。
API key 仅放在运行时环境变量或 secrets 中。
会话字段会拒绝可识别的凭据模式；不要将密钥粘贴到自由文本中，
因为模式检测无法识别每一种可能的凭据。

## 论文导入与知识库

可上传 PDF，并可选提供标题、作者、年份、会议/期刊；也可导入开放的 arXiv ID。
系统保留原始 PDF、结构化章节、文本/表格/图注、页码、chunk 和嵌入向量。
可在 Knowledge Base 中查看索引状态和元数据。
切分限制在同一章节和元素类型内，目标 token 数和重叠范围可配置。
小数与科学计数法数值保持完整；可识别的 Markdown 表格按行边界切分，
表头/图表说明保留各自来源，而公式与结构未知的表格保持完整，即使超过目标大小。
新导入的 arXiv 论文会在下载前固定官方带版本的 ID。
来源状态初始为 `unknown`；手动标记为撤回或撤稿的论文会被排除在检索之外。
稠密检索与词法检索使用相同的全部元数据过滤条件。
可通过 API 标注 chunk 实体，以支持数据集、方法和指标过滤。

```bash
curl -F 'file=@paper.pdf' -F 'title=Paper title' -F 'authors=Alice;Bob' \
  http://localhost:8000/api/papers/upload
curl -H 'Content-Type: application/json' -d '{"arxiv_id":"2408.09869"}' \
  http://localhost:8000/api/papers/arxiv
```

## RAG 与科研分析

RAG 支持事实查询、方法比较和带约束的检索。
每一个发布的事实论断都具备经过验证的结构化 Evidence ID、精确支持文本，
以及论文、章节、页码和 chunk 定位。
模型自行编写的引用标记会被拒绝；只有确定性的格式化过程生成引用。
Reviewer 还必须确认每一个附带的论断/证据对都得到支持；不相关的引用需要修订。
多查询检索针对原始问题或当前子任务进行重排，在查询扩展期间保留这一目标。
配置的重排阈值会限制两个工作流提供给生成与验证环节的证据。
缺少支持时，会先进行有界扩展，再明确拒答。
检索分数是启发式分值，不是经过校准的模型置信概率。

```mermaid
sequenceDiagram
 participant U as User
 participant A as API / Worker
 participant G as RAG StateGraph
 participant D as PostgreSQL
 participant V as Reviewer
 U->>A: query + metadata filters
 A-->>U: job ID + SSE
 A->>G: structured QueryPlan
 G->>D: dense + lexical search
 D-->>G: candidates
 G->>G: RRF, cross encoder, evidence gate
 G->>V: claims + cited exact spans
 V-->>G: support and completeness verdicts
 alt sufficient verified evidence
 G-->>A: cited answer
 else evidence insufficient
 G->>D: bounded expanded retrieval
 G-->>A: verified answer or explicit refusal
 end
 A-->>U: execution events + final status
```

Research 生成结构化计划和子任务，调用 Retriever 和 Analysis，
然后执行确定性的 Report Synthesis 与 Reviewer。
NEED_MORE_EVIDENCE 返回检索；NEED_REVISION 返回分析。
检索、修订和总迭代预算防止无限循环。草稿始终明确标注。
重试保留已接受证据的有界并集。
重新规划任务时，只有完整任务内容未变的任务才会保留完成状态，即使复用了相同 ID。

```mermaid
sequenceDiagram
 participant S as Supervisor
 participant R as Retriever
 participant A as Analysis
 participant N as Report Synthesis
 participant V as Reviewer
 S->>S: plan + subtasks + budgets
 S->>R: scoped tasks and filters
 R-->>S: Evidence IDs
 S->>A: evidence pool
 A->>N: structured claims/comparisons
 N->>V: citation-linked draft
 V-->>S: PASS / NEED_MORE_EVIDENCE / NEED_REVISION
 alt PASS
 S-->>S: release report
 else NEED_MORE_EVIDENCE
 S->>R: bounded expansion/retrieval
 else NEED_REVISION
 S->>A: bounded revision
 end
```

```bash
curl -H 'Content-Type: application/json' -d '{"mode":"rag"}' \
  http://localhost:8000/api/conversations
# Replace CONVERSATION_ID and use a new client UUID for each new logical turn.
curl -H 'Content-Type: application/json' \
  -d '{"content":"Which datasets are used?","client_request_id":"00000000-0000-4000-8000-000000000001"}' \
  http://localhost:8000/api/conversations/CONVERSATION_ID/messages
# Existing single-run API clients remain supported:
curl -H 'Content-Type: application/json' \
  -d '{"query":"How do the papers compare training methods?","filters":{"year_start":2023}}' \
  http://localhost:8000/api/rag/query
curl -H 'Content-Type: application/json' \
  -d '{"research_question":"Compare methods, datasets and metrics for scientific retrieval."}' \
  http://localhost:8000/api/research
curl -N http://localhost:8000/api/research/RUN_ID/events
```

## 服务商与本地模型

编辑 `.env` 中的运行时密钥、`config/agents.yaml`，或通过 Settings UI 设置非密钥映射。
每个 Agent 都可通过 LiteLLM 独立使用 OpenAI、Anthropic、DeepSeek、Ollama，
或兼容 OpenAI 的本地服务。嵌入与重排有各自的配置。
YAML 只在模型字段中展开不含密钥的 `*_MODEL` 变量；
API 映射写入会拒绝未解析的模板。
本地 Host 校验和浏览器同源写入检查保护本地 API。
服务商连通性测试不会返回密钥。
默认聊天映射使用带日期的模型标识；如果服务商返回实际模型身份，也会记录。
托管嵌入支持 `openai`、`cohere`、`cohere_chat` 和 `voyage` 前缀；
兼容服务使用 `openai/<model>`，并显式设置 base 地址。
嵌入端点身份会同时固定用于索引和 SDK 调用。
托管服务的 `EMBEDDING_REVISION` 是运维人员指定的索引标签，并不固定服务端模型权重。
本地 Hub 适配器首次使用时，会将请求的 revision 解析为不可变 SHA；
要复现后续运行，请显式设置该 SHA。
`local:v2` 指纹要求重新嵌入旧的本地索引。
`python -m ragagent.reindex --all-indexed` 会按论文原子更新向量，同时保留 chunk/引用 ID。
全部本地/托管嵌入示例、模型缓存、迁移、网络要求和故障排查见
[部署文档](docs/deployment.md)。

## 评测与开发

Evaluation 页面接受带标注的数据集并运行检索消融评测。
API 和页面也支持 RAG、多 Agent 和会话评测。
会话评测使用生产环境的 Context Builder 和独立 Graph 流水线，
衡量上下文解析、证据支撑、记忆隔离和长摘要案例；不会用记忆替代检索。
每次运行写出 results.json/results.md，记录 Git commit、数据集哈希、时间戳、
执行配置，以及实际的逐查询指标与延迟。
每个案例都会保存结果检查点；部分完成或失败的终态运行仍保留可下载产物，
在数据集、来源、配置和语料身份匹配时，可通过 `resume_run_id` 显式续跑。
续跑费用分别保留当前尝试和先前尝试的记录。
生成评测还会保留最终输出、证据和原始裁判响应，以便检查。
语义裁判结果明确标记为 MODEL_BASED；未知的服务商收费会使总费用不完整。
工作流用量包含托管嵌入；进行中或失败的付费调用会记录为费用未知，而不是假定免费。
worker Run 持久化付费调用用量更新；评测产物也会在案例之间保存这些更新的检查点。
同步检索/服务商测试只返回当前请求用量，重建索引 CLI 打印累计用量；
两者都没有持久化的 Run 计费台账。

```bash
python scripts/annotation_template.py my-annotations.json --count 100
uv sync
uv run ruff format . && uv run ruff check . && uv run mypy src
uv run pytest -q
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run check
npm --prefix frontend run build
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
npm --prefix frontend run test:transport
npm --prefix frontend run desktop:check
npm --prefix frontend run desktop:test
npm --prefix frontend run desktop:build
```

仓库中**没有 100 条人工标注样例**。未标注的 100 行模板必须由人工填写。
可选合成语料/数据明确标记为
**DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED**
（仅供演示 / 不是基准测试 / 未经人工标注）。不声称任何提升数值。
指标定义和局限见 [评测文档](docs/evaluation.md)。
核心测试不需要付费 API 或模型下载；PostgreSQL/pgvector 集成测试在 CI 中运行。
`scripts/smoke.py` 通过前端代理测试全新的空 Compose 部署，
包括真正缺少密钥时的 worker 失败与终态 SSE；它不测试成功推理。
[阶段日志](docs/stage-log.md) 记录实际验证结果。

## 文档与局限

[实现/验收基线](docs/MASTER_SPEC.md) ·
[产品升级交付说明](docs/product-upgrade-report.md) ·
[修复决策](docs/adr/README.md) ·
[参考项目/许可证审查](docs/reference-review.md) · [架构](docs/architecture.md) ·
[数据模型](docs/data-model.md) · [会话与记忆](docs/conversation-memory.md) · [检索](docs/retrieval.md) ·
[Agent](docs/agents.md) · [API](docs/api.md) · [部署](docs/deployment.md) ·
[贡献者规则](AGENTS.md)。

V1 是面向可信本地环境的单用户应用。
英文 PostgreSQL FTS、精确向量检索和词法 token 计数，是首个版本的有意选择。
Docling 的公式、OCR 和表格保真度取决于具体文档与模型。
语义验证可能出错，科研结论需要人工复核。
服务商能力、延迟和模型费用各不相同。
Graph 检查点自动续跑、公共/多租户安全与 ANN 调优仍需要进一步工作。
模型和 PDF 不随仓库分发。
受限云端网络或模型访问会明确列为验证局限，绝不会用模拟推理掩盖。
桌面打包、签名和 GUI 行为取决于平台；
使用脚本化服务商的测试验证的是接口约定，而不是真实模型的多轮对话质量。
当前 checkout 的实际升级检查和剩余验证缺口记录在阶段日志中，
不会因为存在构建命令就推断检查已经通过。

基线和 ADR 是工程修复期间编写的文档，
不是恢复的历史规格或此前的验收证据。
阶段日志区分了实际完成的检查，
与仍未验证的真实 PDF、模型、服务商和基准测试验证。
