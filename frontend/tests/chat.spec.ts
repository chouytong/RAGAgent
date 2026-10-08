import { expect, test } from "@playwright/test";
import { completed, done, evidence, run, setupChat } from "./chat-fixtures";

test("text entered while New Chat creation waits survives the response", async ({
  page,
}) => {
  const chat = await setupChat(page);
  chat.create("MOCK existing chat");
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /^MOCK existing chat/ }),
  ).toBeVisible();
  await page.route("**/api/conversations", async (route) => {
    if (route.request().method() === "POST")
      await new Promise((resolve) => setTimeout(resolve, 350));
    await route.fallback();
  });
  await page.getByRole("button", { name: "New Chat", exact: true }).click();
  await page.getByLabel("研究问题").fill("MOCK draft typed during creation");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("研究问题")).toHaveValue(
    "MOCK draft typed during creation",
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(chat.sentBodies[0]?.content).toBe("MOCK draft typed during creation");
});

test("claim span uses original Unicode text and PDF target page remains explicit", async ({
  page,
}) => {
  const text = "😀前言方法达到23%。后续内容";
  const original = {
    ...evidence,
    content: text,
    quote: text,
    span_start: 0,
    span_end: Array.from(text).length,
  };
  const detailed = {
    ...completed,
    result: {
      ...completed.result,
      reranked_evidence: [original],
      citation_validation: {
        supported_pairs: [
          {
            claim_id: "metric",
            evidence_id: original.evidence_id,
            supporting_span_start: 3,
            supporting_span_end: 11,
          },
        ],
      },
    },
  };
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK span");
  chat.makeMessage(conversation, "assistant", detailed.result.answer, detailed);
  await page.goto(`/#/rag/${conversation.id}`);
  await page.getByRole("button", { name: "文献 · p.7" }).first().click();
  await expect(page.getByLabel("逐结论支持片段")).toContainText(
    "方法达到23%。",
  );
  await expect(page.getByLabel("主引用原文", { exact: true })).toHaveText(text);
  await expect(
    page.getByRole("link", { name: "打开原始 PDF", exact: true }),
  ).toHaveAttribute(
    "href",
    `/api/papers/${original.paper.paper_id}/pdf#page=7`,
  );
  await expect(page.getByRole("note")).toContainText("请手动跳转到此页");
});

test("latest history stays lightweight and citations load one Run only when opened", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK heavy history");
  for (let index = 0; index < 60; index += 1) {
    const item = { ...completed, id: crypto.randomUUID() };
    chat.makeMessage(conversation, "user", `MOCK query ${index}`, item);
    chat.makeMessage(conversation, "assistant", item.result.answer, item);
  }
  await page.goto(`/#/rag/${conversation.id}`);
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(25);
  expect(chat.fullRunRequests).toEqual([]);
  await page.getByRole("button", { name: "文献 · p.7" }).first().click();
  await expect(page.getByLabel("主引用原文", { exact: true })).toHaveText(
    evidence.quote,
  );
  expect(chat.fullRunRequests).toHaveLength(1);
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "文献 · p.7" }).first().click();
  await expect(page.getByLabel("主引用原文", { exact: true })).toHaveText(
    evidence.quote,
  );
  expect(chat.fullRunRequests).toHaveLength(1);
  expect(chat.messageOffsets).toEqual([]);
});

