# Implementation Plan

## Overview

The initial implementation phases and the continuous-improvement loop are complete. Keep this document as the project baseline and use it to verify that future changes preserve the intended architecture.

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

- [x] `python scripts/ops/login.py` opens Chromium for manual login.
- [x] Authenticated state is saved to `data/watchfacts_state.json`.
- [x] Script does not ask for or store passwords.

Verify:

```bash
python -m compileall scripts
```

Likely files:

- `scripts/ops/login.py`

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

## Phase 6: Continuous Improvement Loop

Status: complete.

Goal: reduce manual back-and-forth when search results are incomplete or wrong by collecting issue evidence directly from Telegram and turning it into regression tests.

Reference spec:

- [Continuous Improvement Spec](continuous-improvement.md)

### Task 6.1: Feedback Data Model

Description: Add SQLite storage for result feedback and suspicious-result flags.

Acceptance:

- [x] `app/db.py` creates `result_feedback` and `suspicious_results` tables without breaking existing databases.
- [x] Feedback records include query, shown listing text, optional raw listing text, seller, posted date, source URL, reason, report count, and issue status.
- [x] Suspicious records include query, rank, reason code, severity, shown listing text, optional raw listing text, and source URL.
- [x] Database writes use parameterized SQL.
- [x] No token, cookie, browser state, password, or full HTML page is stored.

Verify:

```bash
.venv/bin/python -m pytest tests/test_db.py
make check
```

Likely files:

- `app/db.py`
- `tests/test_db.py`

### Task 6.2: Telegram Feedback Buttons

Description: Add one-tap feedback controls to sent search results.

Acceptance:

- [x] Result messages include feedback callbacks for `Thiếu thông tin` and `Sai kết quả`.
- [x] Callback data stays within Telegram limits.
- [x] Feedback is tied to the exact result shown to the user.
- [x] Unauthorized users cannot submit feedback.
- [x] Duplicate feedback on the same issue updates report count instead of creating noisy rows.
- [x] User acknowledgement is visual Vietnamese and does not expose internal data.
- [x] Feedback storage failures are logged safely and do not break result delivery.

Verify:

```bash
.venv/bin/python -m pytest tests/test_telegram_bot.py
```

Likely files:

- `app/telegram_bot.py`
- `app/db.py`
- `tests/test_telegram_bot.py`

### Task 6.3: Owner Issue Review Commands

Description: Add owner-only commands for reviewing and exporting issue cases.

Acceptance:

- [x] `/issues` lists open user feedback; `/suspicious` lists auto-QA flags with concise Vietnamese formatting.
- [x] `/issue F<id>` and `/issue S<id>` show one issue with query, shown text, raw text when available, seller, date, source URL, and report count.
- [x] `/issues_export` and `/suspicious_export` return deterministic JSON suitable for regression fixtures.
- [x] Future status commands can mark issues `fixed` or `ignored`.
- [x] Owner commands require `TELEGRAM_ALLOWED_USER_IDS`.
- [x] Outputs never include cookies, Telegram tokens, browser state, or full page HTML.

Verify:

```bash
.venv/bin/python -m pytest tests/test_telegram_bot.py tests/test_db.py
```

Likely files:

- `app/telegram_bot.py`
- `app/db.py`
- `tests/test_telegram_bot.py`
- `tests/test_db.py`

### Task 6.4: Suspicious Result Detection

Description: Add deterministic heuristics that auto-flag likely incomplete extractions.

Acceptance:

- [x] Detect extracted text ending with standalone currency tokens such as `HKD`, `USD`, `USDT`, `EUR`, `AED`, or `CHF`.
- [x] Detect extracted text ending with price markers such as `Price`, `$`, `💰`, or `💲`.
- [x] Detect cases where raw listing text contains currency plus a long number but shown text omits that number.
- [x] Detect cases where raw text is much longer than shown text near the matched query/reference.
- [x] Store flags in `suspicious_results` with reason code and severity.
- [x] Do not block result delivery if suspicious detection fails.
- [x] Owner can review auto-flagged cases through `/suspicious` and `/issue S<id>`.

