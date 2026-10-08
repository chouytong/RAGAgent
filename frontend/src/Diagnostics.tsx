import { useState } from "react";
import { z } from "zod";
import { invoke } from "@tauri-apps/api/core";
import { api } from "./api";
import { Json } from "./components";
import { isDesktop } from "./transport";

const Snapshot = z.object({
  build: z.object({
    version: z.string(),
    source_commit: z.string(),
    dirty: z.boolean().nullable(),
    built_at_utc: z.string(),
  }),
  database: z.enum(["available", "unavailable"]),
  redis: z.enum(["available", "unavailable"]),
  local_auth: z.string(),
  inference: z.literal("not_tested"),
  queues: z
    .record(z.string(), z.object({ pending: z.number(), workers: z.number() }))
    .nullable(),
  chat_configuration: z.unknown(),
  retrieval_configuration: z.unknown(),
});
export function Diagnostics() {
  const [data, setData] = useState<z.infer<typeof Snapshot> | null>(null);
  const [native, setNative] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function check() {
    setBusy(true);
    setError("");
    try {
      setData(
        await api(
          "/api/diagnostics",
          Snapshot,
          undefined,
          "GET",
          AbortSignal.timeout(10000),
        ),
      );
      if (isDesktop()) setNative(await invoke("desktop_build_info"));
    } catch (value) {
      setError(value instanceof Error ? value.message : "诊断读取失败。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>本机诊断与版本</summary>
      <p>
        分别检查授权、数据库、Redis
        与各工作队列。模型加载、推理和索引匹配需通过实际任务验证。
      </p>
      <button disabled={busy} onClick={() => void check()}>
        读取诊断
      </button>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <p>
            后端 {data.build.version} · 数据库：
            {data.database === "available" ? "可用" : "不可用"} · Redis：
            {data.redis === "available" ? "可用" : "不可用"}
          </p>
          <p>
            本机授权：{data.local_auth === "initialized" ? "已配置" : "未配置"}{" "}
            · 模型推理：尚未验证
          </p>
          {data.queues && (
            <ul>
              {Object.entries(data.queues).map(([role, state]) => (
                <li key={role}>
                  {role}：
                  {state.workers ? `${state.workers} 个 worker` : "无 worker"}
                  ，待处理 {state.pending}
                </li>
              ))}
            </ul>
          )}
          <h4>后端诊断详情</h4>
          <Json value={data} />
        </>
      )}
      {native !== null && (
        <>
          <h4>桌面构建信息</h4>
          <Json value={native} />
        </>
      )}
    </details>
  );
}
