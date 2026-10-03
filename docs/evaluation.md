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
Unannotated/empty-relevance data and nonexistent corpus IDs are rejected.

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

Citation precision is supported predicted claim/evidence pairs divided by all
predicted pairs. Citation recall is verified cited gold chunks divided by labeled
gold chunks; this is a reference-coverage measure, not a unique minimal-citation
estimate. Citation completeness is claims with a supported citation divided by
all released claims. Unsupported claim rate uses judge-supported claim IDs.
Answer/report completeness is fully answered required aspects divided by labeled
required aspects. Missing aspect labels and zero denominators produce null,
not an invented perfect score. Explicit refusals have no released factual claims.

Multi-agent also records verified workflow completion, retry/revision count,
workflow latency, per-provider prompt/completion tokens and cost when the SDK
knows prices. Unknown costs are null; failed API calls may not return usage or charges. Judge latency/usage is separate from the
workflow. Retrieval-round counters do not count every expanded SQL query.

## Artifacts and reproducibility

Each run writes `data/evaluations/{run_id}/results.json` and `results.md`, downloadable
from the evaluation API. They include Git commit, canonical dataset hash,
timestamp, label source/warnings, model configuration, retrieval configuration,
per-query outputs/rankings, actual latency and summary. Keys are never recorded.
Mounting the clone's read-only `.git` directory lets containers identify the
commit; source archives must supply a real `GIT_COMMIT` value.

CI checks metrics against hand-calculated fixtures and executes real PostgreSQL
ablations without paid APIs/model downloads. These are correctness tests; they
are not evidence of retrieval improvement on research literature.
