import { expect, test } from "@playwright/test";
import { createServer } from "node:http";

// These browser tests use explicit MOCK HTTP/SSE fixtures. They exercise the
// production UI and wire contracts, not real retrieval or model verification.
const run = {
  id: "c82d3363-dcbb-45df-b81e-8bfaed0992c7",
  kind: "rag",
  status: "queued",
  trace_id: "test-trace",
  error_code: null,
  result: null,
};
const evidence = {
  evidence_id: "d5871625-f202-46f6-a2a2-cfba7d9d6994",
  paper: { paper_id: "paper-1", title: "MOCK evidence paper" },
  chunk_id: "chunk-1",
  section_path: "Results",
  page_start: 7,
  page_end: 8,
  quote: "Exact source text from the MOCK fixture.",
};
const completed = {
  ...run,
  status: "completed",
  result: {
    answer: `MOCK supported statement. [E:${evidence.evidence_id}]`,
    reranked_evidence: [evidence],
  },
};
const done = (result: object) =>
  `event: done\ndata: ${JSON.stringify(result)}\n\n`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/papers?*", (route) => route.fulfill({ json: [] }));
});

test("typing multiword and OR filters preserves the actual request", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/rag/query", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ status: 202, json: run });
  });
  await page.route("**/api/runs/*/events", (route) =>
    route.fulfill({ contentType: "text/event-stream", body: done(completed) }),
  );
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
  await page.getByRole("button", { name: "开始", exact: true }).click();
  await expect
    .poll(() => submitted?.filters)
    .toMatchObject({
      authors: ["Alice Smith", "Bob Jones"],
      venues: ["Nature Communications"],
      datasets: ["CIFAR 10", "Image Net"],
    });
  await expect(page.getByRole("heading", { name: "已验证结果" })).toBeVisible();
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
  await page.route("**/api/rag/query", (route) =>
    route.fulfill({ status: 503, json: { error_code: "queue_unavailable" } }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "RAG", exact: true }).click();
  await page.getByLabel("研究问题").fill("MOCK question");
  await page.getByRole("button", { name: "开始", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("queue_unavailable");
  await expect(
    page.getByRole("button", { name: "开始", exact: true }),
  ).toBeEnabled();
});

test("real EventSource reconnects with its cursor, completes and restores citations", async ({
  page,
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
      response.end(done(completed));
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("MOCK SSE server unavailable");
  await page.route("**/api/rag/query", (route) =>
    route.fulfill({ status: 202, json: run }),
  );
  await page.route(`**/api/runs/${run.id}`, (route) =>
    route.fulfill({ json: completed }),
  );
  await page.route("**/api/runs/*/events", (route) =>
    route.fulfill({
      status: 307,
      headers: { Location: `http://127.0.0.1:${address.port}/events` },
    }),
  );
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "RAG", exact: true }).click();
    await page.getByLabel("研究问题").fill("MOCK cited question");
    await page.getByRole("button", { name: "开始", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("正在重连");
    await expect(
      page.getByRole("heading", { name: "已验证结果" }),
    ).toBeVisible();
    expect(resumedCursor).toBe("7");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "文献 · p.7" }).click();
    const dialog = page.getByRole("dialog", { name: "引用原文" });
    await expect(dialog).toContainText(evidence.quote);
    await expect(dialog).toContainText("Chunk: chunk-1");
    await expect(
      dialog.getByRole("link", { name: "打开原始 PDF" }),
    ).toHaveAttribute("href", "/api/papers/paper-1/pdf#page=7");
    await page.reload();
    await page.getByRole("button", { name: "RAG", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "已验证结果" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "文献 · p.7" }),
    ).toBeVisible();
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
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
  await expect(page.getByRole("status")).toContainText("MOCK-new-model");
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
  await page.route("**/api/runs/*/events", (route) =>
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
