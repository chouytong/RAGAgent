# ADR 0001: Structural section identity

Date: 2026-10-04. Status: accepted for the engineering repair; validation is
recorded in the stage log.

## Problem

A rendered heading path is not a unique identity. `['A / B']` and `['A', 'B']`
render the same path, while repeated headings may name different nodes. Using
that string as the ingestion key can attach exact chunk text to the wrong
section hierarchy and undermine citation provenance.

## Decision

Keep a stable structural identity separate from heading/display path. Preserve
parser section-node identities and parent identities through chunking and
persistence; keep displayed headings for users and existing section filters.
The parser assigns distinct IDs to heading occurrences and carries ancestry in
`section_ids`. Ingestion uses JSON-encoded node ancestry; older fixtures without
IDs use the JSON heading array, which avoids separator collisions but cannot
identify repeated identical occurrences.
Schema changes use a new Alembic migration. Existing records must be handled
explicitly rather than pretending previously flattened identity can recover
lost hierarchy: each receives `legacy:<UUID>`. Reingestion is required where the
original structure was lost.
Downgrade refuses to recreate the old unique display-path constraint when new
duplicate display paths exist; it must not delete structural nodes to proceed.

## Consequences

Heading delimiters and repeated names do not merge distinct structural nodes.
Fixture coverage must include flat versus nested collisions, repeated headings
and parent links. Parser fidelity on real PDFs remains a separate verification
requirement; structural keys do not prove Docling inferred the right headings.
