# Scientific RAGAgent 工程审查与整改报告

日期：2026-10-07（UTC）。审查基线：`ba04246c82f556753c36980e76cf64360ca533ce`，冻结副本为 `/workspace/RAGAgent-review-baseline`。整改分支：`fix/engineering-hardening`。

本报告是阶段记录。Phase 1–4 的实现和已执行检查如下；**Phase 5–8 均为 PENDING**。没有以 scripted provider 测试代替真实模型效果，没有把计划中的 Windows 构建、安装或签名写成已完成。A 节行号指审查基线；C 节符号指整改分支。

## A. Initial Findings

现有架构是共享 React Web/Tauri 客户端、FastAPI、PostgreSQL/pgvector、Redis/RQ。Conversation 协调层在独立的 RAG/Research Graph 上方，负责历史、摘要、显式 Memory 和查询改写；两图仍从本轮检索原文生成、验证并发布引用。Durable dispatch、终态事务、SSE 回放及 late-worker guard 已存在。

| 项目 | 核验结论 | 基线证据与触发行为 | 最小整改方向 |
| --- | --- | --- | --- |
| comparison 以论文数量判断支持 | **Confirmed** | `src/ragagent/retrieval/evidence.py:evidence_gate:200` 对 comparison 要求至少两篇论文；`graphs/rag.py:after_retrieve:64` 在分析前阻断。同论文同时包含 A/B 实验，即使 citation validation valid，实际复现仍为 PARTIAL。 | pre-gate 检查可用原文；post-review 检查不同实体及各自的语义支持。 |
| Message List 默认包含大型 Run result | **Confirmed** | `api/conversations.py:message_response:70` 每条消息读取 Run，`domain/conversation.py:MessageResponse:150` 嵌入 RunSnapshot；`list_messages:224` 返回同结构。真实 PG 性能 fixture 证明载荷和 SQL 查询增长，见 D。 | 轻量消息列表；Run/Evidence 详情按需读取。 |
| SSE 与全量历史轮询重复 | **Confirmed** | `frontend/src/Tasks.tsx:refreshMessages:327` 从 offset=0 遍历全部消息；`useEffect:393` 活跃 Run 每 4 秒重复。SSE 同时存在于该文件第 68 行。 | SSE 为主，增量 ordinal 对账、向上历史分页、失败恢复时才轮询。 |
| Retry 缺少有效回答/attempt lineage | **Confirmed** | `api/conversations.py:retry_message:293` 保留旧消息并以 metadata 记录 retry；`conversations/context.py:ContextBuilder.build:260` 同时接受 completed/insufficient_evidence，未排除被成功重试替代的旧拒答。旧拒答也可能留在 summary。failed/cancelled 已被排除，不能把它们泛称为当前漏洞。 | 明确 retry lineage、effective response，保留审计但从上下文/摘要排除旧 attempt。 |
| 所有任务共用一个队列 | **Confirmed** | `api/queue.py:RQQueue:47` 固定 research；`worker.py:main:425` 只有该队列；`compose.yaml:worker:57` 一个 worker。长 Evaluation/ingestion 可占用交互 worker；当前未量化等待时长。 | interactive/ingestion/evaluation 独立路由与 worker。 |
| 已独立的问题仍调用 contextualizer | **Confirmed** | `conversations/context.py:contextualize:465` 仅无 context 时跳过；已有任意历史/Memory 时第 483 行调用模型。 | 保守的 standalone/context-dependent gate，保留歧义失败路径。 |
| Memory 未做相关性 Top-K 选择 | **Confirmed** | `conversations/service.py:prepare_context:62` 读取全部 Memory；`context.py:ContextBuilder.build:289` 全部进入 payload，然后裁剪。存储数量增加会挤压上下文。 | 区分 Stored/Selected Memory；相关性选择和独立预算，硬约束不得被选择器静默丢弃。 |
| context budget 不存在 | **Not Confirmed** | `context.py:estimated_context_tokens:68` 与 `ContextBuilder.build:312` 已限制 UTF-8 byte 估算的改写输入；`domain/conversation_context.py:ContextConfig:10` 有配置。 | 保留预算；补长对话实测并明确它不是 tokenizer 精确计数或整个 Graph/费用上限。 |
| Memory/历史直接变成 Evidence | **Not Confirmed** | `context.py:CONTEXT_INSTRUCTION:29`、`_validate_rewrite:402`；`worker.py:execute_async:251` 只将改写问题/filters 交给两图。`tests/unit/test_conversation_context.py:615` 与 `tests/integration/test_conversation_worker.py:327` 覆盖错误 500 与 fresh source 23/120 的隔离。 | 保持边界，增加 retry/summary/错误 Memory 回归；真实模型抗注入能力仍需验证。 |
| quote 是模型生成而非原文 | **Not Confirmed**；逐 claim 窄 span **Partially Confirmed** | `retrieval/service.py:record:43` 取完整 chunk；`retrieval/evidence.py:exact_span:26` 校验 exact substring；`domain/research.py:ClaimEvidencePair:91` 只有 ID，没有 claim-specific offsets。 | Phase 8 添加 exact supporting span；无法验证时回退原 chunk，不能改写原文。 |
| Local API 依赖 Host/Origin 而无认证 | **Confirmed** | `api/security.py:local_request_error:33`，无 Origin 的本地命令行请求第 42 行放行，GET 也不要求凭据。Loopback 和 origin 保护不是 client authentication。 | local token、受限存储、Web/桌面认证与错误 token 测试，保留既有 Host/Origin 检查。 |
| provider raw exception 已泄漏 | **Not Confirmed** | `providers/chat.py:LiteLLMProvider.complete:142`、`providers/embedding.py:LiteLLMEmbedder.embed:145` 转换 safe code；`worker.py:execute_async:400` 安全 rethrow；`api/app.py:invalid_request:71` 不回显拒绝输入。 | 补四 provider 的 Run/SSE/UI/log redaction 回归；不能把测试不足描述成已证实泄漏。 |
| 删除会话后 Redis 作业未撤销 | **Confirmed** | `api/conversations.py:delete_conversation:202` 只 commit FK cascade，没有 queue.cancel。已经派发的任务可能继续占用队列/外部调用。 | DB 删除成功后 best-effort queue cancel；Redis 失败不得阻断删除。 |
| 删除/取消必然复活消息或丢失终态事务 | **Not Confirmed** | `jobs.py:ensure_running:132`、`finish_run:175`、`cancel_run:210` 已有锁与发布权限检查；API `persist_turn:141` 原子保存 Run/messages/outbox。`test_conversations.py:239/370/412` 有相关回归。 | 补并发 finish/delete 与实际排队 cancel 测试；保留原子性和锁顺序。 |
| 中文对英文论文的默认检索质量差 | **Partially Confirmed** | `settings.py:Settings:15/20` 默认英文 MiniLM embedding/reranker；`retrieval/service.py:LexicalRetriever.search:137` 使用 English FTS。适配器可配置，但本次多语言 Recall/MRR 尚未实测。 | 先运行真实模型 benchmark；没有结果前不换 production default。 |
| 发布制品没有 Git 时 provenance 可失败 | **Confirmed** | `evaluation/artifacts.py:source_commit:24` 先读 GIT_COMMIT/git，再直接读取 `.git/HEAD`；`frontend/src-tauri/build.rs:1` 未写制品 build metadata。缺 git、缺有效变量及 .git 时可失败。 | 注入可信构建 commit/version/hash；明确 unknown fallback，不能生成假的 commit。 |
| Windows CI/安装验收已完成 | **Not Confirmed** | `.github/workflows/desktop.yml:linux-desktop:4` 只有 Linux job；`tauri.conf.json:bundle:19` 声明 targets=all 不证明 MSI/NSIS 已构建。 | 增加真实 windows-latest 构建、制品和明确签名状态；Windows 11 安装验收单列。 |
| 文档错误宣传全部推理都在本地/完全 standalone | **Not Confirmed** | `README.md:23` 已说明桌面不打包 Python/PG/Redis；第 118 行说明 remote providers 接收必要 context。 | 双语统一使用 Desktop Client + Local Backend；保持 privacy 边界准确。 |
| 高频查询完全没有索引 | **Not Confirmed** | `db/models.py:Message:245/253` 有 conversation+ordinal，Run/status/run_id、Memory conversation+created_at、Chunk paper/section、GIN FTS 均已有索引。 | 按新增 cursor/lineage 的实际 SQL pattern 检查；不盲目加索引。 |

