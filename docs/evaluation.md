# Evaluation and annotation

No benchmark improvements or human-annotated dataset are bundled. Synthetic
fixtures check pipeline behavior only: **DEMO ONLY / NOT A BENCHMARK / NOT
MANUALLY ANNOTATED**. A human must supply real queries, relevance labels and
expected answers before making research-quality comparisons.

Generate 100 unannotated forms:

```bash
python scripts/annotation_template.py my-annotations.json --count 100
```

A ready-made unannotated 100-row template and JSON schema are in
`evals/retrieval/`. Get exact corpus IDs from `/api/papers/{id}/chunks`. Read the
PDF and each chunk; enter question type, filters, relevant chunk/paper IDs,
expected answer, required aspects, notes and annotator/time. Change label_source
to human only after actual human annotation. The software records that declaration
as user supplied; it does not independently certify who labeled the data.
Unannotated data and nonexistent labeled corpus IDs are rejected. Retrieval
evaluation requires nonempty relevant chunk labels for every case. Generation
cases require a nonempty `expected_answer` and, unless `expected_refusal=true`,
nonempty `required_aspects` and relevance labels. An expected-refusal case can
omit relevant chunks/aspects; that flag must be supplied by the annotator rather
than inferred from a model refusing.

Optional pipeline corpus:

```bash
docker compose exec api python scripts/seed_demo.py
```

This appends one clearly labeled synthetic paper and uses real configured
embeddings (may incur charges with a hosted embedding backend). It generates an
original PDF and structured text, without pretending it came from a publisher.
Use `evals/retrieval/demo.json`. It is not sufficient for performance conclusions.

## Retrieval

POST `/api/evaluations/retrieval` with `{dataset: <dataset JSON>}`. Separate runs
measure dense, lexical, hybrid and hybrid_rerank. Each mode has its own actual
elapsed latency including embedding/inference for that mode; cold-cache ordering
can influence measurements. Warm up and repeat on a fixed corpus before comparing.

- Recall@1/5/10: retrieved relevant unique chunks / all relevant chunks.
- Precision@5: relevant chunks in first five / 5, including short result lists.
- MRR@10: reciprocal rank of first relevant result within ten, or zero.
- nDCG@10: binary relevance DCG divided by ideal DCG for known relevance.
- latency_ms: wall-clock execution time, never a copied number.

Dense/lexical/fused rankings are retained. Evaluation uses rerank K ≥ 10,
recorded separately from application evidence K. Chunk IDs and labels must refer
to the same index snapshot; changing chunking requires relabeling.

## RAG and multi-agent

POST `/api/evaluations/rag` or `/api/evaluations/multi-agent` with the same dataset.
The configured reviewer mapping is instantiated as a separate judge. Judgments
are **MODEL_BASED**, not human ground truth. Artifacts record judge provider,
model, full prompt, version and hash. Using the same model family for reviewer
and judge can create correlated errors; independent human review is needed for
scientific conclusions.

The manifest also freezes workflow retry, revision, iteration and evidence
budgets, plus each actual chat adapter's request timeout (null if unavailable).
Changes to environment settings during a run do not rewrite this provenance.

Citation precision is supported predicted claim/evidence pairs divided by all
predicted pairs. Citation recall is verified cited gold chunks divided by labeled
gold chunks; this is a reference-coverage measure, not a unique minimal-citation
estimate. Citation completeness is claims with a supported citation divided by
all released claims. Unsupported claim rate uses judge-supported claim IDs.
Answer/report completeness is fully answered required aspects divided by labeled
required aspects. Generation evaluation rejects missing required-aspect labels
on ordinary cases; it does not silently fill them or award a perfect score.
Undefined citation ratios produce null. Explicit refusals have no released
factual claims, and an unexpected refusal has answer completeness 0 even if the
judge asserts that an aspect was answered.

The judge receives the actual final answer/report, workflow status, claims,
exact evidence, expected answer/aspects and explicit expected-refusal flag.
Its evidence payload includes only released claims' cited IDs with valid exact
spans, their quotes and source metadata; full chunk content, retrieval scores
and uncited pool records are excluded from the semantic judgment. Full evidence
is retained separately in the artifact for human inspection.
`refusal_correctness` is defined for expected-refusal cases and actual refusals;
otherwise it is null. To score 1, the case must expect refusal, the workflow
must end in `insufficient_evidence`, release no claims, produce a nonempty actual
refusal, and receive the judge's `refusal_supported` verdict. Expected-refusal
answer completeness uses the same score. This is still a
**MODEL_BASED** determination, not human verification that a refusal was warranted.

Multi-agent also records verified workflow completion, retry/revision count,
workflow latency, per-provider prompt/completion tokens and cost when the SDK
knows prices. If any call's cost is unknown, the total is null; usage separately
records `known_cost` and `unknown_cost_calls`. Failed API calls may be billed
without returning usage, so they make cost incomplete. Judge latency/usage is separate from the
workflow. Retrieval-round counters do not count every expanded SQL query.

## Artifacts and reproducibility

Each run writes `data/evaluations/{run_id}/results.json` and `results.md`, downloadable
from the evaluation API. They include Git commit, canonical dataset hash,
timestamp, label source/warnings, model configuration, retrieval configuration,
per-query outputs/rankings, actual latency and summary. Generation results retain
`actual_output`, `evidence`, `gold_labels`, `judge_input` and raw `judgment` as well
as released claims, status and metrics. Evidence snapshots carry source metadata,
exact text/offsets and scores so a download can be inspected independently.

The manifest also freezes `source_hash`, `source_dirty`, `source_file_count`
and `source_hash_scope` at run start. The hash identifies the actual allowlisted
Python/Mako source under `src`/`migrations`, `pyproject.toml` and `uv.lock`,
including uncommitted changes; `.env`, corpus data and other secret/runtime files
are excluded. `source_dirty` is null when Git status cannot be inspected. The
hash is an identity/check value, not a bundled copy of the source; retain the
matching checkout or archive separately to reproduce it.

Provider configuration is captured from the actual instantiated adapters before
execution; changing the settings file during a run does not relabel that run.
Adapters without introspectable configuration are explicitly marked unavailable.
Retrieval provenance records the actual embedding/reranker revisions and the
embedder's frozen normalized API base; local adapters record no hosted base.
Fallback configuration without instantiated adapters is labeled
`configuration_available=false`, with unavailable values null.
For PostgreSQL-backed retrieval, the indexed corpus and metadata/vector/entity
inputs are hashed at the start and checked again at the end; a detected change
rejects the evaluation. This boundary check does not lock the corpus throughout
the run. Test search ports without a corpus session mark that hash unavailable
and retain evidence snapshot hashes instead. Keys are never recorded.
Mounting the clone's read-only `.git` directory lets containers identify the
commit; source archives must supply a real `GIT_COMMIT` value.

CI checks metrics against hand-calculated fixtures and executes real PostgreSQL
ablations without paid APIs/model downloads. These are correctness tests; they
are not evidence of retrieval improvement on research literature.
