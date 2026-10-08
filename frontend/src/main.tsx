import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Knowledge } from "./Knowledge";
import { Tasks } from "./Tasks";
import { Settings } from "./Settings";
import { Evaluation } from "./Evaluation";
import { request, isDesktop, AUTH_REQUIRED } from "./transport";
import { AuthPanel } from "./AuthPanel";
import "./style.css";

const pages = [
  "RAG",
  "Research",
  "Knowledge Base",
  "Settings",
  "Evaluation",
] as const;
type Page = (typeof pages)[number];
const paths: Record<Page, string> = {
  RAG: "rag",
  Research: "research",
  "Knowledge Base": "knowledge",
  Settings: "settings",
  Evaluation: "evaluation",
};
function pageFromHash(): Page {
  const path = window.location.hash.split("/")[1];
  return pages.find((page) => paths[page] === path) ?? "RAG";
}
function BackendStatus() {
  const [state, setState] = useState<"checking" | "offline" | "live" | "ready">(
    "checking",
  );
  const [checking, setChecking] = useState(false);
  const check = useCallback(async (signal?: AbortSignal) => {
    setChecking(true);
    let live = false;
    const probeSignal = signal
      ? AbortSignal.any([signal, AbortSignal.timeout(5000)])
      : AbortSignal.timeout(5000);
    try {
      const health = await request("/api/health", { signal: probeSignal });
      if (!health.ok) throw new Error("backend_unavailable");
      live = true;
      const ready = await request("/api/ready", { signal: probeSignal });
      if (!signal?.aborted) setState(ready.ok ? "ready" : "live");
    } catch {
      if (!signal?.aborted) setState(live ? "live" : "offline");
    } finally {
      if (!signal?.aborted) setChecking(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void check(controller.signal);
    const timer = setInterval(() => void check(controller.signal), 15000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [check]);
  const text = {
    checking: "正在检查本机后端…",
    offline: "本机后端不可用，请先启动 FastAPI、数据库与任务服务。",
    live: "后端在线，授权、数据库或任务服务尚未就绪。",
    ready: "本机后端与任务服务就绪 · 模型配置与推理需另行验证",
  }[state];
  return (
    <div className={`backend-status ${state}`} aria-label="本机后端状态">
      <span role={state === "offline" ? "alert" : "status"}>{text}</span>
      <button disabled={checking} onClick={() => void check()}>
        重新检查
      </button>
    </div>
  );
}
function App() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [needsAuth, setNeedsAuth] = useState(false);
  useEffect(() => {
    const requireAuth = () => setNeedsAuth(true);
    window.addEventListener(AUTH_REQUIRED, requireAuth);
    return () => window.removeEventListener(AUTH_REQUIRED, requireAuth);
  }, []);
  useEffect(() => {
    const update = () => setPage(pageFromHash());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <h1>Scientific RAGAgent</h1>
          <p>
            可追溯的文献问答与研究协作 {isDesktop() ? "· Desktop" : "· Web"}
          </p>
        </div>
        <nav aria-label="应用导航">
          <button onClick={() => setNeedsAuth(true)}>连接授权</button>
          {pages.map((item) => (
            <button
              key={item}
              aria-current={page === item ? "page" : undefined}
              onClick={() => {
                setPage(item);
                window.location.hash = `/${paths[item]}`;
              }}
            >
              {item}
            </button>
          ))}
        </nav>
      </header>
      <BackendStatus />
      <main
        className={
          page === "RAG" || page === "Research" ? "chat-page" : undefined
        }
      >
        {needsAuth ? (
          <AuthPanel onConnected={() => setNeedsAuth(false)} />
        ) : page === "Knowledge Base" ? (
          <Knowledge />
        ) : page === "Settings" ? (
          <Settings />
        ) : page === "Evaluation" ? (
          <Evaluation />
        ) : (
          <Tasks key={page} research={page === "Research"} />
        )}
      </main>
      <footer>
        Evidence first ·
        历史与记忆保存在本机。选择远程模型时，必要上下文会发送到模型服务商。科研结论仍需研究人员判断。
      </footer>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
