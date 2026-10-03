import { useState } from "react";
import type { Evidence } from "./api";
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
  return (
    <details>
      <summary>文献过滤条件（同字段 OR，不同字段 AND）</summary>
      <div className="grid">
        {fields.map((key) => (
          <label key={key}>
            {key}
            <input
              placeholder="多个值用分号分隔"
              value={value[key].join(";")}
              onChange={(e) =>
                onChange({
                  ...value,
                  [key]: e.target.value
                    .split(";")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
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
          <p>
            {selected.section_path} · p.{selected.page_start}–
            {selected.page_end}
          </p>
          <small>Chunk: {selected.chunk_id}</small>
          <blockquote>{selected.quote}</blockquote>
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
