# Reference review

Reviewed 2026-10-03 against GitHub REST metadata, repository README files,
license files, official documentation sources and PyPI metadata. This is an
architecture review, not an independent reproduction of upstream benchmarks.
Machine-readable maintenance observations: [snapshot](references/snapshot.json).
`pushed_at` measures repository pushes; it does not establish support quality.
No upstream implementation code was copied.

| Repository | Maintenance observed | License verification | Stack | Learn from | Avoid adopting | Relationship |
|---|---|---|---|---|---|---|
| [rag-research-agent-template](https://github.com/langchain-ai/rag-research-agent-template) | Archived; last push 2024-12-03 | MIT | Python, LangGraph, separate indexing/retrieval/research graphs | Separate ingestion graph and research subgraph, explicit query routing | Archived dependency/API pins, sample documents as a production corpus | Historical graph decomposition reference |
| [ResearchPaL](https://github.com/The-Name-is-Karthik/ResearchPaL) | Not archived; last push 2026-07-28 | README claims MIT; root directory has no LICENSE; GitHub license=null. Treat as unlicensed until clarified | FastAPI, React, LangGraph, Supabase pgvector, PyMuPDF/Adobe, Jina/Gemini | Layout-aware evidence presentation, RRF, clickable page citations | Mandatory hosted services, keyword-frequency substitute for PostgreSQL FTS, removing invalid citations without refusing unsupported claims, importing unverified metrics | Product workflow inspiration only; no code reuse |
| [agenticArxiv](https://github.com/saumyajain03/agenticArxiv) | Not archived; last push 2026-08-03 | No root LICENSE; GitHub license=null. Treat as unlicensed | FastAPI, LangGraph, Docling, OpenSearch, Airflow, Redis, Jina | Planner/researcher/critic separation, ingestion observability, bounded rewrite intent | OpenSearch/Airflow overhead for v1, opaque LLM guardrail scores, domain hard-coding, claims of production quality as evidence | Ingestion and workflow ideas only |
| [literature-review-agent](https://github.com/littlelelephant/literature-review-agent) | Not archived; last push 2026-08-02 | AGPL-3.0 | Python 3.11+, LangGraph, Europe PMC/arXiv, MinerU, local evidence artifacts | Evidence cards, explicit abstract/full-text depth, provenance audits, bounded expansion | Copying AGPL implementation into MIT project; mandatory hosted MinerU; abstract evidence presented as full-text support | Conceptual reference only; no source vendoring or runtime dependency |
| [langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py) | Archived; last push 2026-07-15 | MIT | Python, LangGraph, tool handoff | Context isolation and supervisor delegation concepts; README recommends tools directly | **Package dependency prohibited**; deprecated prebuilt API examples and unbounded handoff loops | Reimplemented using current StateGraph primitives |
| [Docling](https://github.com/docling-project/docling) | Active; last push 2026-10-03 | MIT repository; model weights have independent terms | Python, docling-core document model, layout/table/OCR models | DocumentConverter, DoclingDocument structured items, provenance, heading hierarchy, table export | Flattening to Markdown before provenance extraction; downloading models during core tests; assuming equations are always recognized | Default parser behind a protocol; model caches are deployment concerns |
| [pgvector](https://github.com/pgvector/pgvector) | Active; last push 2026-10-01 | PostgreSQL License (verified LICENSE; GitHub NOASSERTION is not absence of license) | PostgreSQL C extension, SQL operators, SQLAlchemy adapter | Cosine distance, exact filtered search, HNSW iterative scans, FTS + RRF | Combining incomparable raw dense/lexical scores, assuming ANN WHERE filtering retains recall, embedding-model mixing | Shared transactional knowledge store, hybrid retrieval foundation |
| [LiteLLM](https://github.com/BerriAI/litellm) | Active; last push 2026-10-03 | MIT outside enterprise/; enterprise restrictions verified in LICENSE | Python SDK, provider adapters, optional gateway | Unified completion/embedding interface, provider prefixes, usage and optional cost | Enterprise code, unnecessary proxy service, SDK debug logging exposing prompts/secrets, silently dropping unsupported parameters | Sole external chat provider adapter, independently configured embeddings |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Active; last push 2026-10-03 | MIT | Python, typed graph state, conditional edges, streaming/checkpoints | Typed state, partial updates, compiled StateGraph, explicit bounded routing | Treating recursion_limit as the sole business retry guard, arbitrary untyped state, legacy supervisor package | Workflow runtime |

## Current official APIs checked before implementation

The environment permits package/GitHub hosts, but not arbitrary documentation
hosts. Official documentation was read from its canonical GitHub source; this
is not a bypass of the network policy.

- [Multi-agent overview](https://docs.langchain.com/oss/python/langchain/multi-agent),
  [official source](https://github.com/langchain-ai/docs/blob/main/src/oss/langchain/multi-agent/index.mdx): subagents, handoffs, routers and custom workflows are separate patterns. Context engineering determines what each specialist sees.
- [Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents),
  [source](https://github.com/langchain-ai/docs/blob/main/src/oss/langchain/multi-agent/subagents.mdx): specialist calls return scoped results to a central coordinator; background jobs are distinct from Python async.
- [Custom workflows](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow),
  [source](https://github.com/langchain-ai/docs/blob/main/src/oss/langchain/multi-agent/custom-workflow.mdx): StateGraph mixes deterministic and agentic nodes with conditional branches.
- [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
  [source](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/graph-api.mdx): TypedDict/Pydantic state, nodes returning updates, compile before invocation. Use `START`, `END`, `StateGraph`, `add_conditional_edges`, `astream`.
- [Docling quickstart](https://github.com/docling-project/docling/blob/main/docs/getting_started/quickstart.md): `DocumentConverter().convert(path).document`. Use structured document items and provenance rather than plain Markdown splitting.
- [pgvector official retrieval/filtering/hybrid guidance](https://github.com/pgvector/pgvector#hybrid-search): combine PostgreSQL full-text search with rank fusion or cross-encoder; filtered approximate search can under-return. First release uses exact dense search with filter predicates; add ANN only after real measurement.
- [LiteLLM SDK](https://docs.litellm.ai/docs/), [official README](https://github.com/BerriAI/litellm): SDK integration rather than proxy deployment. Verify installed SDK completion and embedding signatures in provider tests.

PyPI observed stable versions: LangGraph 1.2.12, LiteLLM 1.103.2,
Docling 2.133.0, FastAPI 0.142.2, pgvector Python 0.5.0.
Reproducibility uses committed uv/npm lockfiles, not moving latest installs.

## Decisions and license boundaries

1. PostgreSQL + pgvector + FTS is sufficient for v1. Redis supplies a background-job queue; it is not the durable evidence store.
2. Implement Supervisor independently with typed StateGraph and conditional routing. Agents receive validated task/evidence inputs through service protocols.
3. Use normalized entities and citations. Evidence always refers to exact stored chunk text and offsets, paper, section and pages.
4. Chat, embeddings, reranker and parser are independent ports. Production uses real providers; mock providers are explicitly test-only.
5. Model outputs are proposals. Evidence existence/span checks are deterministic; semantic support requires an explicitly recorded verifier result. Scores are heuristics, never calibrated probabilities.
6. Corpus content and model weights retain their own licenses. The project MIT license does not relicense papers or downloaded weights. No GPL/AGPL/no-license upstream implementation is imported.
7. No borrowed benchmark values. Synthetic fixtures are pipeline checks only.
