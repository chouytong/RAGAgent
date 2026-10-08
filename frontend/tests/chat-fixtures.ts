import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";

// Explicit MOCK HTTP/SSE persistence fixtures. These verify UI contracts, not
// real database durability, retrieval quality, model inference or desktop GUI.
export const run = {
  id: "c82d3363-dcbb-45df-b81e-8bfaed0992c7",
  kind: "rag",
  status: "queued",
  trace_id: "test-trace",
  error_code: null,
  result: null,
};
export const evidence = {
  evidence_id: "d5871625-f202-46f6-a2a2-cfba7d9d6994",
  paper: { paper_id: "paper-1", title: "MOCK evidence paper" },
  chunk_id: "chunk-1",
  section_path: "Results",
  page_start: 7,
  page_end: 8,
  quote: "Exact source text from the MOCK fixture.",
};
export const completed = {
  ...run,
  status: "completed",
  result: {
    answer: `MOCK supported statement. [E:${evidence.evidence_id}]`,
    reranked_evidence: [evidence],
  },
};
export const done = (result: object) =>
  `event: done\ndata: ${JSON.stringify(result)}\n\n`;
type MockRun = {
  id: string;
  kind: string;
  status: string;
  trace_id: string;
  error_code: string | null;
  result: Record<string, unknown> | null;
};
type MockConversation = {
  id: string;
  title: string;
  mode: string;
  archived: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  active_run_id: string | null;
};
type MockMessage = {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  ordinal: number;
  run_id: string | null;
  retry_of_message_id: string | null;
  attempt_number: number;
  is_effective: boolean;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  run: MockRun | null;
};
const time = "2026-10-05T00:00:00Z";
export async function setupChat(
  page: Page,
  options: {
    result?: MockRun;
    onSend?: (body: Record<string, unknown>) => void;
    sendError?: string;
    holdStream?: boolean;
  } = {},
) {
  const conversations = new Map<string, MockConversation>();
  const messages = new Map<string, MockMessage[]>();
  const runs = new Map<string, MockRun>();
  const memories = new Map<string, Record<string, unknown>[]>();
  const summaries = new Map<string, Record<string, unknown> | null>();
  const requests = new Map<
    string,
    {
      conversation: MockConversation;
      user_message: MockMessage;
      assistant_message: MockMessage;
      run: MockRun;
    }
  >();
  const messageOffsets: number[] = [];
  const messageQueries: string[] = [];
  const fullRunRequests: string[] = [];
  const sentBodies: Record<string, unknown>[] = [];
  let sends = 0;
  let retries = 0;
  let cancels = 0;
  function create(title = "New chat", mode = "rag") {
    const id = randomUUID();
    const conversation = {
      id,
      title,
      mode,
      archived: false,
      metadata: { auto_title: title === "New chat" },
      created_at: time,
      updated_at: time,
      active_run_id: null,
    };
    conversations.set(id, conversation);
    messages.set(id, []);
    memories.set(id, []);
    summaries.set(id, null);
    return conversation;
  }
  function makeMessage(
    conversation: MockConversation,
    role: string,
    content: string,
    currentRun: MockRun | null,
  ) {
    const history = messages.get(conversation.id)!;
    if (currentRun) runs.set(currentRun.id, currentRun);
    const message = {
      id: randomUUID(),
      conversation_id: conversation.id,
      role,
      content,
      ordinal: history.length,
      run_id: currentRun?.id ?? null,
      retry_of_message_id: null as string | null,
      attempt_number: 1,
      is_effective: true,
      status:
        role === "user" ? "completed" : (currentRun?.status ?? "completed"),
      metadata:
        currentRun?.status === "completed"
          ? { presentation: presentation(currentRun) }
          : {},
      created_at: time,
      updated_at: time,
      run: currentRun,
    };
    history.push(message);
    return message;
  }
  // MOCK projection represents the bounded wire schema, not evidence verification.
  function presentation(item: MockRun) {
    const pool = (item.result?.reranked_evidence ??
      item.result?.evidence_pool ??
      []) as (typeof evidence)[];
    return {
      limitations: item.result?.limitations ?? [],
      citation_refs: pool.map((source) => ({
        evidence_id: source.evidence_id,
        page_start: source.page_start,
        page_end: source.page_end,
      })),
    };
  }
  function lightRun(item: MockRun | null) {
    if (!item) return null;
    const { result: _detail, ...summary } = item;
    return summary;
  }
  function lightMessage(item: MockMessage) {
    return { ...item, run: lightRun(item.run) };
  }
  function lightTurn(item: {
    conversation: MockConversation;
    user_message: MockMessage;
    assistant_message: MockMessage;
    run: MockRun;
  }) {
    return {
      ...item,
      user_message: lightMessage(item.user_message),
      assistant_message: lightMessage(item.assistant_message),
      run: lightRun(item.run),
    };
  }
  function completeRun(id: string, custom?: MockRun) {
    const current = runs.get(id)!;
    const next = {
      ...(custom ?? options.result ?? completed),
      id,
      kind: current.kind,
    };
    runs.set(id, next);
    for (const conversation of conversations.values()) {
      for (const message of messages.get(conversation.id)!)
        if (message.run_id === id) {
          message.run = next;
          if (message.role === "assistant") {
            message.status = next.status;
            if (next.status === "completed")
              message.metadata = {
                ...message.metadata,
                presentation: presentation(next),
              };
            message.content = ["completed", "insufficient_evidence"].includes(
              next.status,
            )
              ? String(next.result?.answer ?? next.result?.draft_report ?? "")
              : "";
          }
        }
      if (conversation.active_run_id === id) conversation.active_run_id = null;
    }
    return next;
  }
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/api/ready", (route) =>
    route.fulfill({ json: { status: "ready" } }),
  );
  await page.route("**/api/papers?*", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/conversations**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.split("/").filter(Boolean).slice(2);
    const method = route.request().method();
    const body =
      method === "GET" || method === "DELETE"
        ? {}
        : (route.request().postDataJSON() as Record<string, unknown>);
    const reply = (json: unknown, status = 200) =>
      route.fulfill({ json, status });
    if (!path.length) {
      if (method === "POST")
        return reply(create("New chat", String(body.mode)), 201);
      const items = [...conversations.values()].filter(
        (item) =>
          !url.searchParams.get("mode") ||
          item.mode === url.searchParams.get("mode"),
      );
      const offset = Number(url.searchParams.get("offset") ?? 0),
        limit = Number(url.searchParams.get("limit") ?? 50);
      return reply(items.slice(offset, offset + limit));
    }
    const conversation = conversations.get(path[0]);
    if (!conversation) return reply({ detail: "conversation_not_found" }, 404);
    if (path.length === 1) {
      if (method === "PATCH") {
        conversation.title = String(body.title);
        return reply(conversation);
      }
      if (method === "DELETE") {
        conversations.delete(conversation.id);
        messages.delete(conversation.id);
        memories.delete(conversation.id);
        summaries.delete(conversation.id);
        return reply({ status: "deleted" });
      }
      return reply(conversation);
    }
    if (path[1] === "clear") {
      messages.set(conversation.id, []);
      memories.set(conversation.id, []);
      summaries.set(conversation.id, null);
      conversation.title = "New chat";
      return reply(conversation);
    }
    if (path[1] === "summary") {
      if (method === "DELETE") summaries.set(conversation.id, null);
      return reply(
        method === "DELETE"
          ? { status: "deleted" }
          : summaries.get(conversation.id),
      );
    }
    if (path[1] === "memory") {
      memories.set(conversation.id, []);
      summaries.set(conversation.id, null);
      return reply({ status: "deleted" });
    }
    if (path[1] === "memories") {
      if (method === "POST") {
        const memory = {
          ...body,
          id: randomUUID(),
          conversation_id: conversation.id,
          filters: body.filters ?? null,
          metadata: {},
          created_at: time,
          updated_at: time,
        };
        memories.get(conversation.id)!.push(memory);
        return reply(memory, 201);
      }
      if (method === "DELETE") {
        memories.set(
          conversation.id,
          memories.get(conversation.id)!.filter((item) => item.id !== path[2]),
        );
        return reply({ status: "deleted" });
      }
      return reply(memories.get(conversation.id));
    }
    if (path[1] === "messages") {
      const history = messages.get(conversation.id)!;
      if (method === "GET") {
        messageQueries.push(url.search);
        if (path.length === 3) {
          const item = history.find((message) => message.id === path[2]);
          return item
            ? reply(lightMessage(item))
            : reply({ detail: "message_not_found" }, 404);
        }
        const limit = Number(url.searchParams.get("limit") ?? 50);
        if (url.searchParams.has("offset")) {
          const offset = Number(url.searchParams.get("offset"));
          messageOffsets.push(offset);
          return reply(history.slice(offset, offset + limit).map(lightMessage));
        }
        if (url.searchParams.has("after_ordinal"))
          return reply(
            history
              .filter(
                (item) =>
                  item.ordinal > Number(url.searchParams.get("after_ordinal")),
              )
              .slice(0, limit)
              .map(lightMessage),
          );
        const candidates = url.searchParams.has("before_ordinal")
          ? history.filter(
              (item) =>
                item.ordinal < Number(url.searchParams.get("before_ordinal")),
            )
          : history;
        return reply(candidates.slice(-limit).map(lightMessage));
      }
      const key = String(body.client_request_id);
      if (requests.has(key)) return reply(lightTurn(requests.get(key)!), 202);
      if (options.sendError && path.length === 2)
        return reply({ detail: options.sendError }, 503);
      if (path[3] === "retry") retries += 1;
      else {
        sends += 1;
        sentBodies.push(body);
        options.onSend?.(body);
      }
      const id = runs.size ? randomUUID() : run.id;
      const currentRun = { ...run, id, kind: conversation.mode };
      runs.set(id, currentRun);
      conversation.active_run_id = id;
      if (conversation.title === "New chat")
        conversation.title = String(body.content ?? history[0]?.content).slice(
          0,
          80,
        );
      const user =
        path[3] === "retry"
          ? history.find((item) => item.role === "user")!
          : makeMessage(conversation, "user", String(body.content), currentRun);
      const assistant = makeMessage(conversation, "assistant", "", currentRun);
      if (path[3] === "retry") {
        const old = history.find((item) => item.id === path[2])!;
        old.is_effective = false;
        assistant.retry_of_message_id = old.id;
        assistant.attempt_number = old.attempt_number + 1;
      }
      const turn = {
        conversation,
        user_message: user,
        assistant_message: assistant,
        run: currentRun,
      };
      requests.set(key, turn);
      return reply(lightTurn(turn), 202);
    }
    return reply({ detail: "mock_route_not_found" }, 404);
  });
  await page.route("**/api/runs/*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    fullRunRequests.push(id);
    const item = runs.get(id);
    return route.fulfill({
      json: item ?? { detail: "run_not_found" },
      status: item ? 200 : 404,
    });
  });
  await page.route("**/api/runs/*/cancel", (route) => {
    cancels += 1;
    const id = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({
      json: completeRun(id, {
        ...runs.get(id)!,
        status: "cancelled",
        error_code: "user_cancelled",
        result: null,
      }),
    });
  });
  if (!options.holdStream)
    await page.route("**/api/runs/*/events?*", (route) => {
      const id = new URL(route.request().url()).pathname.split("/")[3];
      return route.fulfill({
        contentType: "text/event-stream",
        body: done(completeRun(id)),
      });
    });
  return {
    conversations,
    messages,
    runs,
    memories,
    summaries,
    requests,
    messageOffsets,
    messageQueries,
    fullRunRequests,
    sentBodies,
    create,
    makeMessage,
    completeRun,
    get sends() {
      return sends;
    },
    get retries() {
      return retries;
    },
    get cancels() {
      return cancels;
    },
  };
}
