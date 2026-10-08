import { expect, test } from "@playwright/test";
import { setupChat } from "./chat-fixtures";

test("diagnostics distinguishes healthy infrastructure and missing workers from model inference", async ({
  page,
}) => {
  await setupChat(page);
  await page.route("**/api/providers", (route) =>
    route.fulfill({ json: { agents: {} } }),
  );
  await page.route("**/api/diagnostics", (route) =>
    route.fulfill({
      json: {
        build: {
          version: "0.2.0",
          source_commit: "unknown",
          dirty: null,
          built_at_utc: "unknown",
        },
        database: "available",
        redis: "available",
        local_auth: "initialized",
        inference: "not_tested",
        queues: {
          interactive: { pending: 0, workers: 1 },
          evaluation: { pending: 3, workers: 0 },
        },
        chat_configuration: {},
        retrieval_configuration: { model_loading: "not_tested" },
      },
    }),
  );
  await page.goto("/#/settings");
  await page.getByText("本机诊断与版本", { exact: true }).click();
  await page.getByRole("button", { name: "读取诊断", exact: true }).click();
  await expect(page.getByText(/数据库：可用/)).toBeVisible();
  await expect(page.getByText(/模型推理：尚未验证/)).toBeVisible();
  await expect(page.getByText("evaluation：无 worker，待处理 3")).toBeVisible();
});