Verify:

```bash
.venv/bin/python -m pytest tests/test_search.py tests/test_matcher.py tests/test_db.py
```

Likely files:

- `app/search.py`
- `app/matcher.py`
- `app/db.py`
- new `app/issues.py` or `app/suspicious.py`
- relevant tests

### Task 6.5: Regression Export Workflow

Description: Make reported cases easy to convert into tests.

Acceptance:

- [x] Exported issue fixture includes query, raw text, shown text, seller, and source URL.
- [x] Export is stable across runs and suitable for copying into test fixtures.
- [x] Documentation explains how to convert exported issues into `tests/test_matcher.py`, parser fixtures, or benchmark cases.
- [x] Script can generate draft matcher regression tests from `/issues_export` JSON.
- [x] Maintainers can mark exported issues as fixed or ignored.

Verify:

```bash
git diff --check
.venv/bin/python -m pytest
```

Likely files:

- `docs/continuous-improvement.md`
- `docs/operations.md`
- `app/db.py`
- `app/telegram_bot.py`
- optional script under `scripts/`

## Checkpoints

After each phase:

- [x] Baseline phases 0-5 complete.
- [x] Phase 6 complete and deployed.
- [x] `make check` passes for every change.
- [x] Relevant tests pass for every code change.
- [x] Docker build passes when runtime files changed.
- [x] No secrets are staged.
- [x] README and docs are updated when commands or architecture change.

## Phase 7: OpenAI Controlled Intelligence

Status: complete.

Goal: remove the local model experiment and introduce one controlled AI path through OpenAI API, with deterministic search remaining the default and fallback behavior.

Reference docs:

- [Product Spec](product-spec.md)
- [Technical Spec](technical-spec.md)
- [Continuous Improvement Spec](continuous-improvement.md)
- [ADR-005: Use OpenAI Controlled AI For Result Refinement](decisions/005-controlled-hybrid-ai-refinement.md)

### Task 7.1: Remove Local Model Runtime Surface

Description: Delete the unsupported local model runtime path so production and docs have one AI integration target.

Acceptance:

- [x] Remove `LOCAL_LLM_*` settings from `app/config.py`, `.env.example`, tests, and settings output.
- [x] Remove retired local-model service settings from `.env.example`.
- [x] Remove the local model service from `docker-compose.yml`.
- [x] Remove obsolete local-model Makefile targets.
- [x] Remove or archive `scripts/smoke_local_llm.py` and local model benchmark docs.
- [x] Ensure local model files are no longer required by any documented flow.
- [x] Existing deterministic search, feedback, issue review, and suspicious detection tests still pass.

Verify:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_search.py tests/test_telegram_bot.py
make check
docker compose config
```

Also verify that active runtime files and operator docs no longer contain retired local-model config names, services, scripts, or model-file references.

Likely files:

- `app/config.py`
- `app/ai_refiner.py`
- `app/search.py`
- `app/telegram_bot.py`
- `.env.example`
- `docker-compose.yml`
- `Makefile`
- `docs/`
- `tests/`

### Task 7.2: Add OpenAI Configuration

Description: Add OpenAI-specific configuration while keeping AI disabled by default.

Acceptance:

- [x] Add `OPENAI_API_KEY` as optional unless `HYBRID_AI_MODE` is not `off`.
- [x] Add `OPENAI_MODEL` with a cost-conscious default appropriate for structured refinement.
- [x] Add `OPENAI_TIMEOUT_SECONDS` with a short default suitable for Telegram UX.
- [x] Add `OPENAI_MAX_REFINES` to cap per-query calls.
- [x] Fail fast with a clear config error when OpenAI mode is enabled without an API key.
- [x] `/settings` shows AI mode and model name but never shows `OPENAI_API_KEY`.

Verify:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_telegram_bot.py
```

Likely files:

- `app/config.py`
- `app/telegram_bot.py`
- `.env.example`
- `tests/test_config.py`
- `tests/test_telegram_bot.py`

### Task 7.3: Implement OpenAI Refiner Provider

