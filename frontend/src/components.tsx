import { useState } from "react";
import type { Evidence, SourceStatus } from "./api";
export const sourceStatusLabels: Record<SourceStatus, string> = {
  unknown: "来源状态未核验",
  active: "来源状态：已标记有效",
  withdrawn: "来源已撤回，请勿据此作科研结论",
  retracted: "来源已撤稿，请勿据此作科研结论",
};
export function SourceProvenance({
  source,
}: {
  source: Evidence["paper"] | Omit<Evidence["paper"], "paper_id">;
}) {
  const status = source.source_status;
  return (
    <div aria-label="来源状态与版本">
      <p
        role={
          status === "withdrawn" || status === "retracted" ? "alert" : undefined
        }
      >
        {sourceStatusLabels[status]}
      </p>
      <p>
        {source.arxiv_id && `arXiv: ${source.arxiv_id} · `}
        {source.arxiv_version === null
          ? "版本未记录，需核对原始 PDF"
          : `冻结版本：v${source.arxiv_version}`}
      </p>
    </div>
  );
}
export type Filters = {
  paper_ids: string[];
  authors: string[];
  venues: string[];
  sections: string[];
  entity_types: string[];
  datasets: string[];
  methods: string[];
  metrics: string[];
  year_start: number | null;
  year_end: number | null;
};
export const emptyFilters: Filters = {
  paper_ids: [],
  authors: [],
  venues: [],
  sections: [],
  entity_types: [],
  datasets: [],
  methods: [],
  metrics: [],
  year_start: null,
  year_end: null,
};
export function FilterEditor({
  value,
  onChange,
}: {
  value: Filters;
  onChange: (v: Filters) => void;
}) {
  const fields = [
    "paper_ids",
    "authors",
    "venues",
    "sections",
    "entity_types",
    "datasets",
    "methods",
    "metrics",
  ] as const;
  const [inputs, setInputs] = useState(() =>
    Object.fromEntries(fields.map((key) => [key, value[key].join(";")])),
  );
  return (
    <details>
      <summary>文献过滤条件（同字段 OR，不同字段 AND）</summary>
      <div className="grid">
        {fields.map((key) => (
          <label key={key}>
            {key}
            <input
              placeholder="多个值用分号分隔"
              value={inputs[key]}
              onChange={(e) => {
                const text = e.target.value;
                setInputs((previous) => ({ ...previous, [key]: text }));
                onChange({
                  ...value,
                  [key]: text
                    .split(";")
                    .map((s) => s.trim())
                    .filter(Boolean),
                });
              }}
            />
          </label>
        ))}
        {(["year_start", "year_end"] as const).map((key) => (
          <label key={key}>
            {key}
            <input
              type="number"
              value={value[key] ?? ""}
              onChange={(e) =>
                onChange({
                  ...value,
                  [key]: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </label>
        ))}
      </div>
    </details>
  );
}
export function Citations({
  text,
  evidence,
}: {
  text: string;
  evidence: Evidence[];
}) {
  const [selected, setSelected] = useState<Evidence | null>(null);
  return (
    <>
      <div className="report">
        {text.split(/(\[E:[0-9a-f-]{36}\])/g).map((part, i) => {
          const id = part.match(/^\[E:([0-9a-f-]{36})\]$/)?.[1];
          const e = evidence.find((e) => e.evidence_id === id);
          return e ? (
            <button key={i} className="citation" onClick={() => setSelected(e)}>
              文献 · p.{e.page_start}
            </button>
          ) : (
            <span key={i}>{part}</span>
          );
        })}
      </div>
      <details>
        <summary>证据 ({evidence.length})</summary>
        {evidence.map((e) => (
          <button
            className="evidence"
            key={e.evidence_id}
            onClick={() => setSelected(e)}
          >
            {e.paper.title} — {e.section_path} · p.{e.page_start}–{e.page_end}
          </button>
        ))}
      </details>
      {selected && (
        <dialog open aria-label="引用原文">
          <button onClick={() => setSelected(null)}>关闭</button>
          <h3>{selected.paper.title}</h3>
          <SourceProvenance source={selected.paper} />
          <p>
            {selected.section_path} · p.{selected.page_start}–
            {selected.page_end}
          </p>
          <small>Chunk: {selected.chunk_id}</small>
          <blockquote aria-label="主引用原文">{selected.quote}</blockquote>
          {selected.source_spans.length > 0 && (
            <details>
              <summary>主引用来源定位</summary>
              {selected.source_spans.map((span, i) => (
                <small key={i}>
                  来源：{span.source_id} · 原文字符 {span.span_start}–
                  {span.span_end}
                  {" · "}Chunk 字符 {span.chunk_start}–{span.chunk_end}
                </small>
              ))}
            </details>
          )}
          {selected.source_context.length > 0 && (
            <div aria-label="辅助原文">
              <h4>辅助原文（表头、表题等，独立来源片段）</h4>
              {selected.source_context.map((context, i) => (
                <div key={`${context.source_id}:${context.span_start}:${i}`}>
                  <p>
                    {context.element_type} · {context.section_path.join(" / ")}{" "}
                    · p.
                    {context.page_start}–{context.page_end}
                  </p>
                  <small>
                    来源：{context.source_id} · 原文字符{" "}
                    {context.source_offset + context.span_start}–
                    {context.source_offset + context.span_end}
                  </small>
                  <blockquote aria-label="辅助引用原文">
                    {context.quote}
                  </blockquote>
                  <a
                    href={`/api/papers/${selected.paper.paper_id}/pdf#page=${context.page_start}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    打开辅助片段所在 PDF 页
                  </a>
                </div>
              ))}
            </div>
          )}
          <a
            href={`/api/papers/${selected.paper.paper_id}/pdf#page=${selected.page_start}`}
            target="_blank"
            rel="noreferrer"
          >
            打开原始 PDF
          </a>
        </dialog>
      )}
    </>
  );
}
export function Json({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}
