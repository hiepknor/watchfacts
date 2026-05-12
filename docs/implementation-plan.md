# Implementation Plan

## Overview

The initial implementation phases are complete. Keep this document as the project baseline and use it to verify that future changes preserve the intended architecture.

For new work, add a focused task under "Future Work" before implementing if the change affects behavior, data retention, scraping strategy, or deployment.

## Phase 0: Foundation

### Task 0.1: Runtime Scaffold

Status: mostly complete.

Acceptance:

- [x] `Dockerfile` exists and installs Python dependencies plus Playwright Chromium.
- [x] `docker-compose.yml` mounts `data/` and `logs/`.
- [x] `Makefile` wraps common commands.
- [x] `.env.example`, `.gitignore`, and `.dockerignore` exist.

Verify:

```bash
make check
make build
docker compose config
```

### Task 0.2: Documentation Baseline

Acceptance:

- [x] README explains quick start and commands.
- [x] AGENT.md defines project context and agent rules.
- [x] Specs, roadmap, operations, security, and ADR docs exist.

Verify:

```bash
git diff --check
```

## Phase 1: Core Application Skeleton

### Task 1.1: Add Config Module

Description: Implement typed environment loading and path constants.

Acceptance:

- [x] `app/config.py` loads required and optional environment variables.
- [x] Missing `TELEGRAM_BOT_TOKEN` fails with a clear config error.
- [x] Boolean values support common forms such as `true`, `false`, `1`, and `0`.

Verify:

```bash
python -m pytest tests/test_config.py
python -m compileall app
```

Likely files:

- `app/config.py`
- `tests/test_config.py`

### Task 1.2: Add App Entrypoint

Description: Add `app/main.py` that initializes config and starts the Telegram bot.

Acceptance:

- [x] `python -m app.main` imports successfully.
- [x] Startup logs do not include secrets.
- [x] Missing config exits clearly.

Verify:

```bash
python -m compileall app
```

Likely files:

- `app/__init__.py`
- `app/main.py`

### Task 1.3: Add Telegram Bot Shell

Description: Register basic Telegram handlers and return placeholder responses until search is implemented.

Acceptance:

- [x] `/start` returns a short usage message.
- [x] `/help`, `/settings`, and `/cancel` return visual operational messages.
- [x] Empty messages are rejected.
- [x] Text messages call a search workflow interface.
- [x] Optional Telegram user id allowlist restricts bot usage when configured.
- [x] Telegram pagination result limit can be configured from `.env`.
- [x] Group chats ignore normal text unless the bot is mentioned or replied to.
- [x] Long photo captions and text fallback messages are capped before sending to Telegram.

Verify:

```bash
python -m pytest tests/test_telegram_bot.py
```

Likely files:

- `app/telegram_bot.py`
- `tests/test_telegram_bot.py`

## Phase 2: Deterministic Search Pipeline

### Task 2.1: Matching

Acceptance:

- [x] Query normalization is case-insensitive.
- [x] All query tokens are required.
- [x] Tests cover model/reference and descriptor examples.
- [x] Compound references and multi-listing card segments are handled deterministically.
- [x] Messy live-listing cases are covered, including emoji, keycap digit prices, compact dates, year descriptors, and member/seller metadata boundaries.

Verify:

```bash
python -m pytest tests/test_matcher.py
```

Likely files:

- `app/matcher.py`
- `tests/test_matcher.py`

### Task 2.2: Deduplication

Acceptance:

- [x] Dedupe key uses normalized text, seller, and posted date.
- [x] Repeated listings are removed deterministically.
- [x] Reposted duplicates from the same seller keep the latest posted date in search results.
- [x] Tests cover whitespace/case/punctuation normalization.

Verify:

```bash
python -m pytest tests/test_dedupe.py
```

Likely files:

- `app/dedupe.py`
- `tests/test_dedupe.py`

### Task 2.3: Parser

Acceptance:

- [x] Parser extracts listing candidates from fixture HTML.
- [x] Missing fields are handled gracefully.
- [x] Parser tests do not need network access.

Verify:

```bash
python -m pytest tests/test_parser.py
```

Likely files:

- `app/parser.py`
- `tests/fixtures/watchfacts_listing.html`
- `tests/test_parser.py`

## Phase 3: Browser Integration

### Task 3.1: Login Script

Acceptance:

- [x] `python scripts/login.py` opens Chromium for manual login.
- [x] Authenticated state is saved to `data/watchfacts_state.json`.
- [x] Script does not ask for or store passwords.

Verify:

```bash
python -m compileall scripts
```

Likely files:

- `scripts/login.py`

### Task 3.2: Scraper

Acceptance:

- [x] Scraper loads existing browser state.
- [x] Scraper navigates to configured WatchFacts URL.
- [x] Scraper posts the WatchFacts search form when available and marks server-filtered results.
- [x] Missing/expired session produces a clear error.

Verify:

```bash
python -m pytest tests/test_scraper.py
```

Likely files:

- `app/scraper.py`
- `tests/test_scraper.py`

## Phase 4: Persistence

### Task 4.1: SQLite Schema

Acceptance:

- [x] `data/bot.db` is created automatically.
- [x] Tables exist for queries, listings, and query results.
- [x] SQL uses parameterized queries.

Verify:

```bash
python -m pytest tests/test_db.py
```

Likely files:

- `app/db.py`
- `tests/test_db.py`

### Task 4.2: Search Workflow Integration

Acceptance:

- [x] Telegram query triggers WatchFacts search -> parse -> match -> dedupe -> persist -> format.
- [x] No-result state is user-friendly.
- [x] Errors are logged and surfaced without secrets.
- [x] Large result sets show a summary first, then paginate results with Telegram inline callbacks.
- [x] Server-filtered WatchFacts JSON responses are parsed without over-filtering relevant results.

Verify:

```bash
python -m pytest
make build
```

Likely files:

- `app/telegram_bot.py`
- `app/scraper.py`
- `app/parser.py`
- `app/matcher.py`
- `app/dedupe.py`
- `app/db.py`

## Phase 5: Production Hardening

### Task 5.1: Logging And Observability

Acceptance:

- [x] Structured logs cover startup, query start/end, result counts, and error categories.
- [x] Logs do not contain tokens, cookies, or browser state.

Verify:

```bash
python -m pytest
```

### Task 5.2: Deployment Runbook

Acceptance:

- [x] `docs/operations.md` includes first deploy, restart, logs, backup, and restore steps.
- [x] README links to operations docs.

Verify:

```bash
git diff --check
```

## Future Work

Each item needs a focused spec before implementation:

- Multi-page crawling beyond the current search response.
- Scheduled refresh jobs and proactive alerts.
- Dealer/seller filters.
- Price normalization, currency handling, and numeric sorting.
- Image caching.
- Exporting result sets.
- Additional watch sources.
- Query operators such as optional terms, quoted phrases, or negative filters.

## Checkpoints

After each phase:

- [x] Baseline phases 0-5 complete.
- [ ] `make check` passes for every change.
- [ ] Relevant tests pass for every code change.
- [ ] Docker build passes when runtime files changed.
- [ ] No secrets are staged.
- [ ] README and docs are updated when commands or architecture change.
