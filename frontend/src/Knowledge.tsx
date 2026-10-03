import { useEffect, useState } from "react";
import { z } from "zod";
import { Paper, Run, api } from "./api";
export function Knowledge() {
  const [papers, setPapers] = useState<z.infer<typeof Paper>[]>([]),
    [error, setError] = useState(""),
    [arxiv, setArxiv] = useState(""),
    [busy, setBusy] = useState(false),
    [task, setTask] = useState("");
  async function refresh() {
    try {
      setPapers(await api("/api/papers", z.array(Paper)));
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    void refresh();
    const i = setInterval(() => void refresh(), 5000);
    return () => clearInterval(i);
  }, []);
  async function upload(form: HTMLFormElement) {
    setBusy(true);
    setError("");
    try {
      const data = new FormData(form);
      if (!data.get("year")) data.delete("year");
      const r = await api("/api/papers/upload", Run, data);
      setTask(r.id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function importArxiv() {
    setBusy(true);
    try {
      const r = await api("/api/papers/arxiv", Run, { arxiv_id: arxiv });
      setTask(r.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>Knowledge Base</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void upload(e.currentTarget);
        }}
      >
        <label>
          PDF
          <input name="file" type="file" accept="application/pdf" required />
        </label>
        <div className="grid">
          <label>
            标题
            <input name="title" />
          </label>
          <label>
            作者（分号分隔）
            <input name="authors" />
          </label>
          <label>
            年份
            <input name="year" type="number" min="1000" max="2100" />
          </label>
          <label>
            会议 / 期刊
            <input name="venue" />
          </label>
        </div>
        <button disabled={busy}>上传并建立索引</button>
      </form>
      <div className="inline">
        <input
          aria-label="arXiv ID"
          value={arxiv}
          onChange={(e) => setArxiv(e.target.value)}
          placeholder="arXiv ID，如 2408.09869"
        />
        <button disabled={busy || !arxiv} onClick={() => void importArxiv()}>
          导入开放论文
        </button>
      </div>
      {task && <p>后台任务：{task}。首次解析和模型加载可能需要数分钟。</p>}
      {error && <p role="alert">{error}</p>}
      <button onClick={() => void refresh()}>刷新</button>
      <table>
        <thead>
          <tr>
            <th>论文</th>
            <th>元数据</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {papers.map((p) => (
            <tr key={p.id}>
              <td>
                <a
                  href={`/api/papers/${p.id}/pdf`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {p.title}
                </a>
                <small>{p.id}</small>
              </td>
              <td>
                {p.authors.join("; ")}
                <br />
                {p.year} · {p.venue}
              </td>
              <td>
                {p.status} · {p.chunk_count} chunks
                {p.error_code && <span role="alert">{p.error_code}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!papers.length && <p>知识库为空。先上传 PDF 或导入 arXiv 论文。</p>}
    </section>
  );
}
