import { useEffect, useState } from "react";
import { z } from "zod";
import type { Message } from "../api";
import { Citations, Json } from "../components";
import { activeStatus } from "./useConversationMessages";
import { useRunEvents } from "./useRunEvents";

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  completed: "证据校验通过",
  insufficient_evidence: "证据不足",
  failed: "执行失败",
  cancelled: "已取消",
};
const Presentation = z.object({
  limitations: z.array(z.string()).default([]),
  citation_refs: z
    .array(
      z.object({
        evidence_id: z.string(),
        page_start: z.number(),
        page_end: z.number().optional(),
      }),
    )
    .default([]),
  conversation_context: z.unknown().optional(),
});

/** Main answer is persisted Message.content; expensive scientific detail is fetched on demand. */
export function ResultPanel({
  message,
  current,
  activeConversation,
  retryable,
  onReconcile,
  onRetry,
}: {
  message: Message;
  current: boolean;
  activeConversation: boolean;
  retryable: boolean;
  onReconcile: (messageId: string) => Promise<void>;
  onRetry: (message: Message) => Promise<void>;
}) {
  const [details, setDetails] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [detailError, setDetailError] = useState("");
  const { fullRun, events, connection, loadRun } = useRunEvents(
    message,
    details,
    onReconcile,
  );
  const run = fullRun ?? message.run;
  const state = fullRun?.status ?? message.status;
  const result = fullRun?.result;
  const presentationResult = Presentation.safeParse(
    message.metadata.presentation,
  );
  const presentation = presentationResult.success
    ? presentationResult.data
    : undefined;
  const released = state === "completed" || state === "insufficient_evidence";
  const text = released
    ? message.content || result?.answer || result?.draft_report || ""
    : "";
  const limitations = [
    ...new Set([
      ...(result?.limitations ?? presentation?.limitations ?? []),
      ...(result?.analysis_results.flatMap(
        (analysis) => analysis.limitations,
      ) ?? []),
    ]),
  ];
  const plan =
    result?.research_plan ??
    events.find((event) => event.node === "plan")?.payload.research_plan;
  const loadDetail = () =>
    loadRun().catch((error) => {
      setDetailError(String(error));
      throw error;
    });
  useEffect(() => {
    // Only the current response may need its result immediately (legacy records
    // without compact metadata). Historical answers never all fetch their Runs.
    if (current && released && !presentation && !fullRun)
      void loadDetail().catch(() => undefined);
  }, [current, released, message.id, Boolean(presentation)]);
  const loadEvidence = async () => {
    const detail = await loadDetail();
    return (
      detail.result?.evidence_pool ?? detail.result?.reranked_evidence ?? []
    );
  };
  return (
    <article
      className="chat-message assistant-message"
      aria-label="Assistant 消息"
      data-message-id={message.id}
    >
      <div className="message-heading">
        <strong>Scientific RAGAgent</strong>
        <h3
          className={`status-badge ${state}`}
          title={
            state === "completed"
              ? "已通过自动证据检查；这不能保证科学事实正确，仍需研究人员判断。"
              : undefined
          }
        >
          {statusLabels[state] ?? state}
        </h3>
      </div>
      {activeStatus(state) && (
        <p className="running-placeholder" role="status">
          正在检索与验证文献证据…
        </p>
      )}
      {text && (
        <Citations
          text={text}
          evidence={result?.evidence_pool ?? result?.reranked_evidence ?? []}
          references={presentation?.citation_refs}
          loadEvidence={loadEvidence}
        />
      )}
      {(state === "failed" || state === "cancelled") && (
        <p role={state === "failed" ? "alert" : "status"}>
          {state === "failed"
            ? "本轮未完成，没有发布未经验证的回答。"
            : "本轮已取消。"}
          {run?.error_code && ` (${run.error_code})`}
        </p>
      )}
      {retryable &&
        ["failed", "cancelled", "insufficient_evidence"].includes(state) && (
          <button
            disabled={activeConversation || retrying}
            onClick={() => {
              setRetrying(true);
              void onRetry(message).finally(() => setRetrying(false));
            }}
          >
            重试本轮
          </button>
        )}
      {limitations.length > 0 && (
        <aside className="limitations" aria-label="未验证项与局限">
          <strong>未验证项与局限（尚未验证）</strong>
          <ul>
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </aside>
      )}
      {connection && (
        <p role="alert">
          {connection}{" "}
          <button
            onClick={() =>
              void onReconcile(message.id).catch((error) =>
                setDetailError(String(error)),
              )
            }
          >
            恢复状态
          </button>
        </p>
      )}
      {detailError && (
        <p role="alert">
          {detailError}{" "}
          <button
            onClick={() => {
              setDetailError("");
              void loadDetail().catch(() => undefined);
            }}
          >
            重新读取详情
          </button>
        </p>
      )}
      {run && (
        <details
          className="execution-details"
          onToggle={(event) => {
            setDetails(event.currentTarget.open);
            if (event.currentTarget.open)
              void loadDetail().catch(() => undefined);
          }}
        >
          <summary>
            执行详情 · {run.kind === "research" ? "Multi-Agent" : "RAG"}
          </summary>
          <p>
            状态：{run.status} · Trace: {run.trace_id}
            {run.error_code && ` · ${run.error_code}`}
          </p>
          {plan != null && (
            <details>
              <summary>Supervisor Plan</summary>
              <Json value={plan} />
            </details>
          )}
          <ol className="execution-trace">
            {events.map((event, index) => (
              <li key={event.eventId || index}>
                <strong>{event.node}</strong> · <time>{event.time}</time>
                <details>
                  <summary>执行详情（可能包含尚未审核的草稿）</summary>
                  <Json value={event.payload} />
                </details>
              </li>
            ))}
          </ol>
          {result?.review_result != null && (
            <details>
              <summary>Reviewer Result</summary>
              <Json value={result.review_result} />
            </details>
          )}
          {(presentation?.conversation_context != null ||
            message.metadata.original_query != null) && (
            <details>
              <summary>问题与指代解析</summary>
              <Json
                value={
                  presentation?.conversation_context ?? {
                    original_query: message.metadata.original_query,
                    contextualized_query: message.metadata.contextualized_query,
                  }
                }
              />
            </details>
          )}
        </details>
      )}
    </article>
  );
}
