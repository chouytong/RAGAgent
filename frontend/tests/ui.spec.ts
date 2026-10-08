import { expect, test as base } from "@playwright/test";
import { completed, done, evidence, run, setupChat } from "./chat-fixtures";
const test = base.extend<{ chat: Awaited<ReturnType<typeof setupChat>> }>({
  chat: [
    async ({ page }, use) => {
      await use(await setupChat(page));
    },
    { auto: true },
  ],
});
import { createServer } from "node:http";

// The original single-task tests now exercise the same filters, citation,
// failure and SSE assertions through formal persistent Conversation turns.
test("typing multiword and OR filters preserves the actual request", async ({
  page,
  chat,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "RAG", exact: true }).click();
  await page.getByText("文献过滤条件（同字段 OR，不同字段 AND）").click();
  await page
    .getByLabel("authors", { exact: true })
    .pressSequentially("Alice Smith;Bob Jones");
  await page
    .getByLabel("venues", { exact: true })
    .pressSequentially("Nature Communications");
  await page
    .getByLabel("datasets", { exact: true })
    .pressSequentially("CIFAR 10;Image Net");
  await expect(page.getByLabel("authors", { exact: true })).toHaveValue(
    "Alice Smith;Bob Jones",
  );
  await page.getByLabel("研究问题").fill("Compare these MOCK papers");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect
    .poll(() => chat.sentBodies[0]?.filters)
    .toMatchObject({
      authors: ["Alice Smith", "Bob Jones"],
      venues: ["Nature Communications"],
      datasets: ["CIFAR 10", "Image Net"],
    });
  await expect(
    page.getByRole("heading", { name: "已通过自动证据校验" }),
  ).toBeVisible();
});

