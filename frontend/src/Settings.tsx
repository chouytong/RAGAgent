import { useEffect, useState } from "react";
import { z } from "zod";
import { Mapping, Model, api } from "./api";
export function Settings() {
  const [mapping, setMapping] = useState<Mapping | null>(null),
    [savedMapping, setSavedMapping] = useState<Mapping | null>(null),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(mapping) !== JSON.stringify(savedMapping);
  useEffect(() => {
    void api("/api/providers", Mapping)
      .then((loaded) => {
        setMapping(loaded);
        setSavedMapping(loaded);
      })
      .catch((e) => setMessage(String(e)));
  }, []);
  async function save() {
    if (!mapping) return;
    setBusy(true);
    try {
      await api(
        "/api/providers",
        z.object({ status: z.string() }),
        mapping,
        "PUT",
      );
      setSavedMapping(mapping);
      setMessage("已保存。密钥只从服务端环境读取。");
      try {
        const loaded = await api("/api/providers", Mapping);
        setMapping(loaded);
        setSavedMapping(loaded);
      } catch {
        setMessage("配置已保存，但密钥状态刷新失败，请重新打开 Settings。");
      }
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function test(agent: string) {
    if (dirty || busy) return;
    setBusy(true);
    setMessage("正在测试…");
    try {
      const r = await api(
        "/api/providers/test",
        z.object({ ok: z.boolean(), model: z.string() }),
        { agent },
      );
      setMessage(`${agent}: ${r.ok ? "连接正常" : "测试未通过"} (${r.model})`);
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>Provider Settings</h2>
      <p>
        配置 Agent → provider / model。API keys
        从服务器环境读取，此界面不接收或显示密钥。
      </p>
      {mapping &&
        Object.entries(mapping.agents).map(([role, model]) => (
          <fieldset key={role} disabled={busy}>
            <legend>{role}</legend>
            <div className="grid">
              <label>
                Provider
                <select
                  value={model.provider}
                  onChange={(e) =>
                    setMapping({
                      ...mapping,
                      agents: {
                        ...mapping.agents,
                        [role]: {
                          ...model,
                          provider: Model.shape.provider.parse(e.target.value),
                        },
                      },
                    })
                  }
                >
                  {Model.shape.provider.options.map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              {(["model", "api_base", "api_key_env"] as const).map((key) => (
                <label key={key}>
                  {key}
                  <input
                    value={model[key] ?? ""}
                    onChange={(e) =>
                      setMapping({
                        ...mapping,
                        agents: {
                          ...mapping.agents,
                          [role]: {
                            ...model,
                            [key]:
                              key === "model"
                                ? e.target.value
                                : e.target.value || null,
                          },
                        },
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <p>
              环境密钥：
              {model.key_configured ? "已配置 / 本地无需密钥" : "未配置"}
            </p>
            <button disabled={dirty} onClick={() => void test(role)}>
              连接测试（可能产生少量费用）
            </button>
          </fieldset>
        ))}
      {dirty && (
        <p>配置尚未保存。请先保存，再测试已保存的 Provider / Model。</p>
      )}
      <button disabled={!mapping || busy || !dirty} onClick={() => void save()}>
        保存配置
      </button>
      <p role="status">{message}</p>
    </section>
  );
}