test("healthy SSE prevents periodic history polling and lost completion recovers the same message", async ({
  page,
}) => {
  const chat = await setupChat(page, { holdStream: true });
  await page.addInitScript(() => {
    class OpenStream {
      static instances: OpenStream[] = [];
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(_url: string) {
        OpenStream.instances.push(this);
        setTimeout(() => this.onopen?.(), 0);
      }
      addEventListener() {}
      close() {}
    }
    Object.assign(window, { EventSource: OpenStream });
  });
  await page.goto("/#/rag");
  await page.getByLabel("研究问题").fill("MOCK live query");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "排队中", exact: true }),
  ).toBeVisible();
  await expect.poll(() => chat.messageQueries.length).toBeGreaterThan(0);
  await page.waitForTimeout(100);
  const baselineRequests = chat.messageQueries.length;
  await page.waitForTimeout(4500); // Covers the removed four-second normal-history timer.
  expect(chat.messageQueries).toHaveLength(baselineRequests);
  expect(chat.fullRunRequests).toEqual([]);
  const id = [...chat.runs.keys()][0];
  const message = [...chat.messages.values()][0].find(
    (item) => item.role === "assistant",
  )!;
  chat.completeRun(id);
  await page.evaluate(() => {
    const constructor = window.EventSource as unknown as {
      instances: { onerror: (() => void) | null }[];
    };
    constructor.instances.at(-1)?.onerror?.();
  });
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(1);
  expect(chat.fullRunRequests).toEqual([]);
  expect(
    chat.messageQueries.some((query) => query.includes("after_ordinal")),
  ).toBe(true);
  await expect(page.locator(`[data-message-id="${message.id}"]`)).toContainText(
    "MOCK supported statement",
  );
  expect(chat.messageOffsets).toEqual([]);
});

// MOCK wire-contract fixtures are explicit. Real PostgreSQL persistence,
// follow-up retrieval and memory isolation are covered by backend tests.
test("new chat, follow-up, conversation switching, reload and hard deletion preserve the selected history", async ({
  page,
}) => {
  const chat = await setupChat(page);
  await page.goto("/#/rag");
  await page.getByRole("button", { name: "New Chat", exact: true }).click();
  await expect(
    page.getByLabel("Conversation History").getByRole("listitem"),
  ).toHaveCount(1);
  await page
    .getByLabel("研究问题")
    .fill("What datasets were used in these MOCK papers?");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toBeVisible();
  const first = [...chat.conversations.values()][0];
  await page
    .getByLabel("研究问题")
    .fill("Which one has the largest sample size?");
  await page.getByLabel("研究问题").press("Enter");
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(2);
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toHaveCount(2);
  expect(chat.sentBodies[1]?.content).toBe(
    "Which one has the largest sample size?",
  );
  await expect(page.getByRole("button", { name: "文献 · p.7" })).toHaveCount(2);
  await page.getByRole("button", { name: "New Chat", exact: true }).click();
  await page.getByLabel("研究问题").fill("A different MOCK research topic");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toHaveCount(1);
  await page
    .getByRole("button", {
      name: /^What datasets were used in these MOCK papers\?/,
    })
    .click();
  await expect(page.getByLabel("User 消息")).toHaveCount(2);
  await page.reload();
  await expect(page).toHaveURL(new RegExp(first.id));
  await expect(page.getByLabel("User 消息")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "文献 · p.7" })).toHaveCount(2);
  expect(await page.evaluate(() => Object.keys(localStorage))).not.toContain(
    "rag_last_run",
  );
  await page.getByLabel("研究问题").fill("What methodology did it use?");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(3);
  await page
    .getByRole("button", { name: `删除会话 ${first.title}`, exact: true })
    .click();
  await page
    .getByRole("button", { name: `确认删除会话 ${first.title}`, exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: /^What datasets were used in these MOCK papers\?/,
    }),
  ).toHaveCount(0);
  expect(chat.conversations.has(first.id)).toBe(false);
  expect(chat.messages.has(first.id)).toBe(false);
  await page.reload();
  await expect(
    page.getByLabel("Conversation History").getByRole("listitem"),
  ).toHaveCount(1);
});

test("rename changes the persisted title and survives reload", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK existing topic");
  await page.goto(`/#/rag/${conversation.id}`);
  await page
    .getByRole("button", { name: "重命名 MOCK existing topic" })
    .click();
  await page.getByLabel("会话标题").fill("MOCK renamed research topic");
  await page.getByRole("button", { name: "保存标题" }).click();
  await expect(
    page.getByRole("heading", {
      name: "MOCK renamed research topic",
      exact: true,
    }),
  ).toBeVisible();
  expect(chat.conversations.get(conversation.id)?.title).toBe(
    "MOCK renamed research topic",
  );
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "MOCK renamed research topic",
      exact: true,
    }),
  ).toBeVisible();
});

