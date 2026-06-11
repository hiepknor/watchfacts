# ADR-006: Separate Short-Lived Result Handles from Stable Listing Identity

## Status

Accepted

## Date

2026-06-11

## Context

The runtime currently exposes `result_id` in MCP payloads to support follow-up actions (`create_chat_draft`, `report_issue`) by rank/id reference.

`result_id` is generated from query/rank and listing snapshot fields. This makes it stable for short-lived cache references inside one search session, but it changes when ranking or extracted text changes. In operational flows, Hermes/TG can request follow-up a short time after search, then process may span restarts.

At the same time, the runtime keeps SQLite `result_reference_cache` for replayability and computes a normalized `stable_listing_id` based on source URL + normalized listing text.

## Decision

Adopt a split identity model:

1. Keep `result_id` as a short-lived follow-up handle tied to the current runtime payload.
2. Continue persisting result references to SQLite by `search_cache` and `result_reference_cache` for operational continuity.
3. Return `stable_listing_id` in search and generated result-page payloads.
4. Resolve follow-up references by in-memory cache, SQLite `result_id`,
   SQLite `stable_listing_id`, or absolute rank before re-running a search.
5. Use stable listing identity (`stable_listing_id`) for long-lived follow-up and durable issue tracing, derived from listing source URL and normalized text (with fallback to full normalized payload fields if source URL is unavailable).

## Alternatives Considered

### Make `result_id` globally durable

- Pros:
  - Simpler API surface for consumers, single identifier only.
- Cons:
  - Violates existing handle semantics without changing contracts.
  - Requires cache invalidation and backward compatibility handling on ranking changes.
  - Encourages consumers to treat a short-lived handle as persistent.

Rejected because it increases coupling and raises stale-reference risk in restart-heavy environments.

### Use source URL alone as stable identity

- Pros:
  - Easy to compute.
  - Good candidate for dedupe and issue aggregation.
- Cons:
  - Can collide when listings are re-posted and mutate.
  - Does not capture meaningful text drift where listing text is the only stable signal.

Rejected as insufficient for future-proof listing comparison.

### Use database PK only

- Pros:
  - Easy internally.
  - Stable across runtime process boundaries.
- Cons:
  - Not observable to runtime clients.
  - Requires broader migration of existing follow-up flow contracts.

Rejected to avoid breaking MCP flow and Hermes assumptions.

## Consequences

- Follow-up flows remain backward-compatible (`result_id`, rank, query) while adding a stronger returned anchor for durable workflows.
- Restart resilience improves because follow-up resolution can pivot to stable identity stored in `result_reference_cache`.
- Quality investigations can cluster issues by listing source rather than only current page/rank.
- The trade-off is added complexity in reference management, which must remain constrained to internal persistence APIs.
