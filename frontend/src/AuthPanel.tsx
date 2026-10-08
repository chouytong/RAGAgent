import { useEffect, useState } from "react";
import { isDesktop, localCredentialStatus, request } from "./transport";

export function AuthPanel({ onConnected }: { onConnected: () => void }) {
  const [hash, setHash] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (isDesktop())
      void localCredentialStatus().then(
        (value) => {
          setHash(value.tokenHash);
          if (!value.available)
            setStatus(
              "系统凭据库不可用，请检查系统钥匙串或 Credential Manager。",
            );
        },
        () => setStatus("无法读取系统凭据状态。"),
      );
  }, []);
  async function connect() {
    setBusy(true);
    setStatus("");
    try {
      if (!isDesktop() && token) {
        const response = await request("/api/auth/session", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) throw new Error("auth_failed");
      }
      const response = await request("/api/auth/status");
      const value: { authenticated: boolean; initialized: boolean } =
        await response.json();
      if (response.ok && value.authenticated) onConnected();
      else
        setStatus(
          value.initialized
            ? "授权失败，请核对凭据与后端配置。"
            : "后端尚未配置授权，请完成本机配对并重启后端。",
        );
    } catch {
      setStatus("连接或授权失败，请检查本机后端与配对配置。");
    } finally {
      setToken("");
      setBusy(false);
    }
  }
  return (
    <section className="panel" aria-label="本机连接授权">
      <h2>本机连接授权</h2>
      {isDesktop() ? (
        <>
          <p>
            将下方配对哈希填入后端 .env 的
            LOCAL_AUTH_TOKEN_HASH，重启后端后重新连接。凭据由系统凭据库保存。
          </p>
          {hash && <pre aria-label="配对哈希">{hash}</pre>}
        </>
      ) : (
        <>
          <p>
            Web 开发模式使用后端进程环境中的 LOCAL_AUTH_TOKEN。授权在 HttpOnly
            会话中保存，后端重启后需重新授权。
          </p>
          <label>
            开发凭据
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </label>
        </>
      )}
      <button disabled={busy} onClick={() => void connect()}>
        连接并检查授权
      </button>
      {status && <p role="alert">{status}</p>}
    </section>
  );
}