test("Markdown renders headings, tables and code while citations retain exact source and remote images stay blocked", async ({
  page,
}) => {
  let imageRequests = 0;
  await page.route("https://privacy.invalid/**", (route) => {
    imageRequests += 1;
    return route.abort();
  });
  await setupChat(page, {
    result: {
      ...completed,
      result: {
        ...completed.result,
        answer: `# MOCK analysis\n\n**Supported** statement. [E:${evidence.evidence_id}]\n\n\`\`\`python\nprint("MOCK code")\n\`\`\`\n\n| Dataset | N |\n| --- | --- |\n| MOCK-A | 12 |\n\n![external image](https://privacy.invalid/private-context)`,
      },
    },
  });
  await page.goto("/#/rag");
  await page.getByLabel("研究问题").fill("MOCK markdown question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "MOCK analysis", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".report strong")).toHaveText("Supported");
  await expect(page.locator(".report pre code")).toHaveText(
    'print("MOCK code")\n',
  );
  await expect(page.locator(".report table")).toContainText("MOCK-A");
  await expect(page.locator(".report img")).toHaveCount(0);
  expect(imageRequests).toBe(0);
  await page.getByRole("button", { name: "文献 · p.7" }).click();
  await expect(
    page
      .getByRole("dialog", { name: "引用原文" })
      .getByLabel("主引用原文", { exact: true }),
  ).toHaveText(evidence.quote);
});

test("Research execution plan, agent trace and reviewer are collapsed outside the final chat body", async ({
  page,
}) => {
  const research = {
    ...completed,
    kind: "research",
    result: {
      draft_report: "# MOCK reviewed report\n\nMOCK supported result.",
      evidence_pool: [],
      research_plan: {
        objective: "MOCK objective",
        subtasks: [{ task_id: "MOCK-task-1", question: "MOCK task question" }],
      },
      review_result: { decision: "PASS", issues: [] },
    },
  };
  const chat = await setupChat(page, { result: research });
  await page.route("**/api/runs/*/events?*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({
      contentType: "text/event-stream",
      body: `id: 1\nevent: execution\ndata: ${JSON.stringify({ node: "analysis", payload: { draft: "MOCK intermediate draft" }, time: "2026-10-05T00:00:00Z" })}\n\n${done(chat.completeRun(id, research))}`,
    });
  });
  await page.goto("/#/research");
  await page
    .getByLabel("研究问题")
    .fill("Compare the MOCK domain adaptation methods");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "MOCK reviewed report" }),
  ).toBeVisible();
  await expect(
    page.getByText("MOCK-task-1", { exact: false }),
  ).not.toBeVisible();
  await expect(
    page.getByText("MOCK intermediate draft", { exact: false }),
  ).not.toBeVisible();
  await page.getByText("执行详情 · Multi-Agent", { exact: true }).click();
  await page.getByText("Supervisor Plan", { exact: true }).click();
  await expect(page.getByText('"MOCK-task-1"', { exact: false })).toBeVisible();
  await page.getByText("Reviewer Result", { exact: true }).click();
  await expect(page.getByText('"PASS"', { exact: false })).toBeVisible();
  await page
    .getByText("执行详情（可能包含尚未审核的草稿）", { exact: true })
    .first()
    .click();
  await expect(
    page.getByText('"MOCK intermediate draft"', { exact: false }),
  ).toBeVisible();
  await expect(page.locator(".report")).not.toContainText(
    "MOCK intermediate draft",
  );
});

