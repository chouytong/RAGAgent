import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Conversation, Message, api } from "../api";
import type {
  Conversation as ConversationType,
  Message as MessageType,
} from "../api";

const PAGE_SIZE = 50;
export const activeStatus = (status?: string) =>
  status === "queued" || status === "running";

function mergeMessages(previous: MessageType[], incoming: MessageType[]) {
  const byId = new Map(previous.map((message) => [message.id, message]));
  for (const message of incoming) {
    const old = byId.get(message.id);
    // A late queued snapshot cannot roll back an already published terminal answer.
    if (old && !activeStatus(old.status) && activeStatus(message.status))
      continue;
    if (old && old.updated_at > message.updated_at) continue;
    byId.set(message.id, message);
  }
  return [...byId.values()].sort((a, b) => a.ordinal - b.ordinal);
}

/** One latest page, explicit upward paging, and targeted recovery; never full-history polling. */
export function useConversationMessages(
  selectedId: string,
  onError: (error: string) => void,
) {
  const [conversation, setConversation] = useState<ConversationType | null>(
    null,
  );
  const [messages, setMessages] = useState<MessageType[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasOlder, setHasOlder] = useState(false);
  const selected = useRef(selectedId);
  const generation = useRef(0);
  const current = useRef(messages);
  const errorCallback = useRef(onError);
  selected.current = selectedId;
  current.current = messages;
  errorCallback.current = onError;

  const merge = useCallback((incoming: MessageType[]) => {
    // Keep the cursor current even before React renders a newly created turn.
    current.current = mergeMessages(current.current, incoming);
    setMessages(current.current);
  }, []);
  const reset = useCallback(() => {
    current.current = [];
    setMessages([]);
    setHasOlder(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const epoch = ++generation.current;
    reset();
    setConversation(null);
    setLoading(Boolean(selectedId));
    setLoadingOlder(false);
    if (selectedId) {
      void Promise.all([
        api(
          `/api/conversations/${selectedId}`,
          Conversation,
          undefined,
          "GET",
          controller.signal,
        ),
        api(
          `/api/conversations/${selectedId}/messages?limit=${PAGE_SIZE}`,
          z.array(Message),
          undefined,
          "GET",
          controller.signal,
        ),
      ])
        .then(([nextConversation, page]) => {
          if (controller.signal.aborted || epoch !== generation.current) return;
          setConversation(nextConversation);
          merge(page);
          setHasOlder(page.length === PAGE_SIZE && (page[0]?.ordinal ?? 0) > 0);
        })
        .catch((error) => {
          if (!controller.signal.aborted && epoch === generation.current)
            errorCallback.current(String(error));
        })
        .finally(() => {
          if (!controller.signal.aborted && epoch === generation.current)
            setLoading(false);
        });
    }
    return () => controller.abort();
  }, [selectedId, merge, reset]);

  const reconcile = useCallback(
    async (id: string, changedMessageIds: string[] = []) => {
      if (!id || selected.current !== id) return;
      const epoch = generation.current;
      const highest = current.current.at(-1)?.ordinal;
      const pending = current.current
        .filter(
          (message) =>
            message.role === "assistant" && activeStatus(message.status),
        )
        .map((message) => message.id);
      const targets = [...new Set([...pending, ...changedMessageIds])];
      const [nextConversation, updates] = await Promise.all([
        api(`/api/conversations/${id}`, Conversation),
        Promise.all(
          targets.map((messageId) =>
            api(`/api/conversations/${id}/messages/${messageId}`, Message),
          ),
        ),
      ]);
      if (selected.current !== id || generation.current !== epoch) return;
      setConversation(nextConversation);
      merge(updates);
      let cursor = highest;
      for (;;) {
        const page = await api(
          `/api/conversations/${id}/messages?limit=${PAGE_SIZE}${cursor === undefined ? "" : `&after_ordinal=${cursor}`}`,
          z.array(Message),
        );
        if (selected.current !== id || generation.current !== epoch) return;
        merge(page);
        if (cursor === undefined)
          setHasOlder(page.length === PAGE_SIZE && (page[0]?.ordinal ?? 0) > 0);
        if (page.length < PAGE_SIZE || !page.length) return;
        const next = page.at(-1)!.ordinal;
        if (cursor !== undefined && next <= cursor)
          throw new Error("invalid_message_cursor");
        cursor = next;
      }
    },
    [merge],
  );

  const loadOlder = useCallback(async () => {
    const id = selected.current;
    const before = current.current[0]?.ordinal;
    if (!id || before === undefined || loadingOlder || !hasOlder) return;
    const epoch = generation.current;
    setLoadingOlder(true);
    try {
      const page = await api(
        `/api/conversations/${id}/messages?limit=${PAGE_SIZE}&before_ordinal=${before}`,
        z.array(Message),
      );
      if (selected.current !== id || generation.current !== epoch) return;
      merge(page);
      setHasOlder(page.length === PAGE_SIZE && (page[0]?.ordinal ?? 0) > 0);
    } catch (error) {
      if (selected.current === id && generation.current === epoch)
        errorCallback.current(String(error));
    } finally {
      if (selected.current === id && generation.current === epoch)
        setLoadingOlder(false);
    }
  }, [hasOlder, loadingOlder, merge]);

  useEffect(() => {
    const recover = () => {
      if (document.visibilityState === "hidden" || !selected.current) return;
      void reconcile(selected.current).catch((error) =>
        errorCallback.current(String(error)),
      );
    };
    window.addEventListener("focus", recover);
    window.addEventListener("online", recover);
    document.addEventListener("visibilitychange", recover);
    return () => {
      window.removeEventListener("focus", recover);
      window.removeEventListener("online", recover);
      document.removeEventListener("visibilitychange", recover);
    };
  }, [reconcile]);

  return {
    conversation,
    messages,
    loading,
    hasOlder,
    loadingOlder,
    setConversation,
    merge,
    reset,
    reconcile,
    loadOlder,
  };
}