test("knowledge pagination reaches paper 51 and returns to the first page", async ({
  page,
}) => {
  const papers = Array.from({ length: 51 }, (_, i) => ({
    id: `paper-${i + 1}`,
    title: `MOCK Paper ${i + 1}`,
    authors: [],
    year: 2024,
    venue: null,
    status: "indexed",
    error_code: null,
    chunk_count: 1,
  }));
  await page.route("**/api/papers?*", (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset"));
    const limit = Number(url.searchParams.get("limit"));
    return route.fulfill({ json: papers.slice(offset, offset + limit) });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Knowledge Base", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toHaveCount(50);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(
    page.getByRole("link", { name: "MOCK Paper 51", exact: true }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.getByRole("button", { name: "上一页" }).click();
  await expect(
    page.getByRole("link", { name: "MOCK Paper 1", exact: true }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(50);
});

test("API failure is visible and allows another RAG submission", async ({
  page,
}) => {
  await page.route("**/api/conversations/*/messages", (route) =>
    route.fulfill({ status: 503, json: { error_code: "queue_unavailable" } }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "RAG", exact: true }).click();
  await page.getByLabel("研究问题").fill("MOCK question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("queue_unavailable");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
});

test("real EventSource reconnects with its cursor, completes and restores citations", async ({
  page,
  chat,
}) => {
  let connections = 0;
  let resumedCursor: string | undefined;
  // A real local HTTP fixture preserves native streaming/reconnect headers;
  // intercepting and fulfilling SSE bodies hides Chromium's Last-Event-ID.
  const server = createServer((request, response) => {
    response.setHeader("Access-Control-Allow-Origin", "*");
    response.setHeader("Access-Control-Allow-Headers", "Last-Event-ID");
    if (request.method === "OPTIONS") {
      response.writeHead(204);
      response.end();
      return;
    }
    connections += 1;
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    if (connections === 1) {
      response.end(
        'retry: 500\nid: 7\nevent: execution\ndata: {"node":"plan","payload":{},"time":"2026-10-03T00:00:00Z"}\n\n',
      );
    } else {
      const cursor = request.headers["last-event-id"];
      resumedCursor = typeof cursor === "string" ? cursor : undefined;
      chat.completeRun(run.id, completed);
      response.end(done(completed));
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("MOCK SSE server unavailable");

  await page.route(`**/api/runs/${run.id}`, (route) =>
    route.fulfill({ json: completed }),
  );
  await page.route("**/api/runs/*/events?*", (route) =>
    route.fulfill({
      status: 307,
      headers: { Location: `http://127.0.0.1:${address.port}/events` },
    }),
  );
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "RAG", exact: true }).click();
    await page.getByLabel("研究问题").fill("MOCK cited question");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("正在重连");
    await expect(
      page.getByRole("heading", { name: "已通过自动证据校验" }),
    ).toBeVisible();
    expect(resumedCursor).toBe("7");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "文献 · p.7" }).click();
    const dialog = page.getByRole("dialog", { name: "引用原文" });
    await expect(dialog).toContainText(evidence.quote);
    await expect(dialog).toContainText("Chunk: chunk-1");
    await expect(dialog).toContainText("来源状态未核验");
    await expect(dialog).toContainText("版本未记录，需核对原始 PDF");
    await expect(
      dialog.getByRole("link", { name: "打开原始 PDF" }),
    ).toHaveAttribute("href", "/api/papers/paper-1/pdf#page=7");
    await page.reload();
    await page.getByRole("button", { name: "RAG", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "已通过自动证据校验" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "文献 · p.7" }),
    ).toBeVisible();
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("citations preserve version and separate auxiliary source text and limitations", async ({
  page,
  chat,
}) => {
  const tableEvidence = {
    ...evidence,
    paper: {
      ...evidence.paper,
      arxiv_id: "2408.09869v3",
      arxiv_family_id: "2408.09869",
      arxiv_version: 3,
      source_status: "retracted",
    },
    source_spans: [
      {
        source_id: "table-body",
        span_start: 120,
        span_end: 160,
        chunk_start: 0,
        chunk_end: 40,
      },
    ],
    source_context: [
      {
        source_id: "table-header",
        element_type: "table_header",
        section_path: ["Results", "Evaluation"],
        page_start: 6,
        page_end: 6,
        content: "Method | Accuracy (%)",
        quote: "Method | Accuracy (%)",
        span_start: 0,
        span_end: 21,
        source_offset: 30,
      },
    ],
  };
  const tableCompleted = {
    ...completed,
    result: {
      ...completed.result,
      reranked_evidence: [tableEvidence],
      limitations: ["MOCK unverified causal interpretation"],
    },
  };

  await page.route("**/api/runs/*/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: done(chat.completeRun(run.id, tableCompleted)),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "RAG", exact: true }).click();
  await page.getByLabel("研究问题").fill("MOCK table question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const limitations = page.getByRole("complementary", {
    name: "未验证项与局限",
  });
  await expect(limitations).toContainText("尚未验证");
  await expect(limitations).toContainText(
    "MOCK unverified causal interpretation",
  );
  await expect(page.locator(".report")).not.toContainText(
    "MOCK unverified causal interpretation",
  );
  await page.getByRole("button", { name: "文献 · p.7" }).click();
  const dialog = page.getByRole("dialog", { name: "引用原文" });
  await expect(dialog).toContainText("arXiv: 2408.09869v3");
  await expect(dialog).toContainText("冻结版本：v3");
  await expect(dialog.getByRole("alert")).toContainText("来源已撤稿");
  await expect(dialog.getByLabel("主引用原文", { exact: true })).toHaveText(
    evidence.quote,
  );
  await expect(dialog.getByLabel("辅助引用原文", { exact: true })).toHaveText(
    "Method | Accuracy (%)",
  );
  await expect(dialog.getByLabel("辅助原文", { exact: true })).toContainText(
    "Results / Evaluation · p.6–6",
  );
  await expect(dialog.getByLabel("辅助原文", { exact: true })).toContainText(
    "来源：table-header · 原文字符 30–51",
  );
  await expect(
    dialog.getByRole("link", { name: "打开辅助片段所在 PDF 页" }),
  ).toHaveAttribute("href", "/api/papers/paper-1/pdf#page=6");
  await dialog.getByText("主引用来源定位", { exact: true }).click();
  await expect(dialog).toContainText("来源：table-body · 原文字符 120–160");
});

test("research limitations are visible outside the reviewed report", async ({
  page,
  chat,
}) => {
  const researchRun = { ...run, kind: "research" };

  await page.route("**/api/runs/*/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: done(
        chat.completeRun(run.id, {
          ...researchRun,
          status: "completed",
          result: {
            draft_report: "MOCK reviewed research report",
            evidence_pool: [],
            analysis_results: [
              { limitations: ["MOCK unverified external validity"] },
              { limitations: ["MOCK unverified external validity"] },
            ],
          },
        }),
      ),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Research", exact: true }).click();
  await page.getByLabel("研究问题").fill("MOCK research question");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".report")).toHaveText(
    "MOCK reviewed research report",
  );
  await expect(
    page
      .getByRole("complementary", { name: "未验证项与局限" })
      .getByRole("listitem"),
  ).toHaveCount(1);
  await expect(
    page.getByRole("complementary", { name: "未验证项与局限" }),
  ).toContainText("MOCK unverified external validity");
});

test("knowledge source status can be manually saved without changing ingestion status", async ({
  page,
}) => {
  let paper = {
    id: "paper-1",
    title: "MOCK versioned paper",
    authors: [],
    year: 2024,
    venue: null,
    status: "indexed",
    error_code: null,
    chunk_count: 1,
    arxiv_id: "2408.09869v2",
    arxiv_family_id: "2408.09869",
    arxiv_version: 2,
    source_status: "unknown",
  };
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/papers?*", (route) =>
    route.fulfill({ json: [paper] }),
  );
  await page.route("**/api/papers/paper-1", (route) => {
    submitted = route.request().postDataJSON();
    paper = { ...paper, source_status: String(submitted?.source_status) };
    return route.fulfill({ json: paper });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Knowledge Base", exact: true })
    .click();
  const row = page.locator("tbody tr");
  await expect(row).toContainText("来源状态未核验");
  await expect(row).toContainText("冻结版本：v2");
  await row
    .getByRole("combobox", { name: "MOCK versioned paper 来源状态" })
    .selectOption("withdrawn");
  await row
    .getByRole("button", { name: "保存 MOCK versioned paper 来源状态" })
    .click();
  await expect.poll(() => submitted).toEqual({ source_status: "withdrawn" });
  await expect(
    page.getByText("MOCK versioned paper：来源状态已保存", { exact: true }),
  ).toBeVisible();
  await expect(row.getByRole("alert")).toContainText("来源已撤回");
  await expect(row).toContainText("indexed · 1 chunks");
  await expect(
    row.getByRole("button", { name: "保存 MOCK versioned paper 来源状态" }),
  ).toBeDisabled();
});

test("provider tests require an explicitly saved mapping", async ({ page }) => {
  let mapping = {
    agents: Object.fromEntries(
      ["supervisor", "retriever", "analyst", "reviewer"].map((role) => [
        role,
        {
          provider: "openai",
          model: "MOCK-old-model",
          api_base: null,
          api_key_env: null,
          key_configured: false,
        },
      ]),
    ),
  };
  let tests = 0;
  await page.route("**/api/providers", async (route) => {
    if (route.request().method() === "PUT") {
      mapping = route.request().postDataJSON();
      await route.fulfill({ json: { status: "saved" } });
    } else await route.fulfill({ json: mapping });
  });
  await page.route("**/api/providers/test", (route) => {
    tests += 1;
    return route.fulfill({
      json: { ok: true, model: mapping.agents.supervisor.model },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const supervisor = page.getByRole("group", {
    name: "supervisor",
    exact: true,
  });
  await supervisor.getByLabel("model", { exact: true }).fill("MOCK-new-model");
  await expect(
    supervisor.getByRole("button", { name: /连接测试/ }),
  ).toBeDisabled();
  await expect(
    page.getByText("配置尚未保存。请先保存，再测试已保存的 Provider / Model。"),
  ).toBeVisible();
  expect(tests).toBe(0);
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(
    supervisor.getByRole("button", { name: /连接测试/ }),
  ).toBeEnabled();
  await supervisor.getByRole("button", { name: /连接测试/ }).click();
  await expect(page.locator("section").getByRole("status")).toContainText(
    "MOCK-new-model",
  );
  expect(tests).toBe(1);
});

test("evaluation prevents duplicate dispatch and displays terminal artifacts", async ({
  page,
}) => {
  let submissions = 0;
  const evaluation = { ...run, kind: "eval_retrieval" };
  await page.route("**/api/evaluations/retrieval", async (route) => {
    submissions += 1;
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.fulfill({ status: 202, json: evaluation });
  });
  await page.route("**/api/runs/*/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: done({
        ...evaluation,
        status: "completed",
        result: { summary: { dense: { recall_at_1: 0.5 } } },
      }),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Evaluation", exact: true }).click();
  await page.getByLabel("数据集内容").fill('{"MOCK":"wire-contract dataset"}');
  await page
    .getByRole("button", { name: "运行 retrieval ablation" })
    .dblclick();
  await expect(page.getByRole("link", { name: "results.json" })).toBeVisible();
  await expect(page.locator("table")).toContainText("0.5000");
  expect(submissions).toBe(1);
});
