import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Conversation, Event, Message, Run, Turn, api } from "./api";
import type {
  Conversation as ConversationType,
  ConversationMode,
  Event as EventType,
  Message as MessageType,
  Run as RunType,
} from "./api";
import {
  Citations,
  ConfirmAction,
  FilterEditor,
  Json,
  emptyFilters,
} from "./components";
import { MemoryPanel } from "./Memory";
import { stream } from "./transport";

function selection(mode: ConversationMode) {
  const match = window.location.hash.match(/^#\/(rag|research)\/([^/?#]+)$/);
  return match?.[1] === mode ? match[2] : "";
}
function navigate(mode: ConversationMode, id = "") {
  window.location.hash = `/${mode}${id ? `/${id}` : ""}`;
}
const activeStatus = (status?: string) =>
  status === "queued" || status === "running";
const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  completed: "已验证结果",
  insufficient_evidence: "证据不足",
  failed: "执行失败",
  cancelled: "已取消",
};

function AssistantMessage({
  message,
  activeConversation,
  retryable,
  onChange,
  onRetry,
}: {
  message: MessageType;
  activeConversation: boolean;
  retryable: boolean;
  onChange: () => void;
  onRetry: (message: MessageType) => Promise<void>;
}) {
  const [run, setRun] = useState<RunType | null>(message.run);
  const [events, setEvents] = useState<(EventType & { eventId: string })[]>([]);
  const [connection, setConnection] = useState("");
  const [details, setDetails] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const cursor = useRef(0);
  const callback = useRef(onChange);
  callback.current = onChange;
  useEffect(() => {
    setRun(message.run);
  }, [message.run]);
  const runId = message.run_id;
  const running = activeStatus(run?.status ?? message.status);
  useEffect(() => {
    if (!runId || (!running && !details)) return;
    let live = true;
    const close = stream(`/api/runs/${runId}/events`, {
      cursor: cursor.current,
      onExecution: (data, eventId) => {
        if (!live) return;
        try {
          const parsed = Event.parse(JSON.parse(data));
          const nextCursor = Number(eventId);
          if (
            eventId &&
            Number.isFinite(nextCursor) &&
            nextCursor <= cursor.current
          )
            return;
          if (eventId && Number.isFinite(nextCursor))
            cursor.current = nextCursor;
          setConnection("");
          setEvents((previous) => [...previous, { ...parsed, eventId }]);
        } catch {
          setConnection("执行事件格式错误，正在从数据库恢复状态。");
        }
      },
      onDone: (data) => {
        if (!live) return;
        try {
          setRun(Run.parse(JSON.parse(data)));
          setConnection("");
          callback.current();
        } catch {
          setConnection("结果格式错误，正在从数据库恢复状态。");
          callback.current();
        }
      },
      onError: () => {
        if (live) setConnection("事件连接中断，正在重连；后台任务继续执行。");
      },
    });
    return () => {
      live = false;
      close();
    };
  }, [runId, running, details]);
  useEffect(() => {
    cursor.current = 0;
    setEvents([]);
    setConnection("");
  }, [runId]);
  const result = run?.result;
  const state = run?.status ?? message.status;
  const text =
    message.content ||
    (state === "completed" || state === "insufficient_evidence"
      ? result?.answer || result?.draft_report || ""
      : "");
  const limitations = [
    ...new Set([
      ...(result?.limitations ?? []),
      ...(result?.analysis_results.flatMap(
        (analysis) => analysis.limitations,
      ) ?? []),
    ]),
  ];
  const plan =
    result?.research_plan ??
    events.find((event) => event.node === "plan")?.payload.research_plan;
  return (
    <article
      className="chat-message assistant-message"
      aria-label="Assistant 消息"
      data-message-id={message.id}
    >
      <div className="message-heading">
        <strong>Scientific RAGAgent</strong>
        <h3 className={`status-badge ${state}`}>
          {statusLabels[state] ?? state}
        </h3>
      </div>
      {running && !text && (
        <p className="running-placeholder" role="status">
          正在检索与验证文献证据…
        </p>
      )}
      {text && (
        <Citations
          text={text}
          evidence={result?.evidence_pool ?? result?.reranked_evidence ?? []}
        />
      )}
      {(state === "failed" || state === "cancelled") && (
        <p role={state === "failed" ? "alert" : "status"}>
          {state === "failed"
            ? "本轮未完成，没有发布未经验证的回答。"
            : "本轮已取消。"}
          {run?.error_code && ` (${run.error_code})`}
        </p>
      )}
      {retryable &&
        (state === "failed" ||
          state === "cancelled" ||
          state === "insufficient_evidence") && (
          <button
            disabled={activeConversation || retrying}
            onClick={() => {
              setRetrying(true);
              void onRetry(message).finally(() => setRetrying(false));
            }}
          >
            重试本轮
          </button>
        )}
      {limitations.length > 0 && (
        <aside className="limitations" aria-label="未验证项与局限">
          <strong>未验证项与局限（尚未验证）</strong>
          <ul>
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </aside>
      )}
      {connection && <p role="alert">{connection}</p>}
      {run && (
        <details
          className="execution-details"
          onToggle={(event) => setDetails(event.currentTarget.open)}
        >
          <summary>
            执行详情 · {run.kind === "research" ? "Multi-Agent" : "RAG"}
          </summary>
          <p>
            状态：{run.status} · Trace: {run.trace_id}
            {run.error_code && ` · ${run.error_code}`}
          </p>
          {plan != null && (
            <details>
              <summary>Supervisor Plan</summary>
              <Json value={plan} />
            </details>
          )}
          <ol className="execution-trace">
            {events.map((event, index) => (
              <li key={event.eventId || index}>
                <strong>{event.node}</strong> · <time>{event.time}</time>
                <details>
                  <summary>执行详情（可能包含尚未审核的草稿）</summary>
                  <Json value={event.payload} />
                </details>
              </li>
            ))}
          </ol>
          {result?.review_result != null && (
            <details>
              <summary>Reviewer Result</summary>
              <Json value={result.review_result} />
            </details>
          )}
          {message.metadata.original_query != null && (
            <details>
              <summary>问题与指代解析</summary>
              <Json
                value={{
                  original_query: message.metadata.original_query,
                  contextualized_query: message.metadata.contextualized_query,
                }}
              />
            </details>
          )}
        </details>
      )}
    </article>
  );
}

