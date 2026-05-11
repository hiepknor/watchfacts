# Implementation Plan

## Overview

Build the bot in small, verifiable phases. Each phase should leave the repo in a working state and should be committed separately.

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
- [x] Empty messages are rejected.
- [x] Text messages call a search workflow interface.

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

- [x] Telegram query triggers scrape -> parse -> match -> dedupe -> persist -> format.
- [x] No-result state is user-friendly.
- [x] Errors are logged and surfaced without secrets.

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

- [ ] Structured logs cover startup, query start/end, result counts, and error categories.
- [ ] Logs do not contain tokens, cookies, or browser state.

Verify:

```bash
python -m pytest
```

### Task 5.2: Deployment Runbook

Acceptance:

- [ ] `docs/operations.md` includes first deploy, restart, logs, backup, and restore steps.
- [ ] README links to operations docs.

Verify:

```bash
git diff --check
```

## Checkpoints

After each phase:

- [ ] `make check` passes.
- [ ] Relevant tests pass.
- [ ] Docker build passes when runtime files changed.
- [ ] No secrets are staged.
- [ ] README/AGENT/docs are updated when commands or architecture change.