Description: Replace the local OpenAI-compatible HTTP caller with a first-class OpenAI refiner that returns strict structured output.

Acceptance:

- [x] Create an OpenAI client boundary that can be stubbed in tests.
- [x] Send only minimal safe snippets: query, shown deterministic text, bounded raw listing snippet, and reason codes.
- [x] Use structured JSON output with fields such as `relevant`, `selected_text`, `confidence`, `reasons`, and `risk_flags`.
- [x] Reject malformed responses, empty suggestions, low-confidence suggestions, and suggestions that are not substrings of the raw listing text.
- [x] Never send `.env`, Telegram tokens, WatchFacts cookies, browser state, full storage state, or full page HTML.
- [x] Timeout or API failure returns deterministic output and logs only safe error categories.

Verify:

```bash
.venv/bin/python -m pytest tests/test_ai_refiner.py tests/test_search.py
make check
```

Likely files:

- new `app/openai_refiner.py` or replacement `app/ai_refiner.py`
- `app/search.py`
- `app/db.py`
- `tests/test_ai_refiner.py`
- `tests/test_search.py`

### Task 7.4: Wire Shadow And Review Modes

Description: Record OpenAI suggestions in shadow/review modes without changing Telegram output.

Acceptance:

- [x] `HYBRID_AI_MODE=shadow` records deterministic vs suggested output, gate results, model, and latency.
- [x] `HYBRID_AI_MODE=review` surfaces suggestions in owner issue review without showing them to normal users.
- [x] Suggestions are tied to feedback or suspicious-result records where possible.
- [x] Duplicate suggestions are deduped by query, raw snippet hash, model, and prompt version.
- [x] Owner-facing output is concise Vietnamese and never includes secrets.

Implemented owner commands:

- `/ai_suggestions`
- `/ai_suggestion <id>`
- `/ai_accept <id>`
- `/ai_ignore <id>`
- `/ai_suggestions_export`

Verify:

```bash
.venv/bin/python -m pytest tests/test_db.py tests/test_telegram_bot.py tests/test_search.py
```

Likely files:

- `app/db.py`
- `app/search.py`
- `app/telegram_bot.py`
- `tests/test_db.py`
- `tests/test_search.py`
- `tests/test_telegram_bot.py`

### Task 7.5: Add Guarded Apply Path

Description: Allow OpenAI suggestions to alter user-facing result text only when all validation gates pass.

Acceptance:

- [x] `HYBRID_AI_MODE=guarded` applies a suggestion only if local gates pass.
- [x] Expand guarded gates to include explicit confidence and risk-flag checks.
- [x] Expand guarded gates to include explicit separator-boundary and length checks.
- [x] Rejected suggestions are stored for review or discarded with reason codes.
- [x] Guarded mode uses raw listing text for allowlisted suspicious cases such as missing price evidence or truncated currency/price markers.
- [x] Guarded output keeps deterministic fallback if OpenAI is unavailable, slow, or uncertain.
- [x] Every newly accepted pattern has a deterministic regression fixture when practical.
- [x] Guarded use is covered by tests for accept, reject, low confidence, risk flags, malformed output, substring failure, separator crossing, and length failure.
- [x] Add explicit timeout test coverage for the OpenAI request boundary.

Verify:

```bash
.venv/bin/python -m pytest tests/test_ai_refiner.py tests/test_search.py tests/test_matcher.py
make check
```

Likely files:

- `app/search.py`
- `app/ai_refiner.py`
- `app/matcher.py`
- `tests/test_ai_refiner.py`
- `tests/test_search.py`
- `tests/test_matcher.py`

### Task 7.6: Update Operations And Security Docs

Description: Make operator documentation match the OpenAI-only AI path.

Acceptance:

- [x] README describes deterministic default behavior and optional OpenAI controlled intelligence.
- [x] Operations guide includes OpenAI setup, rotation, disabling, timeout, and fallback behavior.
- [x] Security docs state what may and may not be sent to OpenAI.
- [x] Technical spec lists OpenAI configuration and removes local model runtime docs.
- [x] Roadmap and ADRs reflect that local model runtime is retired.