test("summary and structured memories can be viewed, added, deleted and cleared as local records", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK memory topic");
  chat.summaries.set(conversation.id, {
    conversation_id: conversation.id,
    content: "MOCK earlier discussion summary, not scientific evidence",
    through_ordinal: 8,
    version: 1,
    source_message_ids: [],
    metadata: {},
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
  });
  chat.memories.set(conversation.id, [
    {
      id: "7c14b0cb-76c5-4f6e-9aac-54d67f1ad508",
      conversation_id: conversation.id,
      kind: "goal",
      key: "MOCK goal",
      content: "Compare post-2023 papers",
      filters: null,
      metadata: {},
      created_at: conversation.created_at,
      updated_at: conversation.updated_at,
    },
  ]);
  await page.goto(`/#/rag/${conversation.id}`);
  await page.getByRole("button", { name: "会话记忆", exact: true }).click();
  const panel = page.getByLabel("会话记忆管理");
  await expect(panel).toContainText(
    "MOCK earlier discussion summary, not scientific evidence",
  );
  await expect(panel).toContainText("科研事实必须重新检索文献并验证引用");
  await panel.getByLabel("记忆类型").selectOption("term");
  await panel.getByLabel("记忆名称（可选）").fill("MOCK alias");
  await panel
    .getByLabel("记忆内容")
    .fill("The second dataset refers to MOCK-B");
  await panel.getByRole("button", { name: "添加本地记忆" }).click();
  await expect(panel).toContainText("Structured Memory (2)");
  expect(chat.memories.get(conversation.id)).toHaveLength(2);
  await panel
    .getByRole("button", { name: "删除记忆 MOCK alias", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认删除记忆 MOCK alias", exact: true })
    .click();
  await expect(panel).toContainText("Structured Memory (1)");
  expect(chat.memories.get(conversation.id)).toHaveLength(1);
  await panel.getByRole("button", { name: "删除摘要", exact: true }).click();
  await page.getByRole("button", { name: "确认删除摘要", exact: true }).click();
  await expect(panel).toContainText("暂无摘要");
  expect(chat.summaries.get(conversation.id)).toBeNull();
  expect(chat.memories.get(conversation.id)).toHaveLength(1);
  await panel
    .getByRole("button", { name: "Clear Conversation Memory", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认Clear Conversation Memory", exact: true })
    .click();
  await expect(panel).toContainText("暂无摘要");
  await expect(panel).toContainText("Structured Memory (0)");
  expect(chat.summaries.get(conversation.id)).toBeNull();
  expect(chat.memories.get(conversation.id)).toHaveLength(0);
});

test("Clear Conversation removes messages, summary and memory while retaining the conversation", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK clear history");
  chat.makeMessage(conversation, "user", "MOCK stored user message", null);
  chat.makeMessage(
    conversation,
    "assistant",
    completed.result.answer,
    completed,
  );
  chat.runs.set(completed.id, completed);
  await page.goto(`/#/rag/${conversation.id}`);
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Clear Conversation", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认Clear Conversation", exact: true })
    .click();
  await expect(page.getByLabel("User 消息")).toHaveCount(0);
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(0);
  expect(chat.conversations.has(conversation.id)).toBe(true);
  expect(chat.messages.get(conversation.id)).toHaveLength(0);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "New chat", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(0);
});