问题范围以代码和已运行检查为依据。未发现可信的 API key 硬编码泄漏证据，不据用户提示推定论文数据泄漏、错误 benchmark 或部署成功。

## B. Architecture Changes

| 方面 | Before：审查基线 | After：当前 Phase 1 | 后续状态 |
| --- | --- | --- | --- |
| 比较正确性 | paper_count≥2 才进入分析 | 可用原文进入分析；reviewer 显式实体支持和 citation pair 检查；paper_count 仅诊断 | 已实现，完整工程检查继续 |
| Retry | metadata 记录重试，无 effective lineage | 新增 retry_of_message_id、attempt_number、is_effective；summary 排除过期 attempt | 已实现，迁移/回归见 C/J |
| 删除 | 数据库 cascade，未撤销外部排队任务 | 删除 commit 后 best-effort queue cancel；late write guard 保留 | 已实现，race 回归见 J |
| 消息与前端刷新 | 完整 Run 随所有消息重复序列化；活跃任务每 4 秒全历史刷新 | 标量 RunSummary 投影，最近 50/双向 ordinal cursor；SSE 为主，active placeholder 单条对账；Run/Evidence lazy load | Phase 2 已实现、Before/After 实测完成 |
| 队列 | 固定 research queue，一个 worker | 固定路由到 interactive/ingestion/evaluation；三个独立 Compose worker，单角色 CLI | Phase 3 已实现，真实 RQ/PG occupancy probe 见 G |
| Context/Memory | 有历史就改写；全部 Memory 入 payload；仅 extractive summary | 双语 gate、Top-K text/全部硬约束、分项近似 token 预算、持久化 typed intent state | Phase 4 已实现，F 节实测 |
| 认证、Windows、span UX | 维持基线行为 | 当前未将计划作为实现记录 | **Phase 5–8 PENDING** |

