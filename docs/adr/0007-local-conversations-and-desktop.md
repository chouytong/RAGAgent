# 0007: Local conversations above independent evidence workflows

Date: 2026-10-06. Status: implemented product-upgrade decision, subject to code
review and the actual checks recorded in [stage-log](../stage-log.md).
This record does not certify real-model quality, platform installers or production
deployment acceptance.

## Problem

The existing React UI submits isolated RAG/Research Runs and restores only a last
Run ID. Follow-ups have no formally persisted conversation context; no inspectable
memory or independent desktop entry exists. Adding a second task engine, cloud
memory service or whole-history model prompt would weaken the existing provenance,
failure handling, privacy and cost controls.

## Decisions

1. Reuse PostgreSQL for Conversation, ordered Message, ConversationSummary and
   explicit Memory. Add Alembic `0004` to upgrade existing databases while retaining
   knowledge-base data and legacy Runs. No SQLite/cloud conversation store is
   introduced. Conversation mode chooses the existing independent rag/research
   graphs; it does not merge their responsibilities.
2. Commit user/assistant placeholders, Run and durable dispatch intent together.
   Client UUIDs deduplicate retransmissions and a partial unique index permits one
   active turn per conversation. Existing Redis/RQ dispatch/reconciliation and
   terminal SSE drain remain the only task system. Final assistant content, Run
   status and terminal event share a transaction. Retry keeps prior attempts;
   cancellation revokes publication before a best-effort transport stop.
3. Build bounded context using recent messages, a deterministic rolling extractive
   summary and explicitly added structured memories. The summary records source
   message IDs and is lossy unverified context, without a separate LLM summary
   charge. UTF-8 bytes provide a conservative rewrite-input token estimate, including
   fixed instruction/schema/framing; actual provider usage is recorded separately.
   This does not substitute for tokenizer measurement or graph/output budgets.
4. Use the existing retriever chat adapter to contextualize follow-ups into a
   standalone question. Validate source-linked referents, introduced numerical
   premises and unresolved common references. Intersect structured constraint
   filters by code and fail on conflicting scopes. Retain original/contextualized
   queries and context versions/budgets in Run results and execution events.
5. Enforce **Memory ≠ Evidence** at the input boundary. Only the standalone query
   and filters enter the selected graph. Conversation text never becomes a paper
   chunk/EvidenceRecord or analyst/reviewer support. Fresh retrieval, exact spans,
   evidence gate and claim/evidence citation validation remain required for every
   scientific answer. Historical assistant drafts cannot bypass this path.
6. Replace isolated-task presentation with the same React chat UI for Web/desktop:
   persisted sidebar/history, Markdown/code, citations, memory management and
   collapsed execution details. Keep Knowledge Base, Settings and Evaluation and
   existing single-run APIs. Expose real deletion of memory/summary/history and
   associated Runs/events; explain independent caches/backups and retained papers.
7. Wrap React in Tauri 2 rather than rewriting it or embedding infrastructure.
   Scoped native commands use a fixed `http://127.0.0.1:8000` Rust bridge, disabled
   proxies/redirects and allowlisted API/resource routes. Renderer navigation and
   network capabilities are constrained. This avoids weakening same-origin Web
   security to accommodate a desktop origin. Python/PostgreSQL/Redis remain local
   services started separately; existing Compose and Web fallback remain usable.
8. Add conversational evaluation using actual configured contextualization,
   independent graphs/current retrieval and a separate judge. Record context
   resolution, evidence grounding, memory isolation and long-summary dimensions,
   complete-case denominators, partial checkpoints and case-level resume identity.
   Synthetic regression fixtures are not a benchmark or successful real inference.

The added direct frontend packages were checked against their installed package
metadata/notices: `react-markdown` 10.1.0 and `remark-gfm` 4.0.1 are MIT;
`@tauri-apps/api` and `@tauri-apps/cli` 2.12.1 declare `Apache-2.0 OR MIT`.
The resolved Cargo metadata was also checked: Tauri/tauri-build, serde/serde_json,
reqwest, futures-util, base64 and uuid offer MIT/Apache-2.0; tokio, tokio-util and
open declare MIT. GTK/WebKit are separately licensed dynamic system dependencies.
Package/Cargo lockfiles retain exact resolved dependencies. This direct-package
review is not a blanket license certification of all platform libraries, model
weights or paper contents; their independent notices/licenses still apply.

## Consequences and limits

Local default services persist chat/history/memory on the user's machine, while
remote inference adapters send necessary text to their providers. Local storage
does not imply all-local inference. Runtime credentials remain separate; known
patterns are rejected from context fields without pretending every free-text
secret can be detected.

Rolling extracts can lose ordering/entities in long conversations, and model
contextualization/verification can err. Unknown/ambiguous context fails explicitly
but these controls do not prove perfect semantic resolution or adversarial
resistance. Literal evaluation labels also miss paraphrases and need human review.
One active turn per conversation preserves consistent context at the cost of
in-conversation parallel turns. RQ worker concurrency remains the existing setup.

Clear memory retains history, so a later turn may rebuild summary; full clear/delete
also removes the conversation's Runs/events/dispatch records. It does not remove
paper data, independent evaluation files, downloaded desktop documents or backups.
Cancelling/deleting cannot undo a dispatched provider's cost or external processing.

Desktop builds require native platform libraries/WebView. Linux tests do not certify
Windows/macOS packaging/signing, and a successful type check does not prove GUI
startup. Actual build/GUI/network checks and real-model/PDF limitations belong in
the stage log and deployment documentation.
