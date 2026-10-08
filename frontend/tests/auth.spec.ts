import { expect, test } from "@playwright/test";
import { setupChat } from "./chat-fixtures";

test("unauthorized Web restores chats after ephemeral authorization without browser token storage", async ({
  page,
}) => {
  await setupChat(page);
  let authorized = false;
  const synthetic = "SYNTHETIC".repeat(8);
  await page.route("**/api/conversations*", async (route) => {
    if (authorized) await route.fallback();
    else
      await route.fulfill({
        status: 401,
        json: { error_code: "local_auth_required" },
      });
  });
  await page.route("**/api/auth/session", async (route) => {
    authorized =
      route.request().headers().authorization === `Bearer ${synthetic}`;
    await route.fulfill({
      status: authorized ? 200 : 401,
      json: { authenticated: authorized },
    });
  });
  await page.route("**/api/auth/status", (route) =>
    route.fulfill({ json: { initialized: true, authenticated: authorized } }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "本机连接授权" }),
  ).toBeVisible();
  await page.getByLabel("开发凭据").fill(synthetic);
  await page.getByRole("button", { name: "连接并检查授权" }).click();
  await expect(page.getByLabel("研究问题")).toBeVisible();
  expect(
    await page.evaluate(
      () => JSON.stringify(localStorage) + JSON.stringify(sessionStorage),
    ),
  ).not.toContain(synthetic);
  await page.getByRole("button", { name: "连接授权", exact: true }).click();
  await expect(page.getByLabel("开发凭据")).toHaveValue("");
});
