# Roadmap

## Milestone 0: Project Foundation

Status: complete.

Scope:

- README and AGENT context.
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
- Summary-first result pagination.
- Telegram message length guards.
- Backup/restore guidance for `data/`.

Exit criteria:

- Operator can deploy, restart, inspect logs, and recover local data.
- Tests and build are documented and repeatable.
- Security/compliance boundaries are preserved.

## Milestone 6: Continuous Improvement Loop

Status: baseline complete.

Goal: make result-quality issues observable and reviewable without requiring the operator to manually explain every bad query to a maintainer.

Problem:

- Current matcher/parser quality improves only after a human notices an issue, reports it manually, and a maintainer creates a regression test.
- WatchFacts result text is messy enough that subtle truncation or wrong-segment bugs will keep appearing.

Deliverables:

- One-tap Telegram feedback for incomplete or wrong results.
- SQLite issue store for feedback and suspicious auto-flags.
- Owner-only issue review commands such as `/issues`, `/issue <id>`, and `/issues_export`.
- Deterministic suspicious-result detector for common truncation patterns.
- Regression export workflow for turning issue cases into tests.
- Documentation for owner review and maintainer regression workflow.

Exit criteria:

- [x] An authorized user can report a result as missing information or wrong from Telegram.
- [x] Owner can list and inspect open issue cases.
- [x] Bot can auto-flag suspicious extracted results without blocking result delivery.
- [x] Exported cases contain enough context to create matcher/parser tests.
- [x] No secrets, cookies, browser state, or full page HTML are exposed in issue records or exports.
- [ ] Add status mutation commands such as `/issue_done` and `/issue_ignore`.
- [ ] Add fixture generation script from exported issues.
- [ ] `python -m pytest` and `make check` pass for every implementation change.

Detailed spec:

- [Continuous Improvement Spec](continuous-improvement.md)

## Later Ideas

- Multi-page crawling.
- Scheduled refresh jobs.
- Dealer filtering.
- Price normalization.
- Image caching.
- Export results.
- Multiple watch sources.
- Query operators.
- CI workflow.

Each later idea needs a focused spec before implementation.
