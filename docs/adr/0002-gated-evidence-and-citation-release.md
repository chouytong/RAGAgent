# ADR 0002: Gated evidence and deterministic citation release

Date: 2026-10-04. Status: accepted for the engineering repair; validation is
recorded in the stage log.

## Problem

Counting high-score evidence is insufficient if generation and verification
still receive rejected evidence. Separately, validating structured
`evidence_ids` does not validate citation-looking text embedded in a claim.

## Decision

Use an explicit Gate-accepted evidence collection for generation, verification
and release in both RAG and research. Retrieval can retain diagnostic candidate
rankings, but those candidates are not automatically claim support. Thresholds
are retrieval heuristics, never calibrated scientific-confidence probabilities.

Claims contain text and structured evidence IDs. Reject model-authored citation
syntax in text; only deterministic formatting emits `[E:UUID]` from validated
IDs. Check exact chunk spans and the verifier's complete ID set. Unknown,
missing or duplicate verdicts fail closed. Required aspects must be supported
before a final answer/report is released.
The semantic reviewer receives only eligible claims' cited IDs, exact quotes
and source metadata, excluding uncited records and quote-external chunk text.

## Consequences

The Gate constrains the actual support set, and a fluent draft cannot bypass
that set by inserting a textual citation. Tests must inspect released text as
well as structured verdicts. The semantic verifier remains model-based and
fallible; this decision does not establish human-level scientific correctness.
