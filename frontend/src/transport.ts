/** Web stays same-origin; desktop networking is validated twice and owned by Rust. */
import { Channel, invoke, isTauri } from "@tauri-apps/api/core";

export const isDesktop = (): boolean => isTauri();
export const AUTH_REQUIRED = "ragagent-auth-required";
function checked(response: Response): Response {
  if (response.status === 401) window.dispatchEvent(new Event(AUTH_REQUIRED));
  return response;
}
export type LocalCredentialStatus = {
  available: boolean;
  tokenHash: string | null;
  errorCode: string | null;
};
export const localCredentialStatus = (): Promise<LocalCredentialStatus> =>
  invoke("local_auth_status");
const decode = (data: string): Uint8Array =>
  Uint8Array.from(atob(data), (character) => character.charCodeAt(0));
function encode(data: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < data.length; offset += 32768)
    binary += String.fromCharCode(...data.subarray(offset, offset + 32768));
  return btoa(binary);
}
function localPath(path: string): void {
  if (!path.startsWith("/api/") || /[\\#\s]/.test(path) || path.includes(".."))
    throw new Error("invalid_local_path");
}
export async function request(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  if (!isDesktop()) return checked(await fetch(path, options));
  localPath(path);
  if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if ([...headers.keys()].some((name) => name !== "content-type"))
    throw new Error("local_header_not_allowed");
  let contentType: string | null = headers.get("content-type"),
    body: string | null = null;
  if (options.body != null) {
    const content = new Response(options.body);
    if (options.body instanceof FormData)
      contentType = content.headers.get("content-type");
    body = encode(new Uint8Array(await content.arrayBuffer()));
  }
  if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const id = crypto.randomUUID();
  const cancel = () =>
    void invoke("cancel_request", { id }).catch(() => undefined);
  options.signal?.addEventListener("abort", cancel, { once: true });
  try {
    const result = await invoke<{
      status: number;
      contentType: string | null;
      body: string;
    }>("api_request", {
      request: { id, path, method, contentType, body },
    });
    if (options.signal?.aborted)
      throw new DOMException("Aborted", "AbortError");
    return checked(
      new Response(
        [204, 205, 304].includes(result.status)
          ? null
          : new Uint8Array(decode(result.body)),
        {
          status: result.status,
          headers: result.contentType
            ? { "Content-Type": result.contentType }
            : {},
        },
      ),
    );
  } catch (error) {
    if (options.signal?.aborted)
      throw new DOMException("Aborted", "AbortError");
    throw new Error(
      typeof error === "string" ? error : "local_backend_unavailable",
    );
  } finally {
    options.signal?.removeEventListener("abort", cancel);
  }
}
export const apiFetch = request;
export type StreamCallbacks = {
  cursor?: number;
  after?: number;
  onExecution: (data: string, lastEventId: string) => void;
  onDone: (data: string) => void;
  onError: (error: string) => void;
  onOpen?: () => void;
};
type NativeEvent =
  | { event: "execution"; data: string; id: string }
  | { event: "done" | "error"; data: string };
export function stream(path: string, callbacks: StreamCallbacks): () => void {
  localPath(path);
  const after = callbacks.after ?? callbacks.cursor ?? 0;
  if (!Number.isSafeInteger(after) || after < 0)
    throw new Error("invalid_event_cursor");
  if (!isDesktop()) {
    const source = new EventSource(`${path}?after=${after}`);
    source.onopen = () => callbacks.onOpen?.();
    source.addEventListener("execution", (event) => {
      const message = event as MessageEvent<string>;
      callbacks.onExecution(message.data, message.lastEventId);
    });
    source.addEventListener("done", (event) => {
      callbacks.onDone((event as MessageEvent<string>).data);
      source.close();
    });
    source.onerror = () => callbacks.onError("event_stream_reconnecting");
    return () => source.close();
  }
  const id = crypto.randomUUID();
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    void invoke("cancel_request", { id }).catch(() => undefined);
  };
  const onEvent = new Channel<NativeEvent>();
  onEvent.onmessage = (event) => {
    if (closed) return;
    if (event.event === "execution")
      callbacks.onExecution(event.data, event.id);
    else if (event.event === "done") {
      callbacks.onDone(event.data);
      closed = true;
    } else {
      if (event.data === "local_auth_required")
        window.dispatchEvent(new Event(AUTH_REQUIRED));
      callbacks.onError(event.data);
    }
  };
  void invoke("run_events", { id, path, after, onEvent }).catch(
    (error: unknown) => {
      if (!closed)
        callbacks.onError(
          typeof error === "string" ? error : "local_stream_unavailable",
        );
    },
  );
  return close;
}
export const subscribeRun = stream;
const uuid =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export async function openLocalResource(path: string): Promise<void> {
  if (!isDesktop()) {
    window.open(path, "_blank", "noopener,noreferrer");
    return;
  }
  if (
    !new RegExp(
      `^/api/(papers/${uuid}/pdf(?:#page=[1-9][0-9]{0,4})?|evaluations/${uuid}/results\\.(json|md))$`,
    ).test(path)
  )
    throw new Error("local_resource_not_allowed");
  try {
    await invoke("open_resource", { path });
  } catch (error) {
    if (error === "local_auth_required")
      window.dispatchEvent(new Event(AUTH_REQUIRED));
    throw new Error(
      typeof error === "string" && /^[a-z][a-z0-9_]{0,100}$/.test(error)
        ? error
        : "local_document_unavailable",
    );
  }
}
export async function openPaperPdf(paperId: string, page = 1): Promise<void> {
  await openLocalResource(`/api/papers/${paperId}/pdf#page=${page}`);
}
