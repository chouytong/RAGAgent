# 0006: Close review findings at their trust and persistence boundaries

Date: 2026-10-04. Status: implemented repair decision, subject to code review.
This record describes the changes, not a completed real-model benchmark or
deployment acceptance. Actual check outcomes belong in [stage-log](../stage-log.md).

## Problem

Static review identified provider secret interpolation, destructive numeric/table
cuts, claim-only citation approval, unfrozen source/model identities, first-query
reranking, an unavailable reembedding procedure, failure-cost loss, batch-result
loss, omitted embedding charges, duplicated model input and late chunk-setting
validation. Each can affect evidence correctness, reproducibility or operating
cost even when infrastructure is healthy.

## Decisions

1. Parse provider YAML before substituting only safe nonsecret `*_MODEL` variables
   in model fields. API writes reject unresolved templates. Validate local Host
   and same-origin browser writes; proxies preserve Host. Keys stay separately
   resolved at inference. These checks protect the local application, not shared
   deployment authentication.
2. Keep numeric tokens intact. Recognize Markdown table rows, carry headers and
   captions as independent `SourceContext` quotes and retain original source/chunk
   offsets. Formulas, unknown table layouts and indivisible long rows may exceed
   the lexical target rather than be destructively split. Validate overlap/target
   during settings construction.
3. Require unique support pairs for every citation attached to a claim. A claim
   supported by one paper cannot validate another paper's unrelated citation;
   missing or unknown pairs require revision. Send exact quote payloads once,
   without repeating full chunk content, through both analysis workflows.
4. Resolve official arXiv `vN` identity before download and retain each version
   independently, including equal-byte versions. Keep source status explicitly
   unknown until operator annotation; exclude known withdrawn/retracted papers
   from both retrieval channels. Legacy unversioned sources stay version-unknown.
5. Rerank multi-query candidates against the original question/current subtask;
   use all queries as the direct-call fallback and preserve targets in expansion.
6. Supply an explicit `ragagent.reindex` CLI. Lock and reembed each indexed paper
   in its own transaction without replacing chunks or citation IDs. A dimension
   change still requires migration; parser/chunk repairs require reingestion.
7. Resolve local Hub revisions to immutable SHAs before fingerprinting/loading,
   hash directory artifacts, and use `local:v2` identity. Old vectors must be
   reembedded. Cross-process reproduction needs recorded SHA configuration.
   Default chat uses a dated model identifier and records returned identities;
   hosted revision labels do not freeze server weights.
8. Persist worker Run usage before/after paid calls, including hosted embeddings
   and unknown in-flight/failed charges. Checkpoint evaluation usage updates as
   well as case results, retain partial
   terminal artifacts, and explicitly resume only matching source/configuration/
   corpus identities. New-attempt totals stay separate from prior-attempt history;
   prior persisted Run usage supplements the artifact with `usage_source=persisted_run`
   when available. Synchronous search/provider tests return `current_request`
   usage only; reindex stdout reports `current_attempt_cumulative`. These paths
   have no durable Run billing ledger and cannot recover lost responses/output.
   Evaluation timeout defaults to 7200 seconds, ordinary jobs to 1800, with the
   dispatch-frozen timeout shared by RQ and reconciliation plus 120 seconds grace.

## Consequences and limits

Old local indexes require an operator reembedding step. Existing chunks do not
gain missing table context or structural provenance by reembedding. Source status
is manual and can become stale; old reports do not automatically refresh after a
withdrawal annotation. Completed evaluation checkpoints save repeat work, while
an interrupted case must execute again and prior charges remain part of history.

Model-based review/judging can still err, exact offsets do not prove PDF fidelity,
and temperature 0 does not prove deterministic inference. Real PDF/model quality,
adversarial prompt resistance and research metrics require representative inputs,
weights, provider access and human annotations. No such outcome is implied by
this decision or by mock/contract tests.
