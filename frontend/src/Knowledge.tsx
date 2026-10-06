import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Paper, Run, api } from "./api";
import type { SourceStatus } from "./api";
import { isDesktop, openPaperPdf } from "./transport";
import { SourceProvenance, sourceStatusLabels } from "./components";
const pageSize = 50;
export function Knowledge() {
  const [papers, setPapers] = useState<z.infer<typeof Paper>[]>([]),
    [error, setError] = useState(""),
    [arxiv, setArxiv] = useState(""),
    [busy, setBusy] = useState(false),
    [task, setTask] = useState(""),
    [offset, setOffset] = useState(0),
    [hasNext, setHasNext] = useState(false),
    [loading, setLoading] = useState(false),
    [sourceEdits, setSourceEdits] = useState<Record<string, SourceStatus>>({}),
    [savingPaper, setSavingPaper] = useState<string | null>(null),
    [sourceNotice, setSourceNotice] = useState("");
  const requestSequence = useRef(0);
  async function refresh(pageOffset = offset, signal?: AbortSignal) {
    const sequence = ++requestSequence.current;
    setLoading(true);
    try {
      const items = await api(
        `/api/papers?limit=${pageSize + 1}&offset=${pageOffset}`,
        z.array(Paper),
        undefined,
        "GET",
        signal,
      );
      if (signal?.aborted || sequence !== requestSequence.current) return;
      setPapers(items.slice(0, pageSize));
      setHasNext(items.length > pageSize);
      setError("");
    } catch (e) {
      if (!signal?.aborted && sequence === requestSequence.current)
        setError(String(e));
    } finally {
      if (!signal?.aborted && sequence === requestSequence.current)
        setLoading(false);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void refresh(offset, controller.signal);
    const i = setInterval(() => void refresh(offset, controller.signal), 5000);
    return () => {
      clearInterval(i);
      controller.abort();
    };
  }, [offset]);
  async function upload(form: HTMLFormElement) {
    setBusy(true);
    setError("");
    try {
      const data = new FormData(form);
      if (!data.get("year")) data.delete("year");
      const r = await api("/api/papers/upload", Run, data);
      setTask(r.id);
      if (offset) setOffset(0);
      else await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function importArxiv() {
    setBusy(true);
    setError("");
    try {
      const r = await api("/api/papers/arxiv", Run, { arxiv_id: arxiv });
      setTask(r.id);
      if (offset) setOffset(0);
      else await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function saveSourceStatus(paper: z.infer<typeof Paper>) {
    setSavingPaper(paper.id);
    setError("");
    setSourceNotice("");
    try {
      const updated = await api(
        `/api/papers/${paper.id}`,
        Paper,
        { source_status: sourceEdits[paper.id] ?? paper.source_status },
        "PATCH",
      );
      setPapers((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setSourceEdits((edits) => {
        const remaining = { ...edits };
        delete remaining[paper.id];
        return remaining;
      });
      setSourceNotice(`${updated.title}：来源状态已保存`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingPaper(null);
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
      {sourceNotice && <p role="status">{sourceNotice}</p>}
      <p>
        来源状态需人工核对；indexed 仅表示已建立索引，不代表来源状态已核验。
      </p>
      <div className="inline" aria-label="文献分页">
        <button disabled={loading} onClick={() => void refresh()}>
          刷新
        </button>
        <button
          disabled={loading || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - pageSize))}
        >
          上一页
        </button>
        <button
          disabled={loading || !hasNext}
          onClick={() => setOffset(offset + pageSize)}
        >
          下一页
        </button>
        <p role="status">第 {offset / pageSize + 1} 页</p>
      </div>
      <table aria-busy={loading}>
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
                  onClick={(event) => {
                    if (isDesktop()) {
                      event.preventDefault();
                      void openPaperPdf(p.id).catch((e) => setError(String(e)));
                    }
                  }}
                  target="_blank"
                  rel="noreferrer"
                >
                  {p.title}
                </a>
                <small>{p.id}</small>
                <SourceProvenance source={p} />
              </td>
              <td>
                {p.authors.join("; ")}
                <br />
                {p.year} · {p.venue}
                <label>
                  来源状态（人工核对）
                  <select
                    aria-label={`${p.title} 来源状态`}
                    value={sourceEdits[p.id] ?? p.source_status}
                    disabled={savingPaper === p.id}
                    onChange={(event) =>
                      setSourceEdits((edits) => ({
                        ...edits,
                        [p.id]: event.target.value as SourceStatus,
                      }))
                    }
                  >
                    {Object.entries(sourceStatusLabels).map(
                      ([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ),
                    )}
                  </select>
                </label>
                <button
                  aria-label={`保存 ${p.title} 来源状态`}
                  disabled={
                    savingPaper !== null ||
                    sourceEdits[p.id] === undefined ||
                    sourceEdits[p.id] === p.source_status
                  }
                  onClick={() => void saveSourceStatus(p)}
                >
                  {savingPaper === p.id ? "正在保存…" : "保存来源状态"}
                </button>
              </td>
              <td>
                {p.status} · {p.chunk_count} chunks
                {p.error_code && <span role="alert">{p.error_code}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!loading && !error && !papers.length && (
        <p>
          {offset
            ? "此页没有文献，请返回上一页。"
            : "知识库为空。先上传 PDF 或导入 arXiv 论文。"}
        </p>
      )}
    </section>
  );
}
