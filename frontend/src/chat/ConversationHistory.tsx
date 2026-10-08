import type { Conversation, ConversationMode } from "../api";
import { ConfirmAction } from "../components";

export function ConversationHistory({
  conversations,
  selectedId,
  mode,
  busy,
  hasMore,
  onNew,
  onChoose,
  onRename,
  onRemove,
  onMore,
}: {
  conversations: Conversation[];
  selectedId: string;
  mode: ConversationMode;
  busy: boolean;
  hasMore: boolean;
  onNew: () => Promise<void>;
  onChoose: (id: string) => void;
  onRename: (conversation: Conversation) => void;
  onRemove: (id: string) => Promise<void>;
  onMore: () => void;
}) {
  return (
    <aside className="conversation-sidebar" aria-label="Conversation History">
      <div className="sidebar-heading">
        <h2>{mode === "research" ? "Research Chat" : "RAG Chat"}</h2>
        <button disabled={busy} onClick={() => void onNew()}>
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
              onClick={() => onChoose(item.id)}
            >
              <strong>{item.title}</strong>
              <small>
                {item.mode === "research" ? "Research · Multi-Agent" : "RAG"} ·{" "}
                {new Date(item.updated_at).toLocaleDateString()}
              </small>
            </button>
            <div className="conversation-actions">
              <button
                aria-label={`重命名 ${item.title}`}
                disabled={busy}
                onClick={() => onRename(item)}
              >
                重命名
              </button>
              <ConfirmAction
                label={`删除会话 ${item.title}`}
                description="真正删除本地会话、消息、相关任务事件、摘要和记忆。正在执行的任务会被取消；已发生的供应商费用无法撤销。"
                disabled={busy}
                onConfirm={() => onRemove(item.id)}
              />
            </div>
          </li>
        ))}
      </ul>
      {!conversations.length && (
        <p className="empty-history">还没有会话。新建会话或直接发送问题。</p>
      )}
      {hasMore && (
        <button disabled={busy} onClick={onMore}>
          加载更早会话
        </button>
      )}
    </aside>
  );
}
