# ADR-011: Isolate The Result-Page Web Runtime

## Status

Accepted

## Date

2026-10-04

## Context

The Telegram bot and result-page web adapter ran from the same image, received
the same `.env`, and mounted the complete runtime data directory. This gave the
public-facing web process access to Telegram/OpenAI secrets, WatchFacts browser
state, Playwright, and bot logs even though it only needs result artifacts and
feedback persistence.

Both processes also write the same SQLite database. Synchronous database and
filesystem calls inside async handlers could block their event loops, while
SQLite used rollback journaling with a five-second busy timeout.

## Decision

- Build separate `bot` and `web` Docker targets and use distinct image tags.
- Give the web service only explicitly selected non-secret settings.
- Share only `data/database/` and `data/result_pages/`; mount browser state and
  logs into the bot only.
- Store browser state under `data/browser/` and SQLite under `data/database/`.
- Migrate legacy runtime files without overwriting destinations; use SQLite's
  backup API for the database.
- Run blocking result-page, feedback, and readiness work in worker threads.
- Use SQLite WAL mode with normal synchronization and the existing busy timeout.
- Separate `/livez` from `/readyz`, retaining `/healthz` as a compatibility
  alias for readiness.
- Throttle full result-artifact cleanup scans with a shared timestamp marker.

## Consequences

The web image no longer contains Playwright and cannot read the WatchFacts
session or bot secrets through its Compose configuration. A compromised web
process still has write access to the shared database and result artifacts, as
required for feedback and expiration; these remain explicit trust boundaries.

SQLite remains appropriate for the current single-host deployment. WAL files
must stay beside the database, so the whole `data/database/` directory is
mounted rather than an individual database file. Horizontal replicas would
require a different write-ownership or database design.

The layout migration leaves legacy files in place for rollback. Operators may
remove them only after validating the new deployment.