保留独立 RAG/Research Graph、PostgreSQL/pgvector、Redis/RQ、Knowledge Base、PDF/arXiv ingestion、SSE、Evaluation、Multi-provider、Tauri 和 Web fallback。没有从零重写或用大型基础设施替换现有组件。

## C. Correctness Fixes

Phase 1 已落盘：

1. `domain/research.py:ComparisonEntityCoverage`、`retrieval/evidence.py:verify_claims/evidence_gate`：至少两个规范化后不同的 source-grounded entities；每个实体必须被 reviewer 语义支持，其支持 pair 必须属于已支持且不矛盾的 claim。名字出现、两个 chunk、两篇论文都不能单独证明比较成立。缺 coverage 时 fail closed。
2. `graphs/rag.py:verify` 与 `graphs/research.py:review` 共用验证；Research replan 保留原 comparison 类型和目标，避免放宽 scope。
3. `api/conversations.py:retry_message`、`conversations/context.py:ContextBuilder.build/roll_summary`、`conversations/service.py:prepare_context`：审计消息保留；失效 attempt 不再进入当前上下文；过期 summary source 触发重建。
4. `migrations/versions/0005_message_retry_lineage.py`：增量迁移和旧 lineage 回填；无 drop database 或删除旧 Run。已有安装通过 Alembic 升级，专用迁移回归检查数据保留。
5. `api/conversations.py:delete_conversation`：删除提交后尽力撤销排队/运行任务；数据库删除不依赖 Redis 成功。已有 late-worker publication guard 和终态事务保留。