export function Tasks({ research }: { research: boolean }) {
  const mode: ConversationMode = research ? "research" : "rag";
  const [conversations, setConversations] = useState<ConversationType[]>([]);
  const [conversation, setConversation] = useState<ConversationType | null>(
    null,
  );
  const [selectedId, setSelectedId] = useState(() => selection(mode));
  const [messages, setMessages] = useState<MessageType[]>([]);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(emptyFilters);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [rename, setRename] = useState<ConversationType | null>(null);
  const [title, setTitle] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const selectedRef = useRef(selectedId);
  const sequence = useRef(0);
  const listSequence = useRef(0);
  const busyRef = useRef(false);
  const composing = useRef(false);
  const end = useRef<HTMLDivElement>(null);
  const scrollContainer = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const pendingSubmission = useRef<{
    content: string;
    filters: string;
    conversationId: string;
    key: string;
  } | null>(null);
  const pendingRetry = useRef<{ messageId: string; key: string } | null>(null);
  selectedRef.current = selectedId;
  const activeRun =
    conversation?.active_run_id ??
    messages.find(
      (message) =>
        message.role === "assistant" &&
        activeStatus(message.run?.status ?? message.status),
    )?.run_id;
  const active = Boolean(activeRun);
  const latestAssistantId = messages
    .filter((message) => message.role === "assistant")
    .at(-1)?.id;
  const activeRef = useRef(active);
  activeRef.current = active;

  function choose(id: string) {
    selectedRef.current = id;
    setSelectedId(id);
    navigate(mode, id);
    setError("");
    setNotice("");
    setMemoryOpen(false);
    nearBottom.current = true;
  }
  const refreshList = useCallback(
    async (signal?: AbortSignal, append = false, offset = 0) => {
      const request = ++listSequence.current;
      const items = await api(
        `/api/conversations?mode=${mode}&limit=51&offset=${offset}`,
        z.array(Conversation),
        undefined,
        "GET",
        signal,
      );
      if (signal?.aborted || request !== listSequence.current) return;
      const visible = items.slice(0, 50);
      setConversations((previous) =>
        append
          ? [
              ...previous,
              ...visible.filter(
                (item) => !previous.some((old) => old.id === item.id),
              ),
            ]
          : visible,
      );
      setHasMore(items.length > 50);
      setHistoryOffset(offset);
      if (!selectedRef.current && visible.length && !append)
        choose(visible[0].id);
    },
    [mode],
  );
  const refreshMessages = useCallback(
    async (id: string, signal?: AbortSignal) => {
      const request = ++sequence.current;
      const [nextConversation, nextMessages] = await Promise.all([
        api(`/api/conversations/${id}`, Conversation, undefined, "GET", signal),
        (async () => {
          const history: MessageType[] = [];
          for (let offset = 0; ; offset += 200) {
            const page = await api(
              `/api/conversations/${id}/messages?limit=200&offset=${offset}`,
              z.array(Message),
              undefined,
              "GET",
              signal,
            );
            history.push(...page);
            if (page.length < 200) return history;
          }
        })(),
      ]);
      if (
        signal?.aborted ||
        request !== sequence.current ||
        selectedRef.current !== id
      )
        return;
      setConversation(nextConversation);
      setMessages(nextMessages.sort((a, b) => a.ordinal - b.ordinal));
      setLoading(false);
    },
    [],
  );
  useEffect(() => {
    const controller = new AbortController();
    void refreshList(controller.signal).catch((e) => {
      if (!controller.signal.aborted) setError(String(e));
    });
    const onHash = () => {
      const id = selection(mode);
      if (id) {
        selectedRef.current = id;
        setSelectedId(id);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => {
      controller.abort();
      window.removeEventListener("hashchange", onHash);
    };
  }, [mode, refreshList]);
  useEffect(() => {
    const controller = new AbortController();
    setMessages([]);
    setConversation(null);
    sequence.current += 1;
    if (!selectedId) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    void refreshMessages(selectedId, controller.signal).catch((e) => {
      if (!controller.signal.aborted) {
        setError(String(e));
        setLoading(false);
      }
    });
    const poll = setInterval(() => {
      if (activeRef.current)
        void refreshMessages(selectedId, controller.signal).catch(() => {
          /* SSE and next database poll recover transient failures. */
        });
    }, 4000);
    return () => {
      controller.abort();
      clearInterval(poll);
    };
  }, [selectedId, refreshMessages]);
  useEffect(() => {
    if (nearBottom.current)
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, active, busy]);
  const refreshCurrent = useCallback(() => {
    const id = selectedRef.current;
    if (id)
      void refreshMessages(id)
        .then(() => refreshList())
        .catch((e) => {
          if (selectedRef.current === id) setError(String(e));
        });
  }, [refreshMessages, refreshList]);
  async function createConversation() {
    const created = await api("/api/conversations", Conversation, { mode });
    choose(created.id);
    setConversation(created);
    setMessages([]);
    return created;
  }
  async function newChat() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await createConversation();
      setQuery("");
      setFilters(emptyFilters);
      pendingSubmission.current = null;
      await refreshList();
    } catch (e) {
      setError(String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function submit() {
    const content = query.trim();
    if (busyRef.current || active || !content || loading) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    nearBottom.current = true;
    let targetId = selectedRef.current;
    try {
      if (!targetId) targetId = (await createConversation()).id;
      const serializedFilters = JSON.stringify(filters);
      let pending = pendingSubmission.current;
      if (
        !pending ||
        pending.content !== content ||
        pending.filters !== serializedFilters ||
        pending.conversationId !== targetId
      ) {
        pending = {
          content,
          filters: serializedFilters,
          conversationId: targetId,
          key: crypto.randomUUID(),
        };
        pendingSubmission.current = pending;
      }
      const turn = await api(`/api/conversations/${targetId}/messages`, Turn, {
        content,
        filters,
        client_request_id: pending.key,
      });
      pendingSubmission.current = null;
      if (selectedRef.current === targetId) {
        setQuery("");
        setConversation(turn.conversation);
        setMessages((previous) =>
          [
            ...previous.filter(
              (item) =>
                item.id !== turn.user_message.id &&
                item.id !== turn.assistant_message.id,
            ),
            turn.user_message,
            turn.assistant_message,
          ].sort((a, b) => a.ordinal - b.ordinal),
        );
      }
      await refreshList();
      await refreshMessages(targetId);
    } catch (e) {
      if (selectedRef.current === targetId || !targetId)
        setError(`${String(e)}。输入已保留；再次发送会复用本次请求标识。`);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function retry(message: MessageType) {
    if (busyRef.current || active) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    const id = selectedRef.current;
    try {
      let pending = pendingRetry.current;
      if (!pending || pending.messageId !== message.id) {
        pending = { messageId: message.id, key: crypto.randomUUID() };
        pendingRetry.current = pending;
      }
      await api(`/api/conversations/${id}/messages/${message.id}/retry`, Turn, {
        client_request_id: pending.key,
      });
      pendingRetry.current = null;
      await refreshMessages(id);
      await refreshList();
    } catch (e) {
      if (selectedRef.current === id) setError(String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function cancel() {
    if (!activeRun || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await api(`/api/runs/${activeRun}/cancel`, Run, {});
      await refreshMessages(selectedRef.current);
      setNotice("取消已记录；正在处理的供应商调用仍可能产生费用。");
    } catch (e) {
      setError(String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function renameConversation() {
    if (!rename || !title.trim() || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const updated = await api(
        `/api/conversations/${rename.id}`,
        Conversation,
        { title: title.trim() },
        "PATCH",
      );
      if (updated.id === selectedRef.current) setConversation(updated);
      setRename(null);
      await refreshList();
    } catch (e) {
      setError(String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function removeConversation(id: string) {
    await api(`/api/conversations/${id}`, z.unknown(), null, "DELETE");
    if (selectedRef.current === id) {
      choose("");
      setMessages([]);
      setConversation(null);
    }
    await refreshList();
  }
  async function clearConversation() {
    const id = selectedRef.current;
    await api(`/api/conversations/${id}/clear`, z.unknown(), {});
    await refreshMessages(id);
    await refreshList();
    setMemoryOpen(false);
    setNotice("本地聊天消息、相关任务事件、摘要与记忆已删除。");
  }
  return (
    <section
      className="chat-layout"
      aria-label={research ? "Research Chat" : "RAG Chat"}
    >
      <aside className="conversation-sidebar" aria-label="Conversation History">
        <div className="sidebar-heading">
          <h2>{research ? "Research Chat" : "RAG Chat"}</h2>
          <button disabled={busy} onClick={() => void newChat()}>
            New Chat
          </button>
        </div>
        <p className="muted">本机会话历史</p>
        <ul className="conversation-list">
          {conversations.map((item) => (
            <li
              key={item.id}
              className={item.id === selectedId ? "selected" : ""}
            >
              <button
                className="conversation-select"
                aria-current={item.id === selectedId ? "page" : undefined}
                onClick={() => choose(item.id)}
              >
                <strong>{item.title}</strong>
                <small>
                  {item.mode === "research" ? "Research · Multi-Agent" : "RAG"}{" "}
                  · {new Date(item.updated_at).toLocaleDateString()}
                </small>
              </button>
              <div className="conversation-actions">
                <button
                  aria-label={`重命名 ${item.title}`}
                  disabled={busy}
                  onClick={() => {
                    setRename(item);
                    setTitle(item.title);
                  }}
                >
                  重命名
                </button>
                <ConfirmAction
                  label={`删除会话 ${item.title}`}
                  description="真正删除本地会话、消息、相关任务事件、摘要和记忆。正在执行的任务会被取消；已发生的供应商费用无法撤销。"
                  disabled={busy}
                  onConfirm={() => removeConversation(item.id)}
                />
              </div>
            </li>
          ))}
        </ul>
        {!conversations.length && (
          <p className="empty-history">还没有会话。新建会话或直接发送问题。</p>
        )}
        {hasMore && (
          <button
            disabled={busy}
            onClick={() =>
              void refreshList(undefined, true, historyOffset + 50).catch((e) =>
                setError(String(e)),
              )
            }
          >
            加载更早会话
          </button>
        )}
      </aside>
      <div className="chat-main">
        <div className="chat-toolbar">
          <div>
            <h2>
              {conversation?.title ??
                (research ? "新的研究会话" : "新的文献问答")}
            </h2>
            <p className="muted">
              {research
                ? "Supervisor → Retrieval → Analyst → Reviewer · 每轮重新验证证据"
                : "多轮理解问题，每轮重新检索和验证文献证据"}
            </p>
          </div>
          {conversation && (
            <div className="inline">
              <button
                aria-expanded={memoryOpen}
                onClick={() => setMemoryOpen((open) => !open)}
              >
                会话记忆
              </button>
              <ConfirmAction
                label="Clear Conversation"
                description="删除本会话全部消息、相关任务事件、摘要和记忆数据库记录。保留会话本身。"
                disabled={busy || active}
                onConfirm={clearConversation}
              />
            </div>
          )}
        </div>
        {error && (
          <p className="chat-error" role="alert">
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {memoryOpen && conversation && (
          <MemoryPanel
            key={conversation.id}
            conversationId={conversation.id}
            active={active}
          />
        )}
        <div
          className="message-list"
          ref={scrollContainer}
          aria-label="聊天消息"
          aria-busy={loading}
          onScroll={() => {
            const node = scrollContainer.current;
            if (node)
              nearBottom.current =
                node.scrollHeight - node.scrollTop - node.clientHeight < 100;
          }}
        >
          {loading && <p role="status">正在读取本地会话…</p>}
          {!loading && !messages.length && (
            <div className="chat-empty">
              <h3>{research ? "开始一段研究讨论" : "从文献证据开始"}</h3>
              <p>
                先在 Knowledge Base
                导入论文，再询问数据集、方法或指标。后续问题可以使用前文指代。
              </p>
              <p className="muted">
                聊天历史与记忆用于理解问题，不能代替论文证据。
              </p>
            </div>
          )}
          {messages
            .filter((message) => message.role !== "system")
            .map((message) =>
              message.role === "user" ? (
                <article
                  key={message.id}
                  className="chat-message user-message"
                  aria-label="User 消息"
                >
                  <div className="message-heading">
                    <strong>你</strong>
                  </div>
                  <div className="user-content">{message.content}</div>
                </article>
              ) : (
                <AssistantMessage
                  key={`${message.id}:${message.run_id}`}
                  message={message}
                  activeConversation={active}
                  retryable={message.id === latestAssistantId}
                  onChange={refreshCurrent}
                  onRetry={retry}
                />
              ),
            )}
          <div ref={end} />
        </div>
        <form
          className="chat-composer"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <FilterEditor
            key={selectedId}
            value={filters}
            onChange={setFilters}
          />
          <label className="visually-hidden" htmlFor={`query-${mode}`}>
            研究问题
          </label>
          <textarea
            id={`query-${mode}`}
            rows={3}
            maxLength={10000}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
            }}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing &&
                !composing.current &&
                event.keyCode !== 229
              ) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder={
              research ? "输入研究问题或继续上一轮讨论…" : "输入文献问题或追问…"
            }
          />
          <div className="composer-actions">
            <small>
              Enter 发送 · Shift+Enter 换行 · 摘要与记忆不是科研证据
            </small>
            {active ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void cancel()}
              >
                取消本轮
              </button>
            ) : (
              <button
                className="send-button"
                disabled={busy || loading || !query.trim()}
              >
                {busy ? "正在发送…" : "发送"}
              </button>
            )}
          </div>
        </form>
      </div>
      {rename && (
        <dialog open aria-label="重命名会话">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void renameConversation();
            }}
          >
            <h3>重命名会话</h3>
            <label>
              会话标题
              <input
                autoFocus
                value={title}
                maxLength={200}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <div className="inline">
              <button type="button" onClick={() => setRename(null)}>
                取消
              </button>
              <button disabled={busy || !title.trim()}>保存标题</button>
            </div>
          </form>
        </dialog>
      )}
    </section>
  );
}
