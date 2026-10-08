import { useRef } from "react";
import type { ConversationMode } from "../api";
import { FilterEditor } from "../components";
import type { Filters } from "../components";

export function MessageComposer({
  mode,
  selectedId,
  query,
  setQuery,
  filters,
  setFilters,
  active,
  busy,
  loading,
  submit,
  cancel,
}: {
  mode: ConversationMode;
  selectedId: string;
  query: string;
  setQuery: (value: string) => void;
  filters: Filters;
  setFilters: (filters: Filters) => void;
  active: boolean;
  busy: boolean;
  loading: boolean;
  submit: () => Promise<void>;
  cancel: () => Promise<void>;
}) {
  const composing = useRef(false);
  return (
    <form
      className="chat-composer"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <FilterEditor key={selectedId} value={filters} onChange={setFilters} />
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
          mode === "research"
            ? "输入研究问题或继续上一轮讨论…"
            : "输入文献问题或追问…"
        }
      />
      <div className="composer-actions">
        <small>Enter 发送 · Shift+Enter 换行 · 摘要与记忆不是科研证据</small>
        {active ? (
          <button type="button" disabled={busy} onClick={() => void cancel()}>
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
  );
}