Verify:

```bash
git diff --check
```

Also verify that README, operations docs, technical spec, examples, Makefile, and Compose files no longer contain retired local-model config names, services, scripts, or model-file references.

## Phase 8: Matcher Rulebook Refactor

Goal: make matcher rules easier to inspect, improve, and test while preserving
the deterministic production behavior that users already rely on.

### Task 8.1: Preserve Stable Public API

Acceptance:

- [x] Keep `app.matcher` as the import path used by search, dedupe, AI gates, and tests.
- [x] Move matcher implementation behind a dedicated rules module without changing caller behavior.
- [x] Keep existing matcher regression tests passing before changing behavior.

Verify:

```bash
.venv/bin/python -m pytest tests/test_matcher.py
```

### Task 8.2: Add Rulebook And Trace

Acceptance:

- [x] Add ordered matcher rule groups: query, reference, descriptor, price, product boundary, metadata boundary, date/condition, noise, cleanup.
- [x] Add `explain_extraction()` so maintainers can see query intent, selected reference, selected token/character span, rule ids, and output text.
- [x] Add tests that assert the rulebook remains priority ordered and trace output is populated for a hard price-prefix case.

Verify:

```bash
.venv/bin/python -m pytest tests/test_matcher.py
```

### Task 8.3: Convert Helpers Into Rule Groups

Acceptance:

- [x] Move price, boundary, descriptor, reference, date/condition, and cleanup helpers into focused modules or explicitly grouped sections.
- [x] Keep public behavior unchanged while moving code.
- [x] Add table-driven fixtures for recurring production patterns.

### Task 8.4: Production Validation

Acceptance:

- [x] Run full local test suite.
- [x] Deploy production only after tests pass.
- [x] Smoke test at least 10 diverse production queries and summarize result counts plus top-result quality.

Latest validation:

- Local full suite: `288 passed`.
- Production deployed through commit `c3eb1a4`.
- Production mode: `HYBRID_AI_MODE=guarded`.
- 10-query smoke set: `5205r`, `126500ln white 2026`, `7118/1200a grey`, `77451or white`, `116500 panda`, `5712/1r`, `5990/1r`, `26240ba new 2024`, `7200r`, `RM65-01 Lebron`.
- Remaining suspicious flags: 2 true positives in top 20 where raw text has no clear price amount.

## Phase 9: Result Quality Scoring And Matcher Diagnostics

Status: complete.

Goal: make ranking and matcher debugging explicit without changing the
deterministic matcher public API.

Reference docs:

- [Result Quality Scoring And Matcher Diagnostics Spec](result-quality-scoring.md)
- [Technical Spec](technical-spec.md)
- [ADR-001: Use Deterministic Matching For Core Search](decisions/001-deterministic-matching.md)

### Task 9.1: Extract Result Scoring Boundary

Description: Move quality/date ordering out of `app/search.py` into a dedicated scoring module.

Acceptance:

- [x] Add `app/result_scoring.py` or equivalent dedicated module.
- [x] Existing quality-first behavior is preserved.
- [x] Clean results sort newest posted date first.
- [x] Results with only `missing_price_evidence` rank below clean results.
- [x] Results with other suspicious issues rank below missing-price-only results.
- [x] Original source order is retained as the final stable tie-breaker.
- [x] Search workflow uses the scoring module instead of a private search helper.

Verify:

```bash
.venv/bin/python -m pytest tests/test_search.py
.venv/bin/python -m pytest tests/test_matcher.py
```

Likely files:

- `app/search.py`
- `app/result_scoring.py`
- `tests/test_search.py`
- optional `tests/test_result_scoring.py`

### Task 9.2: Add Structured Score Reasons

Description: Return score fields and reason codes so ranking decisions can be tested and debugged.

Acceptance:

- [x] Add a structured score object with quality group, posted date rank,
  reference/descriptor/price signals, original rank, and reason codes.
