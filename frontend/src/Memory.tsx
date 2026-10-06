import { useEffect, useState } from "react";
import { z } from "zod";
import { Memory, MemoryKind, Summary, api } from "./api";
import type {
  Memory as MemoryType,
  MemoryKind as MemoryKindType,
  Summary as SummaryType,
} from "./api";
import { ConfirmAction, FilterEditor, Json, emptyFilters } from "./components";

const labels: Record<MemoryKindType, string> = {
  goal: "研究目标",
  constraint: "明确约束",
  term: "术语指代",
  preference: "研究偏好",
  task: "任务上下文",
};
export function MemoryPanel({
  conversationId,
  active,
}: {
  conversationId: string;
  active: boolean;
}) {
  const [summary, setSummary] = useState<SummaryType | null>(null);
  const [memories, setMemories] = useState<MemoryType[]>([]);
  const [kind, setKind] = useState<MemoryKindType>("goal");
  const [memoryKey, setMemoryKey] = useState("");
  const [content, setContent] = useState("");
  const [filters, setFilters] = useState(emptyFilters);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const base = `/api/conversations/${conversationId}`;
  async function refresh(signal?: AbortSignal) {
    const [nextSummary, nextMemories] = await Promise.all([
      api(`${base}/summary`, Summary.nullable(), undefined, "GET", signal),
      api(`${base}/memories`, z.array(Memory), undefined, "GET", signal),
    ]);
    if (!signal?.aborted) {
      setSummary(nextSummary);
      setMemories(nextMemories);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal).catch((e) => {
      if (!controller.signal.aborted) setError(String(e));
    });
    return () => controller.abort();
  }, [conversationId, active]);
  async function remove(path: string, success: string) {
    setBusy(true);
    setError("");
    try {
      await api(path, z.unknown(), null, "DELETE");
      await refresh();
      setNotice(success);
    } finally {
      setBusy(false);
    }
  }
  async function add() {
    if (busy || active || !content.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`${base}/memories`, Memory, {
        kind,
        key: memoryKey.trim() || null,
        content: content.trim(),
        ...(kind === "constraint" ? { filters } : {}),
      });
      setContent("");
      setMemoryKey("");
      setFilters(emptyFilters);
      await refresh();
      setNotice("记忆已保存到本机会话。不会作为论文证据。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="memory-panel" aria-label="会话记忆管理">
      <h3>会话记忆</h3>
      <p className="muted">
        摘要和记忆只用于理解问题与检索意图。科研事实必须重新检索文献并验证引用。
      </p>
      {active && <p role="status">本轮执行期间记忆为只读，结束后可编辑。</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <h4>Conversation Summary</h4>
      {summary ? (
        <>
          <p className="summary-text">{summary.content}</p>
          <small>
            摘要版本 {summary.version} · 压缩至消息 {summary.through_ordinal} ·
            对话上下文，非科学证据
          </small>
          <ConfirmAction
            label="删除摘要"
            description="删除本地摘要记录。聊天历史仍保留；后续较长对话可以生成新摘要。"
            disabled={active || busy}
            onConfirm={() => remove(`${base}/summary`, "摘要已删除")}
          />
        </>
      ) : (
        <p className="muted">
          暂无摘要。较长对话会将较早消息压缩为有边界的上下文摘要。
        </p>
      )}
      <h4>Structured Memory ({memories.length})</h4>
      <ul className="memory-list">
        {memories.map((memory) => (
          <li key={memory.id}>
            <strong>
              {labels[memory.kind]}
              {memory.key && ` · ${memory.key}`}
            </strong>
            <p>{memory.content}</p>
            {memory.filters && (
              <details>
                <summary>检索约束</summary>
                <Json value={memory.filters} />
              </details>
            )}
            <ConfirmAction
              label={`删除记忆 ${memory.key ?? memory.content.slice(0, 24)}`}
              description="真正删除此条本地记忆。"
              disabled={active || busy}
              onConfirm={() =>
                remove(`${base}/memories/${memory.id}`, "记忆已删除")
              }
            />
          </li>
        ))}
      </ul>
      {!memories.length && (
        <p className="muted">
          没有结构化记忆。由你明确添加，不建立隐藏用户画像。
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <label>
          记忆类型
          <select
            value={kind}
            disabled={active || busy}
            onChange={(event) => setKind(MemoryKind.parse(event.target.value))}
          >
            {MemoryKind.options.map((value) => (
              <option key={value} value={value}>
                {labels[value]}
              </option>
            ))}
          </select>
        </label>
        <label>
          记忆名称（可选）
          <input
            value={memoryKey}
            maxLength={128}
            disabled={active || busy}
            onChange={(event) => setMemoryKey(event.target.value)}
          />
        </label>
        <label>
          记忆内容
          <textarea
            rows={3}
            value={content}
            maxLength={4000}
            disabled={active || busy}
            onChange={(event) => setContent(event.target.value)}
            placeholder="例如：后续比较只看 2023 年后的论文"
          />
        </label>
        {kind === "constraint" && (
          <FilterEditor value={filters} onChange={setFilters} />
        )}
        <button disabled={active || busy || !content.trim()}>
          添加本地记忆
        </button>
      </form>
      <ConfirmAction
        label="Clear Conversation Memory"
        description="删除本会话全部摘要与结构化记忆数据库记录。保留聊天历史；清空全部内容请使用会话菜单。"
        disabled={active || busy}
        onConfirm={() => remove(`${base}/memory`, "本会话摘要与记忆已清空")}
      />
    </section>
  );
}