Comparison 新增 23 条单元与 6 条真实 PG 集成回归，覆盖同一 chunk 双实体、同论文双 chunk、多论文、缺实体、缺 coverage、语义否定、无原文实体、错误 pair、重复/别名实体、缺目标、空检索、replan 降级。Retry/删除/迁移的新增回归由完整 Python 测试集覆盖，不能把 scripted provider 通过解读为真实模型科学正确率。

## D. Performance

**Before / After 均已实测。** 原始记录分别为 `docs/benchmarks/messages-before.json`、
`messages-after.json`；可复跑脚本 `scripts/benchmark_messages.py`。Before code
`ba04246`，After 实测本地 implementation commit `affa5d9`（GitHub 发布的 tree 会验证一致）。
18 组原请求的 stored-result hash 全部一致，数据/查询数量未改变；新增 cursor 另标。

这是 **SYNTHETIC API PERFORMANCE FIXTURE / NOT SCIENTIFIC RETRIEVAL QUALITY**。
真实 PostgreSQL 17.10 / pgvector 0.8.2 + FastAPI TestClient，每组 3 warmups + 15 samples；
每 Run 8 evidence/40 trace。包含 PG TCP、DB 查询、序列化、ASGI，不包含实际 HTTP/TLS、
浏览器渲染或模型推理。Before 270 samples，After 原请求 270 + cursor 360 = 630 samples。
环境依赖的延迟不能推为生产 throughput 或科研检索质量。

| 模式 | 总消息数 | Before / After bytes | median ms | p95 ms | SELECT | Run-result SELECT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| rag | 10 | 510886 / 9316 | 14.492 / 5.216 | 27.866 / 6.940 | 12 / 2 | 10 / 0 |
| rag | 50 | 2554466 / 46616 | 46.554 / 5.919 | 111.653 / 6.505 | 52 / 2 | 50 / 0 |
| rag | 100 | 5108941 / 93241 | 93.346 / 7.449 | 190.527 / 9.576 | 102 / 2 | 100 / 0 |
| research | 10 | 510936 / 9366 | 13.781 / 4.702 | 17.836 / 5.623 | 12 / 2 | 10 / 0 |
| research | 50 | 2554716 / 46866 | 43.544 / 6.095 | 104.982 / 7.408 | 52 / 2 | 50 / 0 |
| research | 100 | 5109441 / 93741 | 108.276 / 7.269 | 190.798 / 8.535 | 102 / 2 | 100 / 0 |

RAG 100 条完整历史从 5,108,941 降为 93,241 bytes（98.17%），SELECT 从 102 降为 2，
重型 Run-result SELECT 从 100 降为 0。median 93.346 → 7.449 ms。该对照仍返回完整 100 条。
新默认最近 50 条是不同请求：RAG 100 条对话返回 46,626 bytes、median 6.974 ms；
单条增量 1,280 bytes、4.172 ms；空增量 2 bytes、4.224 ms。不能拿 50 条与旧 100 条假比。

实际 UI 的 120-message fixture 验证首次仅 50 条、没有批量读取 full Runs；点击引用只读取
对应的 1 个 Run，再次点击复用缓存。201 条 user 历史仍能显式分页完整恢复；正常 SSE
跨越旧 4 秒轮询周期不增加历史请求，丢失 done 时能核对原 ordinal 并恢复完成状态。
这些是浏览器回归，不是已量化的浏览器渲染性能或真实模型质量。

实际 context/summary latency、Evaluation+interactive 并发等待将在后续阶段测量。

## E. Retrieval

当前保留生产默认模型、索引 fingerprint、共同 metadata filters、RRF 与 rerank threshold。现有真实 PG 测试证明向量/FTS/filter SQL 合约，不证明真实 embedding/reranker 的跨语言质量。

| 质量评测 | Recall@5 / Recall@10 / MRR / nDCG | 状态 |
| --- | --- | --- |
| English query → English paper | Not measured | Phase 5 PENDING |
| Chinese query → English paper | Not measured | Phase 5 PENDING |
| Chinese query → Chinese paper | Not measured | Phase 5 PENDING |
| 当前模型 / multilingual 候选 / translation / reranker 对照 | Not measured | 无证据前不更改默认模型 |
| dense / FTS / reranker 独立延迟 | Not measured | 必须由实际运行记录 |

