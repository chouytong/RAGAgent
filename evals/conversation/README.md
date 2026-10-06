# Conversation evaluation datasets

**DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED.** No manually
annotated conversation benchmark or measured product improvements are bundled.

`schema.json` describes `ConversationEvaluationDataset`. Start from
`annotation-template.json`, read the original papers, label every turn and use
the actual indexed chunk/paper IDs. The unannotated template is intentionally
not runnable. Human datasets require `annotated_by` and `annotated_at` on every
turn; that declaration is user supplied, not independently certified.

The synthetic `demo.json` uses only the explicit corpus from
`scripts/seed_demo.py`. Seeding uses configured real embeddings and may incur
provider charges. The demo runner uses the configured production models,
context builder and retrieval; there is no mock production fallback. Unknown
corpus IDs, missing answer/aspect/relevance labels, or missing dimension-specific
labels reject execution. Refusal cases require an explicit `expected_refusal`
label and expected refusal text, and can omit chunk/aspect labels.

Submit to `POST /api/evaluations/conversation`:

```json
{"dataset": {"dataset_id": "...", "label_source": "human", "description": "...", "cases": []}}
```

Replace the illustrative empty cases with the complete annotated dataset.
Each case has `mode` (`rag` or `research`), `dimensions`, ordered `turns`, and
optional `seed_messages`/explicit `memories`. Seed messages use source IDs,
ordinals, roles and contents; they are unverified conversational context.
Every executed turn appends its actual released answer/refusal to the following
turn's context. Intermediate drafts are excluded. Cases are independent and do
not create user Conversation records.

The four dimensions are:

- `context_resolution`: label `expected_context_terms` on follow-up turns. The
  metric checks their case-insensitive presence in the actual rewritten query.
  This lexical diagnostic does not prove semantic equivalence; inspect recorded
  referents, source IDs and original/contextualized queries as well.
  For a comparative follow-up such as "Which one has the largest sample size?",
  label both exact candidate names and the comparison operator (`largest`).
  The production rewrite guard requires at least two distinct source-backed
  candidates for "which one" and preserves comparison words. Dropping a
  candidate or `largest` fails before retrieval; it cannot select a winner from
  unverified historical numbers. The annotation template includes a deliberately
  unannotated comparison case; the one-paper synthetic demo is not a comparison
  benchmark.
- `evidence_grounding`: labels use current corpus chunks and expected answers.
  The original RAG/Research graphs enforce evidence and citations. The separate
  judge scores citation pairs, answer completeness and explicit refusals using
  exact cited quotes. These are MODEL_BASED judgments, not human ground truth.
- `memory_isolation`: include an intentionally incorrect history/memory
  assertion and label `forbidden_answer_fragments`. Passing requires their
  absence, a fully answered gold response (or correctly labeled refusal),
  valid source provenance and evidence returned by this turn's retrieval.
  An empty answer cannot pass a normal factual isolation case. Phrase checks
  cannot catch every paraphrase; inspect artifacts and use diverse adversarial
  labels. History itself is never turned into evidence.
- `long_summary`: supply enough seed history or preceding turns to activate the
  production rolling summary. Mark the follow-up `expects_summary=true` and
  label old referent terms that no longer appear in recent messages. Passing
  requires an actual summary, correctly resolved terms, compliance with the
  input-context budget and a grounded gold answer. The summary is a bounded,
  lossy, extractive context artifact, never citation evidence.

`summary` groups metrics by dimension. `dimension_case_counts`,
`dimension_evaluated_case_counts` and `missing_dimensions` disclose coverage;
a subset dataset does not constitute a full four-dimension evaluation. Undefined
metrics are null. Summary metrics include only fully evaluated conversations;
per-turn outputs and safe failure stages remain available for failed cases.
One failed turn stops that case; other cases continue.

`results.json`/`results.md` use the existing artifacts and record source/corpus
identity, actual provider mappings, context version/configuration/rewrite prompt,
summary source IDs, enforced filters, current retrieval plans, exact evidence,
judge inputs and per-provider token/cost snapshots. The context budget uses the
documented conservative UTF-8 byte estimate, not measured provider token counts;
actual usage remains separate. Context rewriting is charged to `retriever` and
hosted embeddings are included in workflow totals. Unknown charges remain null.

Resume using the same endpoint and dataset with `resume_run_id`. Only completed
whole conversation cases are reused. Failed/pending conversations restart from
their seed context, preserving prior-attempt fees separately. Source, dataset,
corpus, actual model, graph, context-budget/version/prompt and judge identities
must match. There is no hidden mid-conversation checkpoint replay.

Tests use scripted providers only in the test suite and execute actual context
and graph code. PostgreSQL integration tests exercise real pgvector/FTS retrieval.
Those synthetic correctness checks are not results on scientific literature.
