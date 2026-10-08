# Measured engineering benchmarks

These artifacts measure the real PostgreSQL-backed API. They do **not** measure
scientific retrieval, model accuracy, inference latency or browser rendering.
The inserted content is clearly labeled **SYNTHETIC API PERFORMANCE FIXTURE /
NOT SCIENTIFIC RETRIEVAL QUALITY**. No model or paid provider is invoked.

## Message API baseline

[`messages-before.json`](messages-before.json) contains the original measurements
against checkout `ba04246c82f556753c36980e76cf64360ca533ce`, before the engineering
review fixes. It records 18 scenarios, three warmup requests per scenario and
15 measured requests per scenario: **270 raw measured samples**. RAG and Research
each have 10, 50 and 100 total messages, with equal numbers of completed user and
assistant messages. Each pair references the same completed Run, whose result
contains eight source-shaped fixture records and 40 diagnostic trace entries.

The three request shapes are deliberately unchanged between before and after:

| Request | Meaning |
|---|---|
| `full_history` | All 10, 50 or 100 messages, using explicit limit and offset. |
| `tail_window_20` | The last 20 messages, or all 10 in the shortest conversation. |
| `single_message` | The last single message, using explicit offset. |

Measurements include database queries, FastAPI response serialization and
TestClient ASGI transport to real PostgreSQL over TCP. They exclude actual HTTP
network transport, TLS, React rendering and provider inference. Each request uses
a new SQLAlchemy Session. The artifacts retain response byte count, elapsed
latency, SELECT count, statements loading `runs.result`, all raw samples and
fixture result hashes. Compare fixture hashes and request shapes before using an
after result. Latency is environment-dependent; the warm-cache samples are not a
production throughput or concurrency claim.

The baseline actually used PostgreSQL **17.10** and pgvector **0.8.2**. For RAG
full history, the recorded measurements were:

| Total messages | Response bytes | Median latency | SELECT statements |
|---:|---:|---:|---:|
| 10 | 510,886 | 14.49 ms | 12 |
| 50 | 2,554,466 | 46.55 ms | 52 |
| 100 | 5,108,941 | 93.35 ms | 102 |

The original script hash is preserved in the baseline manifest:
`854b8f87b8ff61a81994b56b64ab266e4b357ac4c7cb4cd2974ccd0bebfef1d7`.
The committed [`benchmark_messages.py`](../../scripts/benchmark_messages.py)
preserves the same fixture and three original request shapes, with a formatter-only
string-layout change and an optional `--cursors` flag for separately labeled new
requests; later artifacts record the exact script bytes used. A matching stored
result hash verifies the fixture itself independently of that layout change.

## Reproduce safely

Create an isolated PostgreSQL database with pgvector, named
`ragagent_review_baseline` or `ragagent_review_after`. Upgrade it using the matching
checkout's Alembic migrations. The script refuses database names outside the
`ragagent_review_` prefix. Never point it at an existing installation or user
corpus. It deletes/replaces only its own deterministic conversation IDs in that
isolated database. It leaves those fixture records available for inspection;
the baseline run's six fixture conversations were explicitly removed after
measurement. No global table truncation is performed.

Use an environment variable for the database URL; the URL is not written to the
artifact. The interpreter needs the project's normal development dependencies.
Set `PYTHONPATH` explicitly so an editable install cannot silently select a
different checkout. Run from the checkout being measured. For example, with
`CHECKOUT` set to the absolute checkout directory and `RAGAGENT_PERF_DATABASE_URL`
already set to the isolated database's URL:

```bash
cd "$CHECKOUT"
PYTHONPATH="$CHECKOUT/src" python scripts/benchmark_messages.py \
  --checkout "$CHECKOUT" --label after \
  --output docs/benchmarks/messages-after.json
```

The frozen baseline predates the script's addition. To measure that checkout,
pass the absolute path to this script while retaining the baseline `PYTHONPATH`:

```bash
cd "$BASELINE_CHECKOUT"
PYTHONPATH="$BASELINE_CHECKOUT/src" "$PROJECT_PYTHON" \
  "$CURRENT_CHECKOUT/scripts/benchmark_messages.py" \
  --checkout "$BASELINE_CHECKOUT" --label before \
  --output "$CURRENT_CHECKOUT/docs/benchmarks/messages-before-repeat.json"
```

`PROJECT_PYTHON` is an absolute path to an interpreter with the development
dependencies, such as the current checkout's `.venv/bin/python`. Run after a
reviewable implementation commit so `source_commit` identifies the measured
code. Do not relabel measurements taken from a dirty checkout as that commit.

## Additional measurements

The original full-history comparison must retain its request semantics. New
cursor endpoints and initial latest-50 loading require separately labeled
measurements: initial latest page, the next older page, an incremental
`after_ordinal` request with one new message, and an empty incremental response.
Record the returned ordinal range, request parameters, response bytes, SELECT
count and raw latency samples. A bounded initial page can legitimately return
fewer rows than full history; it is a different request, not a before/after
substitute for the all-messages comparison above.

After measurements are **Not measured** until the Phase 2 backend is complete,
committed and executed against its separate upgraded database. Multilingual
embedding/reranker quality is also **Not measured**: the review environment has
no cached weights and its configured proxy rejects Hugging Face access with
CONNECT 403. Synthetic API data must not be used to claim retrieval gains.