English PostgreSQL FTS 是词法检索，不能写成 BM25。当前 exact vector search 是否需要 ANN 必须由规模/Recall/延迟实测决定，未发现需要立即换数据库的证据。数据集必须记录许可、原文来源、版本、可核验 gold relevance 与 index/model/prompt identities；synthetic/demo 不能伪装人工金标。

## F. Memory

`conversations/context.py:query_needs_context` 对独立问题跳过额外改写调用；代词/序号/上一轮提示仍需校验改写。`select_memories` 将 Stored Memory 与 Selected Memory 区分，默认 Top-K=8，按英文字词/中文 bigram 相关性、kind、ID 稳定排序；默认 selected memory/recent/summary 近似预算 1024/2048/1024。`merge_context_filters` 在选择前交集**全部**硬约束，未选文本的条件仍生效。指令、schema、wrapper/current query/filters/实体均计入总预算 8192；估算 CJK 每字 2、Latin/数字约每 3 字符 1、标点 1，仍保留 UTF-8 摘录上限。这不是精确 tokenizer 或真实费用上限。

`domain/conversation_context.py:ConversationState` 与 `db/models.py:ConversationStateRecord`（新增迁移 0006）持久化 goal、constraints、validated entities、显式短 term 和 open question，带 source IDs/version/through ordinal，标记非证据且没有 scientific findings 字段。`conversations/service.py:prepare_context` 恢复原会话仍有效的旧实体来源；superseded/删除的来源不得恢复。Memory 变更/summary 删除/clear 会使 state 失效。GET state + Memory 面板可查看；extractive summary 继续存在。

原错误历史/Memory “500”→fresh 原文 “120/23” 的全部断言保留。为了继续覆盖 contextualizer 边界，原独立问题改为明确代词追问，未删掉错误文本进入改写输入、不能进入 analyst/reviewer、最终必须 fresh quote 的断言。

`docs/benchmarks/context-before.json` / `context-after.json` 每案例 15 个原始 perf_counter 样本；50/100 条英/中文消息 + 同样 100 条 Memory，4 个共享字段 fixture hashes 相同。Before 在默认预算下四例均明确 `context_budget_exceeded`；After 四例构造成功、Top-K 8、全部 Methods 硬约束保留。只测 Context Builder CPU/序列化输入，不调用 LLM，不推断回答质量或端到端速度。下表从 raw samples 自动生成。

| Fixture | Before status / p50 CPU | After input / p50 CPU |
| --- | --- | --- |
| 50 / en / 100 memories | budget_exceeded / 128.390 ms | 5333 approximate tokens / 32.950 ms |
| 50 / zh / 100 memories | budget_exceeded / 133.751 ms | 5103 approximate tokens / 39.221 ms |
| 100 / en / 100 memories | budget_exceeded / 133.681 ms | 5333 approximate tokens / 37.850 ms |
| 100 / zh / 100 memories | budget_exceeded / 139.064 ms | 5103 approximate tokens / 44.790 ms |

同机、顺序运行、每例 15 samples、无 inference；未控制 OS scheduling/CPU isolation，不提供质量或吞吐结论。

**Conversation Context ≠ Scientific Evidence；Memory ≠ Evidence；Model Output ≠ Source of Truth。** 历史/Memory 仅理解意图和实体；答案仍需本轮检索原文、exact span、claim/pair semantic verification、确定性引用。现有错误历史/错误 Memory 回归已保留；实际模型面对 adversarial history/document injection 的失败率未测。

## G. Queues

`queues.py:freeze_queue` 按 Run kind 路由并冻结 `_queue_name`，原 outbox、幂等 job ID、claim 和 reconciliation 保留。`worker.py:main` 的 `--queue` 每次只选一个 workload，Compose 三个 worker 可分别扩容；配置拒绝同名队列。`api/app.py:queues` 分别报告三个 workload；ready 只保证 interactive 基础设施可用，不推断 ingestion/evaluation 或模型 readiness。