test("successive constraint memories reset displayed filters and submit only their visible values", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK explicit constraints");
  await page.goto(`/#/rag/${conversation.id}`);
  await page.getByRole("button", { name: "会话记忆", exact: true }).click();
  const panel = page.getByLabel("会话记忆管理");
  await panel.getByLabel("记忆类型").selectOption("constraint");
  await panel
    .getByText("文献过滤条件（同字段 OR，不同字段 AND）", { exact: true })
    .click();
  await panel
    .getByLabel("paper_ids", { exact: true })
    .fill("87e60a23-e0e8-4c7b-aeb1-c590098583be");
  await panel
    .getByLabel("authors", { exact: true })
    .pressSequentially("Alice Smith;Bob Jones;");
  await expect(panel.getByLabel("authors", { exact: true })).toHaveValue(
    "Alice Smith;Bob Jones;",
  );
  await panel.getByLabel("datasets", { exact: true }).fill("MOCK-A;MOCK-B");
  await panel.getByLabel("记忆内容").fill("MOCK first explicit constraint");
  await panel.getByRole("button", { name: "添加本地记忆" }).click();
  await expect(panel).toContainText("Structured Memory (1)");
  expect(chat.memories.get(conversation.id)?.[0]?.filters).toMatchObject({
    paper_ids: ["87e60a23-e0e8-4c7b-aeb1-c590098583be"],
    authors: ["Alice Smith", "Bob Jones"],
    datasets: ["MOCK-A", "MOCK-B"],
  });
  for (const name of ["paper_ids", "authors", "datasets"])
    await expect(panel.getByLabel(name, { exact: true })).toHaveValue("");
  await panel.getByLabel("datasets", { exact: true }).fill("MOCK-C");
  await panel.getByLabel("记忆内容").fill("MOCK second explicit constraint");
  await panel.getByRole("button", { name: "添加本地记忆" }).click();
  await expect(panel).toContainText("Structured Memory (2)");
  expect(chat.memories.get(conversation.id)?.[1]?.filters).toMatchObject({
    paper_ids: [],
    authors: [],
    datasets: ["MOCK-C"],
  });
});

test("New Chat resets visible filters and does not carry the previous query restrictions", async ({
  page,
}) => {
  const chat = await setupChat(page);
  await page.goto("/#/rag");
  await page.getByRole("button", { name: "New Chat", exact: true }).click();
  const composer = page.locator(".chat-composer");
  await composer
    .getByText("文献过滤条件（同字段 OR，不同字段 AND）", { exact: true })
    .click();
  await composer.getByLabel("authors", { exact: true }).fill("MOCK Alice");
  await composer.getByLabel("datasets", { exact: true }).fill("MOCK-A");
  await composer.getByLabel("year_start", { exact: true }).fill("2023");
  await page.getByLabel("研究问题").fill("MOCK previous constrained question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toBeVisible();
  expect(chat.sentBodies[0]?.filters).toMatchObject({
    authors: ["MOCK Alice"],
    datasets: ["MOCK-A"],
    year_start: 2023,
  });
  await page.getByRole("button", { name: "New Chat", exact: true }).click();
  await composer
    .getByText("文献过滤条件（同字段 OR，不同字段 AND）", { exact: true })
    .click();
  for (const name of ["authors", "datasets", "year_start"])
    await expect(composer.getByLabel(name, { exact: true })).toHaveValue("");
  await page.getByLabel("研究问题").fill("MOCK fresh unrestricted question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toHaveCount(1);
  expect(chat.sentBodies[1]?.filters).toMatchObject({
    authors: [],
    datasets: [],
    year_start: null,
  });
});

test("an active turn can be cancelled, keeps memory read-only and retries as a new assistant attempt", async ({
  page,
}) => {
  const chat = await setupChat(page, { holdStream: true });
  await page.route("**/api/runs/*/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: ": heartbeat\n\n",
    }),
  );
  await page.goto("/#/rag");
  await page.getByLabel("研究问题").fill("MOCK slow research question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消本轮" })).toBeVisible();
  await page.getByRole("button", { name: "会话记忆", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "添加本地记忆" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Clear Conversation", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "取消本轮" }).click();
  await expect(
    page.getByRole("heading", { name: "已取消", exact: true }),
  ).toBeVisible();
  expect(chat.cancels).toBe(1);
  await page.route("**/api/runs/*/events?*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({
      contentType: "text/event-stream",
      body: done(chat.completeRun(id)),
    });
  });
  await page.getByRole("button", { name: "重试本轮" }).click();
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Assistant 消息")).toHaveCount(2);
  await expect(page.getByLabel("User 消息")).toHaveCount(1);
  expect(chat.retries).toBe(1);
});

