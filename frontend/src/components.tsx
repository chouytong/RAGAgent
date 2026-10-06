import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { isDesktop, openPaperPdf } from "./transport";
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
  useEffect(() => {
    // Keep incomplete separators/whitespace while typing, but reflect resets
    // and other controlled changes from the parent in the displayed fields.
    setInputs((previous) => {
      const next = { ...previous };
      let changed = false;
      for (const key of fields) {
        const parsed = previous[key]
          .split(";")
          .map((part) => part.trim())
          .filter(Boolean);
        if (JSON.stringify(parsed) !== JSON.stringify(value[key])) {
          next[key] = value[key].join(";");
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [value]);
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
type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};
export function Citations({
  text,
  evidence,
}: {
  text: string;
  evidence: Evidence[];
}) {
  const [selected, setSelected] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  function remarkEvidence() {
    return (tree: unknown) => {
      function walk(node: MarkdownNode) {
        if (
          !node.children ||
          ["link", "code", "inlineCode", "image"].includes(node.type)
        )
          return;
        node.children = node.children.flatMap((child) => {
          if (child.type !== "text" || !child.value) {
            walk(child);
            return [child];
          }
          return child.value.split(/(\[E:[0-9a-f-]{36}\])/g).map((part) => {
            const id = part.match(/^\[E:([0-9a-f-]{36})\]$/)?.[1];
            const source = evidence.find((item) => item.evidence_id === id);
            return source
              ? {
                  type: "link",
                  url: `#evidence-${id}`,
                  children: [
                    { type: "text", value: `文献 · p.${source.page_start}` },
                  ],
                }
              : { type: "text", value: part };
          });
        });
      }
      walk(tree as MarkdownNode);
    };
  }
  const pdf = (paperId: string, page: number, label: string) => (
    <a
      href={`/api/papers/${paperId}/pdf#page=${page}`}
      target="_blank"
      rel="noreferrer"
      onClick={(event) => {
        if (isDesktop()) {
          event.preventDefault();
          void openPaperPdf(paperId, page).catch((e) => setError(String(e)));
        }
      }}
    >
      {label}
    </a>
  );
  return (
    <>
      <div className="report markdown">
        <Markdown
          skipHtml
          remarkPlugins={[remarkGfm, remarkEvidence]}
          components={{
            a: ({ href, children }) => {
              const source = href?.startsWith("#evidence-")
                ? evidence.find((item) => item.evidence_id === href.slice(10))
                : undefined;
              return source ? (
                <button
                  className="citation"
                  onClick={() => setSelected(source)}
                >
                  {children}
                </button>
              ) : (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              );
            },
            img: () => null,
          }}
        >
          {text}
        </Markdown>
      </div>
      <details>
        <summary>证据 ({evidence.length})</summary>
        {evidence.map((item) => (
          <button
            className="evidence"
            key={item.evidence_id}
            onClick={() => setSelected(item)}
          >
            {item.paper.title} — {item.section_path} · p.{item.page_start}–
            {item.page_end}
          </button>
        ))}
      </details>
      {error && <p role="alert">{error}</p>}
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
              {selected.source_spans.map((span, index) => (
                <small key={index}>
                  来源：{span.source_id} · 原文字符 {span.span_start}–
                  {span.span_end} · Chunk 字符 {span.chunk_start}–
                  {span.chunk_end}
                </small>
              ))}
            </details>
          )}
          {selected.source_context.length > 0 && (
            <div aria-label="辅助原文">
              <h4>辅助原文（表头、表题等，独立来源片段）</h4>
              {selected.source_context.map((context, index) => (
                <div
                  key={`${context.source_id}:${context.span_start}:${index}`}
                >
                  <p>
                    {context.element_type} · {context.section_path.join(" / ")}{" "}
                    · p.{context.page_start}–{context.page_end}
                  </p>
                  <small>
                    来源：{context.source_id} · 原文字符{" "}
                    {context.source_offset + context.span_start}–
                    {context.source_offset + context.span_end}
                  </small>
                  <blockquote aria-label="辅助引用原文">
                    {context.quote}
                  </blockquote>
                  {pdf(
                    selected.paper.paper_id,
                    context.page_start,
                    "打开辅助片段所在 PDF 页",
                  )}
                </div>
              ))}
            </div>
          )}
          {pdf(selected.paper.paper_id, selected.page_start, "打开原始 PDF")}
        </dialog>
      )}
    </>
  );
}
export function ConfirmAction({
  label,
  description,
  onConfirm,
  disabled = false,
}: {
  label: string;
  description: string;
  onConfirm: () => Promise<void>;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setOpen(true);
          setError("");
        }}
      >
        {label}
      </button>
      {open && (
        <dialog className="confirm-dialog" open aria-label={`${label}确认`}>
          <h3>{label}</h3>
          <p>{description}</p>
          {error && <p role="alert">{error}</p>}
          <div className="inline">
            <button
              type="button"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              取消
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void onConfirm()
                  .then(() => setOpen(false))
                  .catch((e) => setError(String(e)))
                  .finally(() => setBusy(false));
              }}
            >
              确认{label}
            </button>
          </div>
        </dialog>
      )}
    </>
  );
}
export function Json({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}