真实 PG/Redis + 两个真实 RQ worker 的单次 occupancy probe：evaluation 在同步屏障上保持 running，interactive RAG 在 **111.697 ms** 完成并发布 scripted source citation，之后才释放 evaluation。原始记录与复跑命令见 `docs/benchmarks/queue-isolation.json`、`tests/integration/test_queue_isolation.py`。这是合成占用隔离检查，**不是模型性能/科学质量评测，不提供 p95 或容量推断**。共享主机 CPU/RAM 仍可能互相争用。已有 research 作业由临时 `--queue legacy` worker 排空，不能直接清空 Redis。

## H. Security

Local API token、secret storage 与四 provider redaction 回归：**Phase 6 PENDING**。当前 Host/Origin/loopback 保护仍有价值，但不认证本机 client。Token 不得进入 JS localStorage、请求 URL、日志、Run result 或镜像；Web fallback 也必须验证合法/缺失/错误 token，并保持 SSE/download 行为。

已有 provider safe error codes、Pydantic input 不回显、runtime key env 和 common credential pattern guards 继续保留。没有确认 raw provider exception 泄漏，不宣称模式检查能检测所有 secret。默认 Compose 的 PDF、index、conversation、Memory、summary、Run history 在本地持久化；远端 chat/embedding/judge 仍收到必要 prompt/context。仅配置所有相关本地推理资源时，才能把模型推理也留在本地。

## I. Windows

真实 windows-latest CI、EXE、MSI、NSIS、build provenance、安装文档：**Phase 7 PENDING**。当前仅审查到 Linux CI 与通用 Tauri bundle 配置，不能推断 Windows installer 已构建。**Windows 11 install/launch/connect/chat/restart/uninstall：NOT EXECUTED。Signing：未验证、未签名状态待真实制品确定。**

产品仍是 **Desktop Client + Local Backend**：Tauri/React UI；独立运行 FastAPI、PostgreSQL/pgvector、Redis、RQ worker。不会为了打包把全部 backend 塞进 Tauri。Phase 8 的 PDF 页码定位、证据文案和 diagnostics 亦 PENDING；系统默认 PDF viewer 是否尊重页码尚未验证。

## J. Tests

本次阶段实际检查，由 root 汇总。测试 provider 为明确标注的 scripted transport；没有付费模型调用或模型下载。Rust 命令在 `frontend/src-tauri` 运行，使用外部 Cargo target 和已验证的 Linux GTK/WebKit sysroot。

| 检查 | 实际命令/范围 | 结果 |
| --- | --- | --- |
| baseline Evidence 局部 | `.venv/bin/pytest -q tests/unit/test_citation_pairs.py tests/unit/test_retrieval.py tests/unit/test_conversation_context.py tests/unit/test_sdk_provider.py` | PASS，90 |
| Phase 1 comparison 局部 | `.venv/bin/pytest -q tests/unit/test_comparison_evidence.py tests/unit/test_citation_pairs.py tests/unit/test_retrieval.py tests/unit/test_graphs.py tests/unit/test_conversation_context.py tests/unit/test_conversation_evaluation.py` | PASS，198；不是全量结果 |
| Phase 1 Python 全量 | `.venv/bin/pytest -q`，隔离 `TEST_DATABASE_URL` / `DATABASE_URL` 和 `TEST_REDIS_URL` / `REDIS_URL`，真实 PG 17.10 + Redis | PASS，504；3 条 upstream warnings |
| comparison 文件 Ruff | `.venv/bin/ruff format` / `.venv/bin/ruff check`，5 个 source + 3 个 test 文件 | PASS |
| comparison 文件 mypy | `.venv/bin/mypy src/ragagent/domain/research.py src/ragagent/retrieval/evidence.py src/ragagent/graphs/rag.py src/ragagent/graphs/research.py src/ragagent/graphs/state.py` | PASS，5 source files |
| Frontend Playwright | `npm run test:e2e`，系统 Chromium | PASS，26 |
| Frontend transport unit | `npm run test:transport` | PASS，8 |
| Rust tests | `cargo test --locked` | PASS，7，无 ignored |
| Python 静态检查 | `.venv/bin/ruff format --check .`、`.venv/bin/ruff check .`、`.venv/bin/mypy src` | PASS，151 格式文件、64 source files |
| Frontend 静态检查/构建 | `npm ci --cache /tmp/ragagent-review-npm-cache`、`npm run lint`、`npm run check`、`npm run build` | PASS |
| Rust 静态检查 | `cargo fmt --check`、`cargo check --locked`、`cargo clippy --locked -- -D warnings` | PASS |
| Tauri Linux native build | `npm run desktop:build` | PASS；6.6 MiB release executable，未测实际产品窗口 |
| Compose config/build | `docker compose --project-name ragagent-engineering-review --env-file <isolated-env> -f compose.yaml -f compose.cloud.yaml -f <validation-overlay> config --quiet` / `build` | PASS；保留 TLS 验证，通过 BuildKit CA secret 安装冻结依赖 |
| Compose 完整启动/health/ready | 同上 `up -d`；分步 `up -d db redis`、`run --rm --no-deps migrate` 重试 | 完整启动 FAIL（环境 ENOSPC）；DB/Redis healthy，API health/ready **NOT EXECUTED** |
| Windows CI/build/安装 | 尚未执行 | NOT EXECUTED |
| 实际论文 + 实际 chat/embedding/reranker 的 A–K 人工验收 | 尚未执行 | NOT EXECUTED |

