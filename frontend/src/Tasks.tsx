import { useEffect, useState } from "react";
import { Event, Run, api } from "./api";
import type { Event as EventType, Run as RunType } from "./api";
import { Citations, FilterEditor, Json, emptyFilters } from "./components";
export function Tasks({ research }: { research: boolean }) {
  const [query, setQuery] = useState(""),
    [filters, setFilters] = useState(emptyFilters),
    [run, setRun] = useState<RunType | null>(null),
    [events, setEvents] = useState<EventType[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const storageKey = research ? "research_last_run" : "rag_last_run";
  useEffect(() => {
    const saved = localStorage.getItem(storageKey);
    if (saved)
      void api(`/api/runs/${saved}`, Run)
        .then((r) => {
          setRun(r);
          setBusy(r.status === "queued" || r.status === "running");
        })
        .catch((e) => setError(String(e)));
  }, [storageKey]);
  const id = run?.id;
  useEffect(() => {
    if (id) localStorage.setItem(storageKey, id);
  }, [id, storageKey]);
  useEffect(() => {
    if (!id) return;
    const es = new EventSource(`/api/runs/${id}/events`);
    es.addEventListener("open", () => setError(""));
    es.addEventListener("execution", (event) => {
      try {
        setError("");
        setEvents((e) => [
          ...e,
          Event.parse(JSON.parse((event as MessageEvent<string>).data)),
        ]);
      } catch {
        setError("执行事件格式错误");
      }
    });
    es.addEventListener("done", (event) => {
      try {
        setRun(Run.parse(JSON.parse((event as MessageEvent<string>).data)));
        setError("");
        setBusy(false);
      } catch {
        setError("结果格式错误");
        setBusy(false);
      }
      es.close();
    });
    es.onerror = () => setError("事件连接中断，正在重连；后台任务继续执行。");
    return () => es.close();
  }, [id]);
  async function submit() {
    setError("");
    setBusy(true);
    setEvents([]);
    try {
      setRun(
        await api(
          research ? "/api/research" : "/api/rag/query",
          Run,
          research ? { research_question: query, filters } : { query, filters },
        ),
      );
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }
  const result = run?.result;
  const limitations = [
    ...new Set([
      ...(result?.limitations ?? []),
      ...(result?.analysis_results.flatMap(
        (analysis) => analysis.limitations,
      ) ?? []),
    ]),
  ];
  const plan =
    result?.research_plan ??
    events.find((e) => e.node === "plan")?.payload.research_plan;
  return (
    <section>
      <h2>{research ? "Multi-Agent Research" : "文献 RAG"}</h2>
      <p>
        {research
          ? "任务规划 → 检索 → 分析 → 审核 → 报告"
          : "回答仅使用已入库且可验证的文献证据。"}
      </p>
      <textarea
        aria-label="研究问题"
        rows={4}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="输入科研问题"
      />
      <FilterEditor value={filters} onChange={setFilters} />
      <button disabled={busy || !query.trim()} onClick={() => void submit()}>
        {busy ? "正在执行…" : "开始"}
      </button>
      {error && <p role="alert">{error}</p>}
      {run && (
        <p>
          状态：{run.status} · Trace: {run.trace_id}
          {run.error_code && ` · ${run.error_code}`}
        </p>
      )}
      {plan != null && (
        <details open>
          <summary>Supervisor Plan</summary>
          <Json value={plan} />
        </details>
      )}
      <ol>
        {events.map((e, i) => (
          <li key={i}>
            <strong>{e.node}</strong> · {e.time}
            <details>
              <summary>执行详情（可能包含尚未审核的草稿）</summary>
              <Json value={e.payload} />
            </details>
          </li>
        ))}
      </ol>
      {result && (
        <>
          <h3>
            {run?.status === "completed" ? "已验证结果" : "结果 / 证据不足说明"}
          </h3>
          <Citations
            text={result.answer ?? result.draft_report ?? ""}
            evidence={result.evidence_pool ?? result.reranked_evidence ?? []}
          />
          {limitations.length > 0 && (
            <aside aria-label="未验证项与局限">
              <h3>未验证项与局限（尚未验证）</h3>
              <ul>
                {limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            </aside>
          )}
          {result.review_result != null && (
            <details open>
              <summary>Reviewer Result</summary>
              <Json value={result.review_result} />
            </details>
          )}
        </>
      )}
    </section>
  );
}