test("a failed assistant response remains visible and retry preserves its original attempt", async ({
  page,
}) => {
  const chat = await setupChat(page, {
    result: {
      ...run,
      status: "failed",
      error_code: "provider_key_missing",
      result: { draft_report: "MOCK unverified failed intermediate draft" },
    },
  });
  await page.goto("/#/rag");
  await page.getByLabel("研究问题").fill("MOCK failing model question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByLabel("Assistant 消息").getByRole("alert"),
  ).toContainText("provider_key_missing");
  await expect(
    page.getByLabel("Assistant 消息").getByRole("alert"),
  ).toContainText("没有发布未经验证的回答");
  await expect(page.locator(".report")).toHaveCount(0);
  await page.route("**/api/runs/*/events?*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({
      contentType: "text/event-stream",
      body: done(chat.completeRun(id, completed)),
    });
  });
  await page.getByRole("button", { name: "重试本轮" }).click();
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toBeVisible();
  await page.getByText(/先前尝试 #1.*审计记录/).click();
  await expect(page.getByRole("heading", { name: "执行失败" })).toBeVisible();
  expect(chat.runs.size).toBe(2);
});

test("repeated submission after a network failure reuses the request ID and double clicks create one turn", async ({
  page,
}) => {
  const chat = await setupChat(page);
  let firstKey = "",
    secondKey = "",
    requests = 0;
  await page.route("**/api/conversations/*/messages", async (route) => {
    requests += 1;
    const body = route.request().postDataJSON();
    if (requests === 1) {
      firstKey = body.client_request_id;
      return route.fulfill({
        status: 503,
        json: { detail: "queue_unavailable" },
      });
    }
    secondKey = body.client_request_id;
    await new Promise((resolve) => setTimeout(resolve, 150));
    return route.fallback();
  });
  await page.goto("/#/rag");
  await page.getByLabel("研究问题").fill("MOCK retry same submission");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("queue_unavailable");
  await expect(page.getByLabel("研究问题")).toHaveValue(
    "MOCK retry same submission",
  );
  await page.getByRole("button", { name: "发送", exact: true }).dblclick();
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toBeVisible();
  expect(secondKey).toBe(firstKey);
  expect(chat.sends).toBe(1);
  await expect(page.getByLabel("User 消息")).toHaveCount(1);
});

test("Enter sends, Shift+Enter inserts a newline and IME composition never submits prematurely", async ({
  page,
}) => {
  const chat = await setupChat(page);
  await page.goto("/#/rag");
  const input = page.getByLabel("研究问题");
  await input.fill("MOCK 中文输入");
  await input.dispatchEvent("compositionstart");
  // Synthetic composition does not activate the operating system IME. Send
  // its native composing key event; physical Shift+Enter/Enter are tested below.
  await input.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    isComposing: true,
  });
  expect(chat.sends).toBe(0);
  await expect(input).toHaveValue("MOCK 中文输入");
  await input.dispatchEvent("compositionend");
  await input.press("Shift+Enter");
  await input.pressSequentially("MOCK second line");
  await expect(input).toHaveValue("MOCK 中文输入\nMOCK second line");
  await input.press("Enter");
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toBeVisible();
  expect(chat.sends).toBe(1);
  expect(chat.sentBodies[0]?.content).toBe("MOCK 中文输入\nMOCK second line");
});

