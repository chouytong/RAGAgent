# V1 implementation and acceptance baseline

## Origin and scope

This document was assembled during the 2026-10-04 engineering repair from the
existing v1 architecture, contributor rules, API and deployment documentation,
and the user's engineering-review checklist. The reviewed branch did not contain
a `MASTER_SPEC.md`. This is a reconstructed implementation/acceptance baseline,
not a recovered original specification or evidence of prior acceptance.

Its scope is the existing trusted local, single-user scientific knowledge base,
RAG and research application. It does not add public hosting, multi-tenancy,
authentication, automatic checkpoint resumption, new databases, or a performance
target. Requirements below describe observable behavior; a source file's presence
does not establish that behavior. Design decisions for this repair are recorded
in [ADR](adr/README.md), not represented as historical approvals.

## Acceptance vocabulary

- **PASS**: complete behavior demonstrated with code and appropriate tests/runtime evidence.
- **PARTIAL**: part of the required behavior is implemented or verified.
- **FAIL**: observed behavior violates the requirement.
- **MISSING**: required implementation or supporting material is absent.
- **UNVERIFIED**: available evidence cannot establish the behavior.

This document defines requirements rather than awarding those statuses. The
[stage log](stage-log.md) records actual commands, results and limitations for a
specific commit/environment. A fixture test may demonstrate routing or numerical
correctness; it cannot establish real PDF fidelity, downloaded-model inference,
provider compatibility or literature-retrieval quality.

## Engineering boundaries

Domain contracts are typed and validated. Parsing, embedding, reranking and chat
are independent ports; external SDK calls remain in adapters. Dependents point
inward to contracts and persistence services. LangGraph uses current `StateGraph`
primitives and explicit business budgets in addition to a recursion limit.
PostgreSQL with pgvector and English FTS is the evidence store; Redis/RQ is the
job transport. Do not introduce additional infrastructure simply to demonstrate
a technique. See [AGENTS.md](../AGENTS.md) and [architecture](architecture.md).

## Required behavior and acceptance evidence

