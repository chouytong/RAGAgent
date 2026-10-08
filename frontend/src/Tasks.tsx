import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Conversation, Run, Turn, api } from "./api";
import type {
  Conversation as ConversationType,
  ConversationMode,
  Message as MessageType,
} from "./api";
import { ConfirmAction, emptyFilters } from "./components";
import { MemoryPanel } from "./Memory";
import { ConversationHistory } from "./chat/ConversationHistory";
import { MessageComposer } from "./chat/MessageComposer";
import { MessageList } from "./chat/MessageList";
import {
  activeStatus,
  useConversationMessages,
} from "./chat/useConversationMessages";

function selection(mode: ConversationMode) {
  const match = window.location.hash.match(/^#\/(rag|research)\/([^/?#]+)$/);
  return match?.[1] === mode ? match[2] : "";
}
function navigate(mode: ConversationMode, id = "") {
  window.location.hash = `/${mode}${id ? `/${id}` : ""}`;
}
export function Tasks({ research }: { research: boolean }) {
  const mode: ConversationMode = research ? "research" : "rag";
  const [conversations, setConversations] = useState<ConversationType[]>([]);
  const [selectedId, setSelectedId] = useState(() => selection(mode));
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(emptyFilters);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [rename, setRename] = useState<ConversationType | null>(null);
  const [title, setTitle] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const selectedRef = useRef(selectedId);
  const listSequence = useRef(0);
  const busyRef = useRef(false);
  const pendingSubmission = useRef<{
    content: string;
    filters: string;
    conversationId: string;
    key: string;
  } | null>(null);
  const pendingRetry = useRef<{ messageId: string; key: string } | null>(null);
  const {
    conversation,
    messages,
    loading,
    hasOlder,
    loadingOlder,
    setConversation,
    merge: mergeMessages,
    reset: resetMessages,
    reconcile,
    loadOlder,
  } = useConversationMessages(selectedId, setError);
  selectedRef.current = selectedId;
  const activeRun =
    conversation?.active_run_id ??
    messages.find(
      (message) =>
        message.role === "assistant" &&
        activeStatus(message.run?.status ?? message.status),
    )?.run_id;
  const active = Boolean(activeRun);

  function choose(id: string) {
    selectedRef.current = id;
    setSelectedId(id);
    navigate(mode, id);
    setError("");
    setNotice("");
    setMemoryOpen(false);
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
    async (id: string, changed: string[] = []) => {
      await reconcile(id, changed);
    },
    [reconcile],
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
  const refreshCurrent = useCallback(
    async (messageId?: string) => {
      const id = selectedRef.current;
      if (id) {
        await refreshMessages(id, messageId ? [messageId] : []);
        await refreshList();
      }
    },
    [refreshMessages, refreshList],
  );
  async function createConversation() {
    const created = await api("/api/conversations", Conversation, { mode });
    choose(created.id);
    setConversation(created);
    resetMessages();
    return created;
  }
  async function newChat() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    // Clear the old draft before I/O; preserve text typed while creation waits.
    setQuery("");
    setFilters(emptyFilters);
    pendingSubmission.current = null;
    try {
      await createConversation();
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
        mergeMessages([turn.user_message, turn.assistant_message]);
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
      const turn = await api(
        `/api/conversations/${id}/messages/${message.id}/retry`,
        Turn,
        {
          client_request_id: pending.key,
        },
      );
      pendingRetry.current = null;
      if (selectedRef.current === id) mergeMessages([turn.assistant_message]);
      await refreshMessages(id, [message.id, turn.assistant_message.id]);
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
      resetMessages();
      setConversation(null);
    }
    await refreshList();
  }
  async function clearConversation() {
    const id = selectedRef.current;
    await api(`/api/conversations/${id}/clear`, z.unknown(), {});
    resetMessages();
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
      <ConversationHistory
        conversations={conversations}
        selectedId={selectedId}
        mode={mode}
        busy={busy}
        hasMore={hasMore}
        onNew={newChat}
        onChoose={choose}
        onRename={(item) => {
          setRename(item);
          setTitle(item.title);
        }}
        onRemove={removeConversation}
        onMore={() =>
          void refreshList(undefined, true, historyOffset + 50).catch((e) =>
            setError(String(e)),
          )
        }
      />
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
        <MessageList
          messages={messages}
          selectedId={selectedId}
          mode={mode}
          loading={loading}
          active={active}
          busy={busy}
          hasOlder={hasOlder}
          loadingOlder={loadingOlder}
          loadOlder={loadOlder}
          onReconcile={refreshCurrent}
          onRetry={retry}
        />
        <MessageComposer
          mode={mode}
          selectedId={selectedId}
          query={query}
          setQuery={setQuery}
          filters={filters}
          setFilters={setFilters}
          active={active}
          busy={busy}
          loading={loading}
          submit={submit}
          cancel={cancel}
        />
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