test("backend unavailable renders a useful state without a blank UI", async ({
  page,
}) => {
  await setupChat(page);
  await page.route("**/api/health", (route) =>
    route.abort("connectionrefused"),
  );
  await page.goto("/");
  await expect(page.getByLabel("本机后端状态")).toContainText("本机后端不可用");
  await expect(
    page.getByRole("button", { name: "重新检查", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "New Chat", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("研究问题")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Knowledge Base", exact: true }),
  ).toBeVisible();
});

test("live backend with unavailable task infrastructure is distinguished from readiness and inference", async ({
  page,
}) => {
  await setupChat(page);
  await page.route("**/api/ready", (route) =>
    route.fulfill({ status: 503, json: { detail: "worker_unavailable" } }),
  );
  await page.goto("/");
  await expect(page.getByLabel("本机后端状态")).toContainText("后端在线");
  await expect(page.getByLabel("本机后端状态")).toContainText("尚未就绪");
  await expect(page.getByLabel("本机后端状态")).not.toContainText(
    "本机后端不可用",
  );
});

test("long persisted conversations load later message pages and restore the newest response", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversation = chat.create("MOCK long history");
  for (let index = 0; index < 201; index += 1)
    chat.makeMessage(
      conversation,
      "user",
      `MOCK stored message ${index}`,
      null,
    );
  chat.makeMessage(
    conversation,
    "assistant",
    "MOCK newest persisted answer",
    completed,
  );
  await page.goto(`/#/rag/${conversation.id}`);
  await expect(page.getByLabel("User 消息")).toHaveCount(49);
  await expect(page.getByLabel("Assistant 消息")).toContainText(
    "MOCK newest persisted answer",
  );
  for (const count of [99, 149, 199, 201]) {
    await page
      .getByRole("button", { name: "加载更早消息", exact: true })
      .click();
    await expect(page.getByLabel("User 消息")).toHaveCount(count);
  }
  await expect(page.getByLabel("Assistant 消息")).toContainText(
    "MOCK newest persisted answer",
  );
  expect(
    chat.messageQueries.some((query) => query.includes("before_ordinal=152")),
  ).toBe(true);
  expect(chat.messageOffsets).toEqual([]);
  await expect(page.getByLabel("研究问题")).toBeEnabled();
});

test("conversation history pagination opens an older conversation and reload preserves it", async ({
  page,
}) => {
  const chat = await setupChat(page);
  const conversations = Array.from({ length: 51 }, (_, index) =>
    chat.create(`MOCK history ${index + 1}`),
  );
  const oldest = conversations[50];
  chat.makeMessage(oldest, "user", "MOCK older stored query", null);
  await page.goto("/#/rag");
  const history = page.getByLabel("Conversation History");
  await expect(history.getByRole("listitem")).toHaveCount(50);
  await page.getByRole("button", { name: "加载更早会话" }).click();
  await expect(history.getByRole("listitem")).toHaveCount(51);
  await history.getByRole("button", { name: /^MOCK history 51 RAG/ }).click();
  await expect(page.getByLabel("User 消息")).toHaveText(
    "你MOCK older stored query",
  );
  await expect(page).toHaveURL(new RegExp(oldest.id));
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "MOCK history 51" }),
  ).toBeVisible();
  await expect(page.getByLabel("User 消息")).toContainText(
    "MOCK older stored query",
  );
});

test("conversational evaluation dispatches its formal endpoint and displays dimensional metrics and artifacts", async ({
  page,
}) => {
  await setupChat(page);
  let submissions = 0;
  const evaluation = { ...run, kind: "eval_conversation" };
  await page.route("**/api/evaluations/conversation", (route) => {
    submissions += 1;
    return route.fulfill({ status: 202, json: evaluation });
  });
  await page.route("**/api/runs/*/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: done({
        ...evaluation,
        status: "completed",
        result: {
          summary: {
            context_resolution: { resolution_accuracy: 1 },
            memory_isolation: { memory_isolation_accuracy: null },
          },
          missing_dimensions: ["long_summary"],
        },
      }),
    }),
  );
  await page.goto("/#/evaluation");
  await page.getByLabel("评测类型").selectOption("conversation");
  await page
    .getByLabel("数据集内容")
    .fill('{"MOCK":"explicit wire-contract fixture"}');
  await page
    .getByRole("button", { name: "运行 Conversational RAG / Research 评测" })
    .click();
  await expect(
    page.getByRole("table", { name: "context_resolution 指标" }),
  ).toContainText("1.0000");
  await expect(
    page.getByRole("table", { name: "memory_isolation 指标" }),
  ).toContainText("N/A");
  await expect(
    page.getByRole("link", { name: "results.json" }),
  ).toHaveAttribute("href", `/api/evaluations/${run.id}/results.json`);
  expect(submissions).toBe(1);
});
