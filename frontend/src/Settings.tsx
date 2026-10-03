import { useEffect, useState } from "react";
import { z } from "zod";
import { Mapping, Model, api } from "./api";
export function Settings() {
  const [mapping, setMapping] = useState<Mapping | null>(null),
    [message, setMessage] = useState("");
  useEffect(() => {
    void api("/api/providers", Mapping)
      .then(setMapping)
      .catch((e) => setMessage(String(e)));
  }, []);
  async function save() {
    if (!mapping) return;
    try {
      await api(
        "/api/providers",
        z.object({ status: z.string() }),
        mapping,
        "PUT",
      );
      setMessage("已保存。密钥只从服务端环境读取。");
    } catch (e) {
      setMessage(String(e));
    }
  }
  async function test(agent: string) {
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
          <fieldset key={role}>
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
                          [role]: { ...model, [key]: e.target.value || null },
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
            <button onClick={() => void test(role)}>
              连接测试（可能产生少量费用）
            </button>
          </fieldset>
        ))}
      <button disabled={!mapping} onClick={() => void save()}>
        保存配置
      </button>
      <p role="status">{message}</p>
    </section>
  );
}
