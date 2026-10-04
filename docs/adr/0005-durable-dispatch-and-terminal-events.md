# ADR 0005: Durable dispatch and terminal event delivery

Date: 2026-10-04. Status: accepted for the engineering repair; validation is
recorded in the stage log.

## Problem

A database Run commit followed by a Redis enqueue has a crash window. An RQ
work subprocess can die without running an ordinary failure callback. Finally,
an SSE reader can observe terminal Run status after fetching an older event
list and prematurely emit `done`.

## Decision

Commit dispatch intent in PostgreSQL with the Run, dispatch idempotently using
the Run identity, and retry interrupted dispatch through a worker-side mechanism.
Reflect ordinary failures and abnormal work-subprocess death in durable Run and
paper state, and reconcile transport state from an API-lifespan coordinator
every 15 seconds, independently of the RQ worker. Atomic unique RQ IDs and a
database execution claim handle repeated delivery. Queued Runs have a fixed
1800-second bound; running Runs have the 1800-second job timeout plus 120-second
grace. These are constants, not user settings. PostgreSQL is the
job/result authority; Redis remains the transport.

Persist terminal status and its terminal event boundary together. SSE replay
must deliver all persisted events through that boundary before `done`, even
when completion occurs between polling reads. Keep cursor replay monotonic and
preserve proxy event-stream behavior.

Expose process liveness separately from DB/Redis/research-worker readiness.
Compose checks the latter. A real empty-corpus missing-key job and proxied SSE
are the deployment smoke; successful inference is a separate verification.

## Consequences

Delivery is recoverable and duplicate transport submissions must be harmless;
this is not automatic checkpoint restoration of a partially executed graph.
Failure handling requires real RQ subprocess tests, and SSE requires a database
concurrency test. A new Alembic revision carries durable-dispatch schema changes.
