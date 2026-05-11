# Roadmap

## Milestone 0: Project Foundation

Status: complete.

Scope:

- README and AGENT context.
- Local skills vendored in `./.skills`.
- Dockerfile, Docker Compose, Makefile, `.env.example`, ignore files.
- Documentation baseline and ADRs.

Exit criteria:

- `make check` passes.
- `make build` passes.
- Documentation explains current limitations.

## Milestone 1: Minimal Bot Skeleton

Status: complete.

Goal: the bot process starts and responds to basic Telegram messages.

Deliverables:

- `app/config.py`
- `app/main.py`
- `app/telegram_bot.py`
- Basic `/start` and text message handling.
- Tests for config and handler behavior.

Exit criteria:

- `python -m app.main` starts with valid config.
- Missing config fails clearly.
- Handler tests pass.

## Milestone 2: Deterministic Matching Core

Status: complete.

Goal: search logic is testable without WatchFacts or Telegram.

Deliverables:

- Query normalization.
- Token-based matching.
- Dedupe key generation.
- Parser fixtures and parser tests.

Exit criteria:

- Matching and dedupe tests pass.
- Fixture parser extracts listing candidates.
- No external network is needed for unit tests.

## Milestone 3: WatchFacts Browser Session

Status: complete.

Goal: authorized browser automation works with manual login state.

Deliverables:

- `scripts/login.py`.
- Playwright scraper using `data/watchfacts_state.json`.
- Clear handling for missing/expired session.

Exit criteria:

- Operator can create browser state manually.
- Scraper can fetch page HTML when session is valid.
- No credential storage is introduced.

## Milestone 4: End-To-End Search

Status: complete.

Goal: Telegram query returns WatchFacts search results.

Deliverables:

- Search workflow orchestration.
- SQLite persistence.
- Telegram result formatting.
- No-result and error messages.

Exit criteria:

- Query -> crawl -> parse -> match -> dedupe -> Telegram response works.
- SQLite records query history and listings.
- Docker Compose runtime works with valid `.env` and browser state.

## Milestone 5: Production Hardening

Status: complete.

Goal: make the bot maintainable on a small server.

Deliverables:

- Structured logging.
- Retry/timeouts for browser navigation.
- Result limit and message splitting.
- Backup/restore guidance for `data/`.
- CI workflow if desired.

Exit criteria:

- Operator can deploy, restart, inspect logs, and recover local data.
- Tests and build are documented and repeatable.
- Security/compliance boundaries are preserved.

## Later Ideas

- Multi-page crawling.
- Scheduled refresh jobs.
- Dealer filtering.
- Price normalization.
- Telegram inline buttons.
- Image caching.
- Export results.
- Multiple watch sources.

Each later idea needs a focused spec before implementation.