504 项 Python 的 3 条 warnings 为 upstream Alembic path_separator 与 RQ fork 提示；未以删测试、skip 核心路径或降低断言通过检查。禁网 sandbox 曾使本地 async IPC 卡住；启用测试进程本地网络权限后 Evidence 局部成功，不代表调用付费 API。

Docker daemon 使用 VFS，约 2.06 GB 后端镜像在创建多个容器时复制完整文件系统，耗尽环境的 32 GB 配额。清理只限本次验证创建的容器、镜像和明确识别的 build cache/Cargo debug cache；数据库卷保留。镜像构建成功不能替代运行检查，后续阶段需重新验证启动。

## K. Known Limitations

- **Phase 5–8 未完成**；真实多语言模型对比、Local API auth、Windows 工程化等仍需实施并重新测试。
- semantic verifier 是 model-based 检查，可能判断错误；source-grounded 名字与 exact substring 不等于科研结论正确，最终结论需人工复核。
- summary 为有损 extractive；改写输入预算不是总费用上限。取消/删除不能撤回已经发出的远端请求或保证零费用。
- Before/After 性能数据仅合成 API fixture；浏览器渲染性能和真实多语言检索质量尚未测量；队列隔离仅有单次合成 probe；不生成推断指标。
- 真正的 Windows 11、macOS packaging/signing、系统 PDF page navigation、实际模型 provider readiness 和模型权重许可/下载可达性需要对应平台、模型与数据验证。
- 增量数据库迁移不应删除旧 conversation/message/Run/Memory/papers/evaluation；降级恢复策略以实际 Alembic 与备份检查为准。

## L. Future Work

Phase 5–8 属于**本次任务剩余范围**，不能移到 Future Work 伪装完成。仅将不必在当前本地单用户版解决的工作列于此：公网/多租户 RBAC 与 TLS 部署、基于大规模实测的 ANN 调优、原始 Graph 自动 checkpoint 恢复、长期人工科学结论审查流程。需要规模、威胁模型或业务需求后再实施，避免无依据新增大型数据库/Agent。


### Phase 2 checks (2026-10-08 Asia/Shanghai)

消息 API 分离 `MessageSummary` / `RunSummary`，GET 仅选择 Run 的标量白名单列；
POST send/retry 回包也不携带完整 result。默认最新 50，旧显式 offset 兼容；
新增 before/after ordinal 和单条消息读取，列表及单条接口引用详情都按需加载。
conversation list 批量读取 active Run IDs，避免 N+1。终态 Message metadata
仅保留有上限的 notes、经过最终 claim/pair/exact-span 检查的 citation ID/page refs
和简短 query 信息；旧记录保持完整 Run detail 可读，不强制全表回填。

