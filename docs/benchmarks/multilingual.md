# Real multilingual retrieval matrix

Current result: **Not measured**. [Status artifact](multilingual-status.json)
contains null metrics for en→en, zh→en and zh→zh, including current models,
multilingual embedding, multilingual reranker, both, and a human-pretranslated
query variant. On 2026-10-08 the configured proxy denied Hugging Face CONNECT
with HTTP 403. No supplied approved human gold or model weight directories are
available; local optional inference packages are absent. Candidate weight
licenses/revisions are unverified, so defaults have not changed.

The runnable matrix reuses `evaluate_retrieval`, pgvector exact cosine, PostgreSQL
English FTS, RRF and the current cross-encoder adapter. It creates a uniquely
named scratch schema and drops that exact schema in a finally block. User tables,
embedding indexes and model configuration are untouched. Use an isolated test
PostgreSQL/pgvector database and an account permitted to create schemas.

```bash
uv sync --locked --extra models
# Supply an operator-approved manifest, licensed local model directories and
# an isolated TEST_DATABASE_URL through the process environment.
uv run python scripts/benchmark_multilingual.py \
  --manifest /absolute/path/approved-matrix.json \
  --output /absolute/path/benchmark-output
```

The [manifest JSON schema](multilingual-manifest.schema.json) is generated from
`MultilingualBenchmark`. It requires:

- Source chunks with stable paper/chunk IDs, language, section/page, source URL,
  source version/status and reviewed corpus license; each paper's metadata agrees.
- Human relevance cases with annotator/date/version, and all three directions.
  Gold IDs/languages must match the source corpus. Software does not independently
  certify human labels. Synthetic harness fixtures are not gold data.
- Matrix entries containing embedding/reranker local directories, operator-reviewed
  permissive license SPDX identity, LICENSE file/hash and declared immutable
  upstream revision when available. Actual local artifact digests are recorded.
  A reviewed license file hash alone is not a legal license determination.
- Optional zh→en translations with human translator/date. No evaluation model
  generates translations or labels, and no paid translation call is hidden.

The harness supports the current 384-dimension candidates. Runtime inference uses
CPU, fixed seed and deterministic PyTorch settings; official code does not enable
remote model code. CLI sets offline model flags. Missing licensed artifacts yield
`Not measured` with null metrics before database/model calls. Existing raw evaluator
results include exact rankings, gold labels, corpus/model/settings/source identities,
per-query latency and failure state. Direction summaries record completed/failed
counts so failed cases are not presented as a complete quality score.

Indexing latency includes initial embedding model load; retrieval per-query latency
includes lazy reranker loading on its first invocation. It is not a cold/warm
latency decomposition or browser/network/inference-answer benchmark. Translation
latency/cost are excluded because translations are preannotated. Corpus metadata
filters beyond paper/year/section are rejected until those labels are provided by
the harness; they are not silently ignored.

Core CI tests only schema/routing/cleanup with clearly marked scripted models.
They establish reproducible plumbing and isolation, **not real model quality**.
For production model adoption, review real metrics and weight licenses, immutable
revisions and reindex requirements. Keep exact search for small corpora; ANN remains
optional and needs measured recall/corpus-scale evidence before tuning or adoption.
