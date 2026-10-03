import { useEffect, useState } from "react";
import { z } from "zod";
import { Run, api } from "./api";
import { Json } from "./components";
export function Evaluation() {
  const [dataset, setDataset] = useState(""),
    [run, setRun] = useState<Run | null>(null),
    [error, setError] = useState("");
  const summary = z
    .record(z.string(), z.record(z.string(), z.number().nullable()))
    .safeParse(run?.result?.summary);
  const id = run?.id;
  useEffect(() => {
    if (!id) return;
    const es = new EventSource(`/api/runs/${id}/events`);
    es.addEventListener("done", (e) => {
      try {
        setRun(Run.parse(JSON.parse((e as MessageEvent<string>).data)));
      } catch (err) {
        setError(String(err));
      }
      es.close();
    });
    es.onerror = () => setError("事件流正在重连");
    return () => es.close();
  }, [id]);
  async function start() {
    try {
      setError("");
      setRun(
        await api("/api/evaluations/retrieval", Run, {
          dataset: JSON.parse(dataset),
        }),
      );
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <section>
      <h2>Evaluation</h2>
      <p>
        运行 dense / lexical / hybrid / hybrid + rerank 消融。使用已入库 chunk
        IDs 的标注数据。合成数据仅用于 pipeline 验证：DEMO ONLY · NOT A
        BENCHMARK · NOT MANUALLY ANNOTATED。
      </p>
      <input
        aria-label="数据集 JSON"
        type="file"
        accept="application/json"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void f.text().then(setDataset);
        }}
      />
      <textarea
        rows={8}
        aria-label="数据集内容"
        value={dataset}
        onChange={(e) => setDataset(e.target.value)}
      />
      <button
        disabled={
          !dataset || run?.status === "queued" || run?.status === "running"
        }
        onClick={() => void start()}
      >
        运行 retrieval ablation
      </button>
      {error && <p role="alert">{error}</p>}
      {run && (
        <>
          <p>
            状态：{run.status}
            {run.error_code && ` · ${run.error_code}`}
          </p>
          {summary.success && (
            <table>
              <thead>
                <tr>
                  <th>模式</th>
                  {Object.keys(Object.values(summary.data)[0] ?? {}).map(
                    (k) => (
                      <th key={k}>{k}</th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {Object.entries(summary.data).map(([mode, metrics]) => (
                  <tr key={mode}>
                    <td>{mode}</td>
                    {Object.entries(metrics).map(([k, v]) => (
                      <td key={k}>
                        {v === null
                          ? "N/A"
                          : v.toFixed(k === "latency_ms" ? 2 : 4)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details>
            <summary>完整结果及 provenance</summary>
            <Json value={run.result} />
          </details>
          {run.status === "completed" && (
            <p>
              <a href={`/api/evaluations/${run.id}/results.json`}>
                results.json
              </a>{" "}
              · <a href={`/api/evaluations/${run.id}/results.md`}>results.md</a>
            </p>
          )}
        </>
      )}
    </section>
  );
}