- [x] Tests assert reason codes for clean, missing-price, and suspicious cases.
- [x] Logs may include safe score summaries without listing secrets or browser state.
- [x] Telegram user-facing output remains unchanged.

Verify:

```bash
.venv/bin/python -m pytest tests/test_result_scoring.py tests/test_search.py
git diff --check
```

Likely files:

- `app/result_scoring.py`
- `tests/test_result_scoring.py`
- `app/search.py`

### Task 9.3: Add Matcher And Score Debug Surface

Description: Provide a safe way to inspect matcher trace and score reasons for a query/listing or stored issue.

Acceptance:

- [x] Debug output includes normalized query intent, selected reference, selected
  token/character span, applied rule ids, score reasons, and suspicious reason
  codes.
- [x] Debug output never includes `.env`, tokens, cookies, browser state, full
  storage state, or full page HTML.
- [x] Initial debug surface is local-only; Telegram exposure is deferred until
  `TELEGRAM_ALLOWED_USER_IDS` is configured.
- [x] Output is capped for Telegram length limits.

Verify:

```bash
.venv/bin/python -m pytest tests/test_telegram_bot.py tests/test_matcher.py tests/test_result_scoring.py
```

Likely files:

- `app/telegram_bot.py` or a local script under `scripts/`
- `app/matcher.py`
- `app/result_scoring.py`
- relevant tests

### Task 9.4: Split Matcher Helpers After Coverage

Description: Optionally split `matcher_rules.py` into smaller files only after scoring and diagnostics tests are stable.

Acceptance:

- [x] `app.matcher` public API remains unchanged.
- [x] Rule order remains documented as query -> reference -> descriptor -> price
  -> product boundary -> metadata boundary -> date/condition detail -> noise ->
  cleanup.
- [x] All matcher, search, AI gate, Telegram, and scoring tests pass.
- [x] No production behavior changes unless a fixture documents the deliberate
  improvement.

Implementation note:

- Normalization and tokenization helpers were split into
  `app/matcher_normalization.py`.
- Query intent and token classifiers were split into
  `app/matcher_token_classification.py`.
- Boundary, descriptor, and extraction helpers remain in
  `app/matcher_rules.py` to avoid a high-risk behavior-preserving move without a
  concrete production issue.

Verify:

```bash
.venv/bin/python -m pytest
git diff --check
```

Candidate files:

- `app/matcher_normalization.py`
- `app/matcher_token_classification.py`
- `app/matcher_reference.py`
- `app/matcher_descriptor.py`
- `app/matcher_boundaries.py`
- `app/matcher_extraction.py`

## Phase 10: Query-Aware Relevance Scoring

Status: complete.

Goal: fill the relevance score fields introduced in Phase 9 without changing
the quality-first and date-first ranking contract.

Reference docs:

- [Result Quality Scoring And Matcher Diagnostics Spec](result-quality-scoring.md)
- [Technical Spec](technical-spec.md)

### Task 10.1: Pass Query Into Scoring

Acceptance:

- [x] Search workflow passes the original query into `rank_results_by_quality`.
- [x] Search cache version is bumped because same-quality/same-date tie-breaks
  can change.
- [x] Existing quality-first and date-first tests still pass.

Verify:

```bash
.venv/bin/python -m pytest tests/test_search.py tests/test_result_scoring.py
```

### Task 10.2: Populate Relevance Signals

Acceptance:

- [x] `exact_reference_score` is populated when matcher trace selects a
  reference.
- [x] `descriptor_score` is populated when query descriptors are local to the
  selected reference.
- [x] `price_evidence_score` is populated when the shown listing has visible
  price evidence.
- [x] Score reason codes include reference, descriptor, and price evidence
  explanations.
- [x] New relevance signals only affect order after quality group and posted
  date are equal.

Verify:

```bash
.venv/bin/python -m pytest tests/test_result_scoring.py tests/test_match_debug.py
```

## Phase 11: Production Quality Audit Loop

Status: in progress.

Goal: make real production query quality observable, repeatable, and directly
convertible into regression fixtures.

Reference docs:

