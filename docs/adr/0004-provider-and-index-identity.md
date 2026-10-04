# ADR 0004: Provider configuration and embedding-space identity

Date: 2026-10-04. Status: accepted for the engineering repair; validation is
recorded in the stage log.

## Problem

Independent chat roles need consistent environment resolution without persisting
keys. A hosted embedding model name and dimension do not identify its vector
space when two endpoints serve different weights under that name. Rebuilding
local models per request also makes configuration ports costly to use.

## Decision

Resolve runtime configuration consistently for Compose and local development;
persist only nonsecret provider/model mapping and environment-variable names.
Runtime lookup prefers process environment (including explicit empty values),
then reads `.env` without exporting its secrets or interpolating file values.
Instantiate each agent independently and freeze the execution's actual mapping
for evaluation provenance. No mock is used as a production fallback.

Hosted embedding identity uses a versioned hash of configured
backend/model/dimension/normalized endpoint and optional revision. Reject URL
credentials, query parameters and fragments. Default local fingerprints remain
compatible, with a configured revision becoming part of identity.
Limit hosted prefixes to `openai`, `cohere`, `cohere_chat` and `voyage`; unsupported
prefixes fail closed. Cohere/Voyage require explicit API bases. OpenAI-compatible
services use an OpenAI-prefixed model with an explicit base.

Freeze OpenAI's effective endpoint at adapter construction: explicit embedding
base, already loaded LiteLLM global base, runtime `OPENAI_BASE_URL`, runtime
`OPENAI_API_BASE`, then the canonical OpenAI v1 endpoint. Fingerprint and SDK
transport use the same saved normalized base, so later environment/global
changes cannot silently retarget a constructed adapter. Hosted revision is an
operator index-identity label, not remote model-weight pinning; local revision
is passed to model construction.
Stored vectors are eligible only for the query embedder's identical fingerprint.
Changing identity requires reindexing; changing dimensionality requires migration
as well. Use bounded process-level configuration caches and synchronized first
loading for local model objects while keeping database sessions request/job
scoped. RQ work subprocesses do not share that cache across jobs.

Unknown provider costs make the total incomplete; expose a known subtotal and
unknown-call count rather than a misleading complete number. Never include
credentials in fingerprints, logs or exported configuration.

## Consequences

Legacy fingerprinted vectors may require reindexing after this repair. Identical
endpoint/model configuration cannot detect silently replaced remote weights;
operators must use an explicit revision and rebuild when changing them. Real
provider compatibility and model-weight inference require separate runtime checks.
