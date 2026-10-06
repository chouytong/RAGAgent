# 产品化升级验收记录

日期：2026-10-06。基于已有 `phase-6-evaluation-deployment` 实现增量升级；保留
Knowledge Base、Settings、Evaluation、两套独立 Graph、引用校验、Run/Worker/SSE
以及 Web/Compose 运行方式。

发布分支：[feature/desktop-conversations](https://github.com/chouytong/RAGAgent/tree/feature/desktop-conversations)。

## 1. 修改内容

新增持久化多轮聊天、会话侧栏、滚动摘要、显式结构化记忆、受控查询改写、
桌面窗口和四维会话评测。重复提交、失败重试、取消、断流、重启和删除均沿用
原有任务架构，并补足消息状态的原子持久化。

## 2. 最终架构

```text
Tauri 独立窗口 / Web fallback
  → 同一 React Chat UI
  → 桌面：受限 Rust IPC → 固定 127.0.0.1:8000
    Web：同源 HTTP / SSE
  → FastAPI Conversation API
  → 本地 PostgreSQL 的 Message + Run + durable dispatch
  → Redis/RQ Worker
  → Context Builder → 查询改写
  → 独立 RAG Graph 或 Research Multi-Agent Graph
  → 新检索 → Exact Evidence → 引用支持校验
  → Assistant + Run + final event 同事务提交 → SSE / 消息重载
```

## 3. 数据模型与迁移

`Conversation` 保存类型、标题、时间和服务端元数据；`Message` 保存角色、
顺序、内容、状态及 Run 关联；`ConversationSummary` 保存版本、覆盖顺序及来源；
`Memory` 保存用户显式目标、约束、术语、偏好或任务，约束可带结构化过滤器。
Run 增加可空会话关联、幂等键及单会话活动任务唯一索引。

Alembic `0004` 可升级已有数据库，保留论文、索引和旧 Run，无需重建数据库。
聊天删除通过外键级联移除其 Run、事件和 dispatch；知识库保持独立。

## 4. API

- `/api/conversations`：创建、分页列出及模式筛选。
- `/api/conversations/{id}`：读取、重命名、删除；`POST /clear` 清空会话内容。
- `/{id}/messages`：有序分页读取、幂等发送；`/{message_id}/retry` 新建重试 Run。
- `/{id}/summary`：查看、删除；`/{id}/memories`：查看、新建。
- `/{id}/memories/{memory_id}`：删除单条；`DELETE /{id}/memory` 清空记忆和摘要。
- `POST /api/runs/{id}/cancel`：先撤销数据库执行权，再尽力停止 RQ。
- `POST /api/evaluations/conversation`：复用原 Run、队列、worker 和 artifact 下载。
- 已有 `/api/runs/{id}/events` SSE 继续用于聊天任务。

完整请求、响应、状态码与删除边界见 [API](api.md)。

## 5. Conversation / Memory 工作流程

Worker 读取当前用户消息之前的有效历史，合并近期摘录、本地滚动摘要和显式记忆。
预算包含改写指令、schema、payload 和 wrapper，以 UTF-8 字节作保守 token 估计；
实际 token/费用仍由 provider 记录。摘要采用确定性、有损摘录，不额外计费调用 LLM。

改写记录原问题、独立问题、来源 ID、配置和版本。比较追问必须保留比较意图与
多个有来源的候选，不能凭历史先挑赢家。结构化约束与当前过滤器取交集。
历史、摘要和记忆不进入 Evidence；analyst/reviewer 使用本轮检索的原文证据。

清空记忆保留聊天历史，因此后续仍可使用近期消息并重建摘要；要同时遗忘聊天上下文，
使用清空或删除会话。删除不是备份和独立下载缓存的取证擦除。

## 6. Desktop UI

Tauri v2 复用 React，提供真实独立窗口及 Rust 本地请求/SSE/文件桥。
目标固定为 `127.0.0.1:8000`，禁代理和重定向，方法/路径白名单和有界取消状态
限制权限。窗口能显示后端不可用及尚未就绪，第一版不打包或自动启动 Python/数据库。

PDF/结果下载只接受本地限定资源，桌面缓存独立于数据库删除；Unix 缓存目录/文件
权限为 0700/0600，单文件及总量限制见 [部署说明](deployment.md)。

## 7. 修改文件

主要模块是 `db/models.py`、迁移 `0004`、`api/conversations.py`、
`domain/conversation*.py`、`conversations/context.py`/`service.py`、`jobs.py`、
`worker.py`、`evaluation/conversation*.py`，以及前端 Chat/Memory/transport/Tauri。
精确文件清单与验证命令记录在 [stage log](stage-log.md)；用户文档和 ADR 同步更新。

## 8. 测试结果

最终完整 Python 回归 **461 passed**（360 单元、101 集成）；Ruff format/check、
mypy 全部通过。前端 npm ci、lint、TypeScript check、build 通过，Playwright
**26 passed**（保留原 9 条，新增 17 条聊天回归），transport **8 passed**。
Rust fmt/check/clippy `-D warnings`、**7 tests** 及 Tauri release build 通过。
真实原生接口检查为 `local_backend_ready`；Xvfb/DBus 中真实窗口确认 Desktop 模式、
React DOM 与 `backend-status ready`。Compose 镜像构建、迁移和本地 HTTP/RQ/SSE
smoke 通过，使用空语料与明确缺密钥路径，不冒充模型推理验收。

本文件不把脚本 provider/合成论文的通过率当科研 benchmark。
Python 的数据库和队列测试使用真实 PostgreSQL/pgvector/Redis/RQ；模型适配器在测试中
明确为脚本。浏览器 HTTP/SSE fixtures 明确是 mock，另有真实 Compose/RQ/SSE smoke。

## 9. 未验证项

真实论文与实际配置模型的科研回答质量、人工金标、多轮拒答/引用准确率及性能提升
未验证。本次没有调用付费推理或把合成 demo 当真实论文验收。
用户要求的完整原生业务场景（真实论文上的 datasets → largest sample size →
关闭/重启 → methodology，以及 Research 比较）仍未验收；已通过的分层测试
不能替代这一场景，需要实际论文和可用模型配置。
Windows/macOS 的安装包、签名、公证、真实系统 IME 和 OS PDF 阅读器交互未验证。

## 10. 已知限制

摘要可能丢失实体/排序；改写、语义引用审查和 model-based judge 不能证明语义完美。
词法评测标签无法覆盖所有同义表达。预算约束改写输入，不代表整次研究任务有货币硬上限。
取消不能撤回已经送出的远程调用，已有费用仍保留；未知收费不会伪装成零。
常见凭据形状在聊天/记忆/会话评测提交前被拦截，未知自由文本秘密仍无法全部识别。
本地持久化不等于本地推理：远程 chat、embedding、judge 会收到必要输入。
前端构建约 515 kB JS（158 kB gzip），有体积提示但构建通过。

## 11. 本地服务启动

在包含此次升级的 checkout 中执行：

```bash
cp .env.example .env  # 仅首次创建；已有 .env 请保留，勿覆盖运行配置。
# 在本机运行环境中配置模型和密钥，再启动基础设施；勿提交 .env。
docker compose up --build
```

已有数据库按照 [部署升级步骤](deployment.md) 备份、暂停写入、运行 migration 后恢复。
基础设施 ready 与模型可推理是不同状态。

## 12. Desktop 启动和构建

安装 Rust 和平台 WebView 依赖后，在仓库根目录执行：

```bash
npm --prefix frontend ci
npm --prefix frontend run desktop:dev
# 编译原生程序；可选 desktop:bundle 生成平台包。
npm --prefix frontend run desktop:build
```

先启动上述本地后端。平台依赖、产物路径和验证命令见 [桌面部署](deployment.md#desktop-ui-with-local-backend)。

## 13. Web fallback

Compose 启动后访问 `http://localhost:8080`，不需要 Tauri。
开发时后端/API/worker 独立启动，前端使用：

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

Web 和桌面共用数据库、API、任务和 React，不依赖浏览器 localStorage 保存聊天正文。