- [Production Quality Audit Loop Spec](production-quality-audit.md)
- [Result Quality Scoring And Matcher Diagnostics Spec](result-quality-scoring.md)
- [Operations Guide](operations.md)

### Task 11.1: Add Audit Script

Status: complete.

Description: Add a safe CLI script that runs a curated query set through the
normal search workflow and reports quality/scoring diagnostics for the top
results.

Acceptance:

- [x] Add `scripts/diagnostics/audit_quality.py`.
- [x] Support explicit query arguments.
- [x] Provide a default query set matching the production audit spec.
- [x] Support `--limit` for top-N results.
- [x] Include count, rank, listing snippet, posted date, quality group,
  severity, relevance scores, price evidence score, and score reason codes.
- [x] Use existing search, scoring, and OpenAI guarded-refinement modules.
- [x] Do not print `.env`, API keys, Telegram tokens, WatchFacts cookies,
  browser state, full page HTML, or full raw listings.
- [x] Add focused tests for report formatting or extraction of score summaries
  if the script exposes helper functions.

Verify:

```bash
.venv/bin/python -m pytest tests/test_audit_quality.py tests/test_result_scoring.py tests/test_search.py
git diff --check
```

Likely files:

- `scripts/diagnostics/audit_quality.py`
- optional `tests/test_audit_quality.py`

### Task 11.2: Add Fixture Workflow For Audit Findings

Status: complete.

Description: Define and document how audit findings become regression tests
before matcher, extraction, scoring, or gate changes are implemented.

Acceptance:

- [x] Document fixture templates for ranking issues, extraction issues,
  missing-price issues, descriptor conflicts, and stale-cache discoveries.
- [x] Reuse existing `/issues_export` and `scripts/fixtures/generate_issue_fixtures.py`
  where applicable.
- [x] Require each confirmed production issue to include query, shown text,
  raw text when available, posted date, and expected behavior.
- [x] Add tests before broad rule changes.

Verify:

```bash
git diff --check
.venv/bin/python -m pytest tests/test_generate_audit_fixtures.py tests/test_generate_issue_fixtures.py tests/test_matcher.py tests/test_search.py tests/test_result_scoring.py
```

Likely files:

- `docs/production-quality-audit.md`
- `docs/operations.md`
- `scripts/fixtures/generate_audit_fixtures.py`
- `tests/test_generate_audit_fixtures.py`
- optional generated regression fixtures under `tests/`

### Task 11.3: Normalize Ambiguous Price Policy

Description: Keep accepted dealer shorthand explicit while preventing material
terms such as karat gold from counting as price evidence.

Acceptance:

- [ ] Document accepted shorthand examples such as `465k`, `HKD785K`,
  `$36k`, `30+lbl`, `26299 + lab`, `USDT 485`, `110k€`, and `248 €`.
- [ ] Document non-price material examples such as `18k rose gold`, `22k gold`,
  and `24k yellow gold`.
- [ ] Add or maintain tests proving material karat terms do not count as price
  evidence.
- [ ] Avoid demoting dealer shorthand without a production issue fixture.

Verify:

```bash
.venv/bin/python -m pytest tests/test_issues.py tests/test_result_scoring.py
```

Likely files:

- `app/issues.py`
- `app/result_scoring.py`
- `tests/test_issues.py`
- `tests/test_result_scoring.py`
- `docs/production-quality-audit.md`

### Task 11.4: Production Verification Checklist

Description: Make production audit part of the deploy flow for matcher,
scoring, and quality-gate changes.

Acceptance:

- [ ] Run focused audit before deploy when behavior changes.
- [ ] Run full local tests before commit.
- [ ] Bump `SEARCH_CACHE_VERSION` when ranking/gate output can change.
- [ ] Deploy with `make deploy`.
- [ ] Verify production container health and production git HEAD.
- [ ] Rerun focused production audit after deploy.
- [ ] Capture unresolved findings to PMO or docs.

Verify:

```bash
.venv/bin/python -m pytest -q
git diff --check
make deploy
```

Likely files:

- `docs/operations.md`
- `docs/production-quality-audit.md`
