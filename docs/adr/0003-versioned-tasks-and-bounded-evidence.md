# ADR 0003: Content-aware tasks and bounded evidence retention

Date: 2026-10-04. Status: accepted for the engineering repair; validation is
recorded in the stage log.

## Problem

Replanning may reuse a task label for a different query or missing aspect.
Matching completion by label alone skips the replacement task. Replacing the
whole evidence collection on each RAG retry also discards support found earlier.

## Decision

Associate completed research tasks with their full validated task content,
not an ID alone. Preserve completion only when all task fields remain equal
after user restrictions are applied. Carry useful
evidence across RAG and research retries through a deduplicated, bounded pool;
The default configured limits are 48 for RAG and 96 for research, with earlier records
first. A truncated pool records `evidence_budget_exhausted`. Bound accumulated
queries to six per plan/task. Preserve user restrictions during
expansion and retain required aspects during replanning.

Use explicit retrieval/revision/iteration limits in state. LangGraph's recursion
limit is a final guard, not the workflow's only termination condition. A final
report still requires Reviewer PASS; a retrieved task is not a verified report.

## Consequences

Changed tasks run again and complementary evidence survives retries. Bounded
retention constrains context/memory but may require refusal when the available
budget cannot preserve enough support. Compiled-graph tests must cover reused
IDs, complementary evidence, filter preservation and all exhaustion routes.
