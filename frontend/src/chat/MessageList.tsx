import { useEffect, useRef } from "react";
import type { ConversationMode, Message } from "../api";
import { ResultPanel, statusLabels } from "./ResultPanel";

export function MessageList({
  messages,
  selectedId,
  mode,
  loading,
  active,
  busy,
  hasOlder,
  loadingOlder,
  loadOlder,
  onReconcile,
  onRetry,
}: {
  messages: Message[];
  selectedId: string;
  mode: ConversationMode;
  loading: boolean;
  active: boolean;
  busy: boolean;
  hasOlder: boolean;
  loadingOlder: boolean;
  loadOlder: () => Promise<void>;
  onReconcile: (messageId: string) => Promise<void>;
  onRetry: (message: Message) => Promise<void>;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const latest = messages
    .filter((message) => message.role === "assistant" && message.is_effective)
    .at(-1)?.id;
  useEffect(() => {
    nearBottom.current = true;
  }, [selectedId]);
  useEffect(() => {
    if (nearBottom.current)
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, active, busy]);
  async function older() {
    const node = scroll.current;
    if (!node) return;
    const height = node.scrollHeight,
      top = node.scrollTop;
    nearBottom.current = false;
    await loadOlder();
    requestAnimationFrame(() => {
      if (scroll.current === node)
        node.scrollTop = top + node.scrollHeight - height;
    });
  }
  return (
    <div
      className="message-list"
      ref={scroll}
      aria-label="聊天消息"
      aria-busy={loading}
      onScroll={() => {
        const node = scroll.current;
        if (node)
          nearBottom.current =
            node.scrollHeight - node.scrollTop - node.clientHeight < 100;
      }}
    >
      {loading && <p role="status">正在读取本地会话…</p>}
      {hasOlder && (
        <button disabled={loadingOlder} onClick={() => void older()}>
          {loadingOlder ? "正在读取更早消息…" : "加载更早消息"}
        </button>
      )}
      {!loading && !messages.length && (
        <div className="chat-empty">
          <h3>{mode === "research" ? "开始一段研究讨论" : "从文献证据开始"}</h3>
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
        .map((message) => {
          if (message.role === "user")
            return (
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
            );
          const result = (
            <ResultPanel
              message={message}
              current={message.id === latest}
              activeConversation={active}
              retryable={message.id === latest}
              onReconcile={onReconcile}
              onRetry={onRetry}
            />
          );
          return message.is_effective ? (
            <div key={`${message.id}:${message.run_id}`}>{result}</div>
          ) : (
            <details
              className="attempt-audit"
              key={`${message.id}:${message.run_id}`}
            >
              <summary>
                先前尝试 #{message.attempt_number} ·{" "}
                {statusLabels[message.status] ?? message.status}（审计记录）
              </summary>
              {result}
            </details>
          );
        })}
      <div ref={end} />
    </div>
  );
}