Tasks 从 845 行拆出历史侧栏、消息列表、Composer、ResultPanel 与两项数据/SSE hooks。
健康 SSE 不触发周期消息轮询；断线、应用恢复、同 ordinal 终态变化有增量对账。
原 26 个浏览器用例均保留，新加大历史 lazy-citation 和 healthy-SSE/lost-done 回归。
旧 201 条 user 历史断言改为先最新页、显式上翻后仍验证完整 201 条，未减少覆盖。

实际：Ruff format/lint、mypy 65 source files；真实 PG/Redis 全量 **522 passed**
（3 upstream warnings）；npm ci/lint/check/build；**8 transport + 28 Playwright passed**；
Rust fmt/locked check/test/clippy **8 passed、0 ignored**；Linux Tauri release build PASS。
Compose config、frontend 镜像 build PASS；backend 镜像重建与完整 health/ready
**NOT EXECUTED**：已确认 VFS/32 GB 配额，只剩不足 2 GB，上一阶段创建后端容器已触发
ENOSPC。这不是部署通过。Windows/真实模型科学验收仍 NOT EXECUTED。
After benchmark 已在该 implementation commit 的独立数据库实跑，原始数据与范围见 D。


### Phase 3 checks (2026-10-08 Asia/Shanghai)

Ruff format/lint PASS，mypy 66 source files PASS，真实 PG/Redis 全量 **534 passed**
（3 upstream warnings）。npm ci/lint/typecheck/build、**8 transport + 28 Playwright** PASS；
Rust fmt/locked check/test/clippy **8 passed、0 ignored**。Compose config PASS。
Docker backend build/full health/ready **NOT EXECUTED**（VFS/32 GB 配额，剩余不足 1 GB）；
frontend 镜像沿用 Phase 2 已验证内容。Windows CI/真实模型验收仍 NOT EXECUTED。
Linux Tauri 本阶段 build 状态另行记入阶段日志，未据打包推断产品窗口或 Windows 能力。

Phase 3 Linux Tauri `npm run desktop:build`：PASS，实际 release 编译 1m21s；未测 Windows 或产品窗口。


### Remote validation and source mapping

Phase 2 GitHub Actions [CI run 37731584513](https://github.com/chouytong/RAGAgent/actions/runs/37731584513)：backend/frontend/Compose 均 success，真实完成 config、build、full up、health/ready 和 smoke。该检查对应 head `9bc1aeae9321a8e4011f7a6a33c8ba9825dea874`，不能当作 Phase 3/4 通过。

MCP 发布保留源码 tree SHA，commit metadata 重建后：Phase 2 implementation 本地 `affa5d9` 对应远端 `cd1b3957732c0e0a66ac14de0d006a4723774416`（tree `e12f627d6a604f687b1998ea59a2b264df47494b`）；Phase 3 本地 `2d0c8aa` 对应远端 `3e5c30fee40f6a32f5878c157e4cb79f5be0b913`（tree `dfd72cfbc43a4879b0ecfd1c137b5ead2b3cf68d`）。原始 measurement 的 recorded local commit 保留；Context measurement 是 working implementation，另记录 exact module SHA-256，base commit 不冒充 measured immutable commit。


### Phase 4 checks (2026-10-08 Asia/Shanghai)

新增 gate/selector/state 的单元与真实 PG restart/来源失效/clear 生命周期检查。
Ruff format/lint、mypy 66 sources PASS，真实 PG/Redis 全量 **558 passed**（3 upstream warnings）；
前端 npm ci/lint/check/build、**8 transport + 28 Playwright** PASS；
Rust fmt/locked check/test/clippy **8 passed、0 ignored**；Linux Tauri release build PASS（1m23s）。
Compose config PASS，当前版本本地 image build/full health/ready NOT EXECUTED（VFS 磁盘配额）；
Phase 2 的远端 Compose success 不冒充本版本验证。Windows/真实模型科学验收 NOT EXECUTED。