| ID | Area | Required behavior | Evidence required for acceptance |
|---|---|---|---|
| KB-01 | Input | Accept bounded PDF upload and validated arXiv IDs; deduplicate original content by hash. Reject arbitrary remote URLs and invalid inputs. | API tests for limits, validation and deduplication; real upload/import runtime check. |
| KB-02 | Parsing | Preserve original PDF and structured parse output, exact element text, section hierarchy and page provenance. Section identity is structural and cannot collide because a heading contains a path separator or repeats. | Parser/chunker fixtures with colliding/repeated headings and multiple pages; representative real PDF inspection. |
| KB-03 | Indexing | Persist sections, chunks and configured-dimensional embeddings atomically; failed ingestion remains explicit and retryable. | PostgreSQL ingestion rollback/idempotence tests; real PDF-to-index run with approved model weights. |
| KB-04 | Knowledge Base | Inspect paper metadata, status, chunks and source PDF; correct metadata and record chunk entity occurrences. Paging must permit access beyond the first result page. | API and UI behavior tests; source-PDF and chunk inspection. |
| RET-01 | Hybrid retrieval | Execute real filtered pgvector cosine search and PostgreSQL FTS, fuse unique chunk ranks using `1/(k+rank)`, and optionally rerank with the independent CrossEncoder port. | PostgreSQL integration tests, hand-calculated fusion assertions, real reranker inference. |
| RET-02 | Filters | Apply paper, author, year, venue, section and entity predicates to both SQL retrieval paths before ranking. Lists are OR within a field and fields are AND. UI editing preserves spaces and separators. | Positive/negative isolation tests on both retrieval paths; frontend input/submission tests. |
| RET-03 | Index identity | Exclude incompatible embedding spaces, including same-name models at distinct hosted endpoints. Changing index identity requires reindexing; changing dimensionality also requires migration. | Fingerprint unit tests and PostgreSQL exclusion test; explicit configuration documentation. |
| EVI-01 | Provenance | Every evidence item identifies stored paper/section/page/chunk and an exact quote equal to `content[start:end]`. | Span and identifier rejection tests, source/chunk API checks, real paper inspection. |
| EVI-02 | Gate and release | Only evidence accepted by the configured Gate can support released claims. Formatting derives citations from validated structured IDs; model text cannot inject an unverified citation. | Negative tests for low-score-only support, unknown text citations and invalid spans in both graphs. |
| EVI-03 | Verification | Require one verdict per submitted claim, reject unknown/duplicate/missing verdict IDs, require semantic support and required-aspect coverage. Model judgments remain explicitly model-based. | Scripted verifier rejection tests and output assertions; real-model validation remains separate. |
| RAG-01 | RAG workflow | Plan, retrieve, gate, generate structured claims, verify and release or explicitly refuse. Preserve useful prior evidence during bounded retries and retain user metadata restrictions. | Actual LangGraph execution tests for retry union, filter preservation, exhaustion and refusal. |
| MA-01 | Specialist workflow | Supervisor plans/routes; Retriever searches/expands; Analysis produces evidence-linked facts; deterministic Synthesis formats; Reviewer audits claims and aspect coverage. | Scoped invocation and conditional-routing tests; released report assertions. |
| MA-02 | Replanning and termination | Changed tasks cannot inherit completion solely by reusing an ID. Evidence/query pools are bounded. Retrieval, revision and iteration budgets terminate feedback loops. | Reused-ID replan, pool-bound and exhaustion tests using the compiled graph. |
| PRO-01 | Multiple providers | Each role has its own nonsecret provider/model mapping; embeddings and reranker are independent. No production mock fallback. Missing credentials/capability errors are explicit. | Provider configuration/SDK tests and separate real-provider connectivity/inference checks. |
| API-01 | API | Validate requests, return safe errors and asynchronous Run IDs; expose durable status/results and source inspection. Separate process liveness from DB/Redis/queue-worker readiness. Long-running model work executes in the worker. | API integration tests and Compose request smoke. |
| JOB-01 | Reliable jobs | Commit durable dispatch intent with each Run, retry interrupted dispatch idempotently, and reflect RQ failure/dead-worker outcomes in Run/paper state. | Database/Redis fault-window and actual worker-subprocess-death tests, not direct callback calls alone. |
| API-02 | SSE | Replay persisted events after a cursor and drain the terminal event history before `done`, including concurrent completion. | PostgreSQL concurrency/replay tests and proxy event-stream smoke. |
| UI-01 | Frontend | Offer Knowledge Base, search/RAG/research, evaluation and nonsecret provider settings; distinguish drafts, failures and final outputs. A citation opens source text/PDF page where the browser supports PDF viewing. | Type checks/build plus targeted browser or DOM behavior tests. |
| EVA-01 | Retrieval metrics | Compute recall, precision, MRR and binary nDCG from actual rankings and validated corpus labels; retain ablation rankings and measured latency. | Hand-calculated metric tests and real PostgreSQL ablation integration. |
| EVA-02 | Generation metrics | Judge actual final output/status with evidence, claims and human-supplied expected aspects. Rejected/refused output cannot be awarded answer completeness merely from a judge assertion. Report model-based judgments and undefined values honestly. | Judge-payload and refusal tests; explicit missing-label policy; independently reviewed benchmark required for quality claims. |
| EVA-03 | Reproducibility | Freeze configuration actually used, retain dataset/index/source identity, final outputs, evidence and raw judgment, and record latency/token/cost provenance without keys. Unknown charges must not look like a complete numeric total. | Artifact schema/content tests and execution configuration-change test. |
| OBS-01 | Observability | Persist ordered execution events and safe error codes; log trace/event metadata without secrets, prompts or PDF text. Distinguish known cost subtotal from incomplete total. | Logging/serialization tests and failure-path inspection. |
| DEP-01 | Local deployment | Locked dependencies and Compose start PostgreSQL, Redis, migration, API, worker and UI on loopback. Preserve corpus/cache volumes and provide explicit model/network requirements. | Build/start/API/UI/SSE smoke with documented architecture and actual model limitations. |
| QA-01 | Tests and CI | Unit tests need no network, paid API, model download or DB. CI requires PostgreSQL/pgvector tests, migration round trip, lint/types and frontend build/behavior checks appropriate to the change. | Actual check output; integration skips are not passes; hosted CI result is separate from local reproduction. |
| SEC-01 | Local security | Keep keys in runtime secrets, never settings/DB/logs/artifacts; validate file and arXiv inputs; retain loopback exposure and independent paper/model licenses. | Targeted tests/config review. Public deployment security is outside v1 and must not be implied. |
| DOC-01 | Documentation | Describe current implementation, configuration, migrations, failure behavior and unverified capabilities accurately. Record repair decisions and actual validation without invented benchmark or history. | Documentation-to-code review and stage log for the exact revision. |

## Required verification boundaries

At creation of this baseline, the previous audit's 48 passing tests and Compose
smoke describe the pre-repair commit only. They do not validate subsequent
repairs. The current repair results belong in the stage log after execution.

The following remain **UNVERIFIED** until explicit runtime evidence is recorded:

- Real Docling conversion/OCR/table/equation fidelity and a PDF-to-embedding-to-index run.
- Downloaded embedding and CrossEncoder weight inference and representative retrieval quality.
- Real arXiv network import and successful chat inference for each configured provider/service.
- Real-model semantic support and completeness judgments, including correlated reviewer/judge error.
- Human-annotated literature benchmark outcomes, retrieval gains, sustained latency/throughput and memory behavior.
- Hosted GitHub CI and independent model-weight license or dependency-vulnerability verification beyond documented checks.

No benchmark dataset or measured improvement is created by this document.
Synthetic fixtures remain **DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED**.
