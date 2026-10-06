import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Run, api } from "./api";
import { Json } from "./components";
import { isDesktop, openLocalResource, stream } from "./transport";

const modes = {
  retrieval: "Retrieval ablation",
  rag: "RAG generation",
  "multi-agent": "Multi-Agent generation",
  conversation: "Conversational RAG / Research",
} as const;
type EvaluationMode = keyof typeof modes;
const metrics = z.record(z.string(), z.number().nullable());
function summaryGroups(value: unknown) {
  const flat = metrics.safeParse(value);
  if (flat.success) return { generation: flat.data };
  const grouped = z.record(z.string(), metrics).safeParse(value);
  return grouped.success ? grouped.data : null;
}
export function Evaluation() {
  const [dataset, setDataset] = useState("");
  const [mode, setMode] = useState<EvaluationMode>("retrieval");
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = useRef(false);
  const cursor = useRef(0);
  const summary = summaryGroups(run?.result?.summary);
  const id = run?.id;
  useEffect(() => {
    if (!id) return;
    cursor.current = 0;
    return stream(`/api/runs/${id}/events`, {
      onExecution: (_data, eventId) => {
        cursor.current = Number(eventId) || cursor.current;
      },
      onDone: (data) => {
        try {
          setRun(Run.parse(JSON.parse(data)));
          setError("");
        } catch (e) {
          setError(String(e));
        }
      },
      onError: () => setError("事件流正在重连"),
    });
  }, [id]);
  async function start() {
    if (busy.current) return;
    busy.current = true;
    setSubmitting(true);
    try {
      setError("");
      setRun(
        await api(`/api/evaluations/${mode}`, Run, {
          dataset: JSON.parse(dataset),
        }),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      busy.current = false;
      setSubmitting(false);
    }
  }
  const resource = (name: "results.json" | "results.md") => (
    <a
      href={`/api/evaluations/${run?.id}/${name}`}
      onClick={(event) => {
        if (isDesktop()) {
          event.preventDefault();
          void openLocalResource(`/api/evaluations/${run?.id}/${name}`).catch(
            (e) => setError(String(e)),
          );
        }
      }}
    >
      {name}
    </a>
  );
  const active =
    submitting || run?.status === "queued" || run?.status === "running";
  return (
    <section>
      <h2>Evaluation</h2>
      <p>
        保留检索消融、RAG 与 Multi-Agent
        评测，并支持多轮上下文解析、证据落地、记忆隔离和长会话摘要评测。合成数据仅用于
        pipeline 验证：DEMO ONLY · NOT A BENCHMARK · NOT MANUALLY ANNOTATED。
      </p>
      <label>
        评测类型
        <select
          value={mode}
          disabled={active}
          onChange={(event) => setMode(event.target.value as EvaluationMode)}
        >
          {Object.entries(modes).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {mode === "conversation" && (
        <p className="muted">
          会话标注格式与模板位于 evals/conversation。Memory ≠
          Evidence；聊天中的陈述不能成为科学证据。缺失的评测维度会在结果中明确标记。
        </p>
      )}
      <input
        aria-label="数据集 JSON"
        type="file"
        accept="application/json"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file)
            void file
              .text()
              .then(setDataset)
              .catch((e) => setError(String(e)));
        }}
      />
      <textarea
        rows={8}
        aria-label="数据集内容"
        value={dataset}
        onChange={(event) => setDataset(event.target.value)}
      />
      <button disabled={active || !dataset} onClick={() => void start()}>
        {mode === "retrieval"
          ? "运行 retrieval ablation"
          : `运行 ${modes[mode]} 评测`}
      </button>
      {error && <p role="alert">{error}</p>}
      {run && (
        <>
          <p>
            状态：{run.status}
            {run.error_code && ` · ${run.error_code}`}
          </p>
          {summary &&
            Object.entries(summary).map(([group, values]) => (
              <div key={group}>
                <h3>{group}</h3>
                <table aria-label={`${group} 指标`}>
                  <thead>
                    <tr>
                      <th>指标</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(values).map(([name, value]) => (
                      <tr key={name}>
                        <td>{name}</td>
                        <td>
                          {value === null
                            ? "N/A"
                            : value.toFixed(name === "latency_ms" ? 2 : 4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          <details>
            <summary>完整结果及 provenance</summary>
            <Json value={run.result} />
          </details>
          {(run.status === "completed" || run.status === "failed") && (
            <p>
              {resource("results.json")} · {resource("results.md")}
            </p>
          )}
        </>
      )}
    </section>
  );
}
