# Architecture decisions

Records 0001–0006 were written for the 2026-10-04 repair; 0007 records the
2026-10-06 conversation/desktop product upgrade. They document decisions made in
this work, not recovered historical ADRs or prior review approvals.
Implementation and check outcomes are recorded separately in
[stage-log.md](../stage-log.md). Acceptance requirements are in
[MASTER_SPEC.md](../MASTER_SPEC.md).

| ADR | Decision |
|---|---|
| [0001](0001-structural-section-identity.md) | Structural section identity and display-path separation |
| [0002](0002-gated-evidence-and-citation-release.md) | Gate-accepted evidence and deterministic citation release |
| [0003](0003-versioned-tasks-and-bounded-evidence.md) | Content-aware task completion and bounded retry evidence |
| [0004](0004-provider-and-index-identity.md) | Independent provider configuration and embedding-space identity |
| [0005](0005-durable-dispatch-and-terminal-events.md) | Durable dispatch, state reconciliation and terminal SSE drain |
| [0006](0006-review-corrections.md) | Review corrections for evidence, source/model identity, secrets and partial evaluation accounting |
| [0007](0007-local-conversations-and-desktop.md) | Local conversations, bounded memory isolation, existing Run/SSE lifecycle and separate Tauri/Web transports |
