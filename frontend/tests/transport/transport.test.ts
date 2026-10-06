/** Adapter unit tests: these exercise real transport encoding; no inference/GUI claims. */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { request, stream, openLocalResource } from "../../src/transport.ts";

let commands: { command: string; args: Record<string, unknown> }[];
let native: (
  command: string,
  args: Record<string, unknown>,
) => Promise<unknown>;
beforeEach(() => {
  commands = [];
  native = async () => ({
    status: 200,
    contentType: "application/json",
    body: btoa('{"ok":true}'),
  });
  Object.assign(globalThis, {
    isTauri: true,
    window: {
      __TAURI_INTERNALS__: {
        invoke: async (command: string, args: Record<string, unknown>) => {
          commands.push({ command, args });
          return native(command, args);
        },
        transformCallback: () => 1,
        unregisterCallback: () => undefined,
      },
    },
  });
});
afterEach(() => {
  mock.restoreAll();
  Object.assign(globalThis, { isTauri: false });
});

test("web fetch remains same-origin and forwards abort signal", async () => {
  Object.assign(globalThis, { isTauri: false });
  const controller = new AbortController();
  const fetchMock = mock.method(
    globalThis,
    "fetch",
    async (path: string, options: RequestInit) => {
      assert.equal(path, "/api/conversations");
      assert.equal(options.signal, controller.signal);
      return new Response("[]", {
        headers: { "Content-Type": "application/json" },
      });
    },
  );
  assert.deepEqual(
    await (
      await request("/api/conversations", { signal: controller.signal })
    ).json(),
    [],
  );
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(commands.length, 0);
});
test("desktop request only passes allowed payload fields and decodes response", async () => {
  const response = await request("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: '{"title":"科研"}',
  });
  assert.deepEqual(await response.json(), { ok: true });
  const payload = commands[0].args.request as {
    path: string;
    body: string;
    contentType: string;
    method: string;
  };
  assert.equal(payload.path, "/api/conversations");
  assert.equal(payload.method, "POST");
  assert.equal(
    new TextDecoder().decode(
      Uint8Array.from(atob(payload.body), (c) => c.charCodeAt(0)),
    ),
    '{"title":"科研"}',
  );
});
test("multipart upload preserves actual boundary and PDF bytes", async () => {
  const form = new FormData();
  form.set(
    "file",
    new Blob(["%PDF-fixture"], { type: "application/pdf" }),
    "local.pdf",
  );
  await request("/api/papers/upload", { method: "POST", body: form });
  const payload = commands[0].args.request as {
    contentType: string;
    body: string;
  };
  const boundary = payload.contentType.split("boundary=")[1];
  assert.ok(boundary);
  assert.ok(atob(payload.body).includes(`--${boundary}`));
  assert.ok(atob(payload.body).includes("%PDF-fixture"));
});
test("desktop rejects arbitrary headers/destinations and aborted requests", async () => {
  await assert.rejects(
    request("https://remote.invalid/api/health"),
    /invalid_local_path/,
  );
  await assert.rejects(
    request("/api/health", { headers: { Authorization: "secret" } }),
    /local_header_not_allowed/,
  );
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(request("/api/health", { signal: controller.signal }), {
    name: "AbortError",
  });
  assert.equal(commands.length, 0);
});
test("request cancellation is sent and no raw native exception is forwarded", async () => {
  const controller = new AbortController();
  native = async (command) => {
    if (command === "api_request") {
      controller.abort();
      throw new Error("private debug detail");
    }
    return undefined;
  };
  await assert.rejects(request("/api/health", { signal: controller.signal }), {
    name: "AbortError",
  });
  assert.equal(commands[1].command, "cancel_request");
});
test("desktop empty responses preserve HTTP 204 semantics", async () => {
  native = async () => ({ status: 204, contentType: null, body: "" });
  assert.equal((await request("/api/conversations")).status, 204);
});
test("desktop event subscription forwards replay cursor and cancels once", () => {
  native = async () => undefined;
  const events: string[] = [];
  const close = stream(
    "/api/runs/12345678-1234-1234-1234-123456789abc/events",
    {
      after: 8,
      onExecution: (data, id) => events.push(`${id}:${data}`),
      onDone: () => undefined,
      onError: () => undefined,
    },
  );
  assert.equal(commands[0].args.after, 8);
  const channel = commands[0].args.onEvent as {
    onmessage: (event: unknown) => void;
  };
  channel.onmessage({ event: "execution", id: "9", data: "{}" });
  close();
  close();
  channel.onmessage({ event: "execution", id: "10", data: "{}" });
  assert.deepEqual(events, ["9:{}"]);
  assert.equal(
    commands.filter((v) => v.command === "cancel_request").length,
    1,
  );
});
test("desktop document opener only accepts local fixed resource URLs", async () => {
  const id = "12345678-1234-1234-1234-123456789abc";
  native = async () => undefined;
  await openLocalResource(`/api/papers/${id}/pdf#page=2`);
  assert.equal(commands[0].command, "open_resource");
  for (const path of [
    "https://evil.invalid/p.pdf",
    "file:///etc/passwd",
    `/api/papers/${id}/pdf?url=remote`,
    `/api/evaluations/${id}/secret.env`,
  ])
    await assert.rejects(openLocalResource(path), /local_resource_not_allowed/);
  assert.equal(commands.length, 1);
});
