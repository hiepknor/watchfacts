# ADR-003: Use SQLite For Local Cache And History

## Status

Accepted

## Date

2026-05-12

## Context

The bot is self-hosted and intended to run on a local machine or small Linux server. It needs local cache, dedupe records, and query history without requiring a managed database.

## Decision

Use SQLite at:

```text
data/bot.db
```

SQLite stores query history, listing records, and query-result relationships.

## Alternatives Considered

### PostgreSQL

- Pros: Strong concurrency and production features.
- Cons: Extra server/service for a small single-bot deployment.
- Rejected for initial scope.

### JSON Files

- Pros: Simple and inspectable.
- Cons: Harder to query and update safely as records grow.
- Rejected.

### In-Memory Cache Only

- Pros: Simplest runtime.
- Cons: Loses dedupe/history on restart.
- Rejected.

## Consequences

- Local deployment stays simple.
- `data/bot.db` must be ignored and backed up if the operator cares about history.
- Schema changes should be documented and tested.
