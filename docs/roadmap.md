# Roadmap

Current operating model: WatchFacts search is a shared runtime.
`watchfacts-bot` is the primary user-facing Telegram runtime. MCP clients access
the same pipeline through the `watchfacts-mcp` Docker service for structured
integrations.

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

- `scripts/ops/login.py`.
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
- [x] Add status mutation commands such as `/issue_done` and `/issue_ignore`.
- [x] Add fixture generation script from exported issues.
- [x] `python -m pytest` and `make check` pass for every implementation change.

Detailed spec:

- [Continuous Improvement Spec](continuous-improvement.md)

## Milestone 7: OpenAI Controlled Intelligence

Status: complete.

Goal: make the bot improve faster on messy listing formats while preserving deterministic, auditable production behavior, using OpenAI API as the only AI provider.

Decision:

- Keep deterministic matching as the default source of truth.
- Remove the local model experiment from the supported runtime path.
- Add OpenAI only as a guarded second-opinion/refiner for suspicious, reported, or hard cases.
- Start in shadow mode: compare AI suggestions with deterministic output, record diffs, and do not change user-facing results.
- Promote AI-assisted corrections only when confidence gates pass and regression tests cover the pattern.

Deliverables:

- [x] Removal of local model settings, docs, smoke script usage, and the Compose local model service.
- [x] OpenAI configuration with `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, and `OPENAI_MAX_REFINES`.
- [x] OpenAI client/refiner using structured JSON output, short timeouts, and deterministic fallback.
- [x] Optional OpenAI refiner that proposes scoped listing text for high-risk results.
- [x] Confidence gates requiring query/reference match, raw substring traceability, confidence, length, and no cross-item leakage.
- [x] Shadow-mode records showing when OpenAI would change output.
- [x] Owner review commands or digest entries for OpenAI-suggested corrections.
- [x] Regression fixture generation from reviewed OpenAI suggestions.

Exit criteria:

- [x] Local model runtime support is removed from config, active docs, Docker Compose, Makefile, scripts, and tests.
- [x] OpenAI is disabled by default and never required for normal search.
- [x] Shadow mode records proposed changes without altering Telegram output.
- [x] OpenAI responses are parsed through a strict schema and rejected on malformed or unsafe output.
- [x] Accepted AI suggestions become deterministic tests before production behavior changes.
- [x] The bot never auto-edits code, auto-deploys, or stores secrets in AI prompts/logs.
- [x] Fallback behavior is deterministic when OpenAI is unavailable, slow, or uncertain.

Architecture decision:

- [ADR-005: Use OpenAI Controlled AI For Result Refinement](decisions/005-controlled-hybrid-ai-refinement.md)

## Milestone 8: Matcher Rulebook Refactor

Status: complete.

Goal: make matcher rules easier to inspect, improve, and test while preserving
the deterministic production behavior that users already rely on.

Deliverables:

- Stable `app.matcher` public API for search, dedupe, AI gates, and tests.
- Dedicated deterministic matcher implementation in `app.matcher_rules`.
- Query intent and token classifiers in `app.matcher_token_classification`.
- Rule taxonomy and trace types in `app.matcher_rulebook`.
- Ordered rule groups for query, reference, descriptor, price, product boundary,
  metadata boundary, date/condition detail, noise, and cleanup.
- `explain_extraction()` for maintainers to inspect selected reference,
  token/character spans, rule ids, and output text.

Exit criteria:

- [x] Public matcher import path remains stable.
- [x] Rulebook and trace tests pass.
- [x] Full local suite passed before production deployment.
- [x] Production validation ran against 10 diverse queries.

## Milestone 9: Result Quality Scoring And Matcher Diagnostics

Status: complete.

Goal: make output ranking and matcher debugging explicit, testable, and safe to
iterate without rewriting the deterministic matcher core.

Decision:

- Keep deterministic matcher as the eligibility gate.
- Move output quality ordering into a dedicated scoring layer.
- Keep quality-first ranking: clean results before missing-price results, and
  missing-price results before stronger suspicious cases.
- Sort newest posted date descending inside the same quality group.
- Add structured score reasons and matcher trace diagnostics for reported
  cases.
- Defer splitting `matcher_rules.py` into smaller files until scoring and trace
  tests are in place.

Deliverables:

- [x] `app/result_scoring.py` or equivalent dedicated scoring boundary.
- [x] Structured `ResultScore` object with quality group, date rank, relevance
  signals, original rank, and reason codes.
- [x] Regression tests for quality-first ranking, date-desc sorting, missing
  price demotion, and suspicious demotion.
- [x] Safe matcher/score debug surface for maintainers or owner-only Telegram
  review.
- [x] Optional matcher helper split after regression coverage proves behavior is
  stable.
- [x] Query-aware relevance signals for reference selection, descriptor
  locality, and visible price evidence.

Exit criteria:

- [x] Existing production result order is preserved unless a fixture documents a
  deliberate improvement.
- [x] Ranking behavior can be understood from one scoring module.
- [x] Reported ranking issues can become focused regression fixtures.
- [x] Debug output explains selected reference, applied rule ids, score reasons,
  and suspicious flags without exposing secrets or browser state.
- [x] `app.matcher` remains the stable public matcher API.

Detailed spec:

- [Result Quality Scoring And Matcher Diagnostics Spec](result-quality-scoring.md)

## Milestone 10: Production Quality Audit Loop

Status: in progress.

Goal: make production result quality measurable after each matcher, scoring, or
gate change, and make every confirmed issue become a regression fixture before
behavior is changed.

Decision:

- Keep deterministic matching, quality scoring, and guarded OpenAI refinement as
  the runtime architecture.
- Add a repeatable production audit loop instead of tuning rules from isolated
  observations.
- Keep Telegram summary-first behavior unchanged.
- Keep quality-first ranking unchanged unless a fixture explicitly documents a
  deliberate improvement.
- Bump the search cache version whenever scoring or quality gates change cached
  output.

Deliverables:

- [x] CLI audit script for curated production/local query sets.
- [x] Default 10-query audit set covering reference, descriptor, year, FPJ/RM,
  multi-list, and price shorthand risks.
- [x] Safe audit report showing top result text snippets, posted dates, quality
  group, relevance scores, price evidence, and score reason codes.
- [x] Issue classification taxonomy for wrong reference, wrong descriptor, bad
  extraction, bad rank, missing price, ambiguous price, and stale cache.
- [x] Regression fixture workflow from audit output and existing issue exports.
- [x] Ambiguous price policy documenting accepted dealer shorthand and rejected
  material/karat terms.
- [x] Production verification checklist for pre-deploy audit, deploy, health
  check, focused post-deploy audit, and PMO/docs capture.

Exit criteria:

- [x] Maintainer can run one command to audit the default query set.
- [x] Audit output is bounded and safe for logs or handoff notes.
- [x] Confirmed production issues have a documented fixture path before fixes merge.
- [x] Cache version updates are part of the checklist for scoring/gate changes.
- [x] Production audit docs explain when to demote, reject, or keep ambiguous
  dealer shorthand.
- [ ] Full tests and production health checks pass before marking the milestone
  complete.

Detailed spec:

- [Production Quality Audit Loop Spec](production-quality-audit.md)

## Milestone 11: MCP Bridge And Runtime Decoupling

Status: complete.

Goal: let MCP clients use WatchFacts search without depending on Telegram
handlers or Telegram formatting.

Decision:

- Keep WatchFacts search logic in `app.tool_runtime`.
- Expose a small MCP bridge in `app.mcp_server`.
- Run `watchfacts-mcp` as a Docker service with a host-local MCP endpoint.
- Keep tool names short and professional: `search`, `health`,
  `create_chat_draft`, `report_issue`, `list_issues`, `get_issue`,
  `update_issue`, and `suspicious_summary`.
- Use `offset` / `next_offset` pagination instead of Telegram callback state.
- Pass product images through `image_url`; never invent image links.

Deliverables:

- [x] Non-Telegram search payload runtime.
- [x] Docker Compose service for `watchfacts-mcp`.
- [x] MCP tools for search, health, OpenWA draft handoff, feedback, issue
  review, and suspicious QA summary.
- [x] Pagination contract with `offset`, `has_more`, and `next_offset`.
- [x] Makefile deploy target `make deploy` for `watchfacts-bot` +
  `watchfacts-mcp`.
- [x] Server deploy path that does not require `sudo` or `SKIP_PULL`.

Exit criteria:

- [x] MCP clients can list WatchFacts MCP tools.
- [x] MCP clients can call `search` with `query`, `limit`, `offset`, and
  `include_similar`.
- [x] `make deploy` pulls, builds, tests, recreates `watchfacts-bot` and
  `watchfacts-mcp`, and is used for standard production releases.
- [x] Search results include enough structured fields for clients to answer in
  Vietnamese and perform follow-up handoff/feedback.

## Future Work: Shared Runtime Hardening

Status: planned.

Goal: keep Telegram-specific runtime stable while moving reusable actions and
review surfaces into shared runtime modules.

Required before changing defaults:

- Keep `watchfacts-bot` / `app.main` as the primary production entrypoint.
- Move reusable owner review flows to shared runtime modules where they also
  benefit MCP tools or another operator UI.
- Keep `make deploy` deploying both `watchfacts-bot` and `watchfacts-mcp`.
- Keep `TELEGRAM_BOT_TOKEN` as required server setup for the primary bot.
- Keep regression tests for the shared runtime independent of Telegram.

## Later Ideas

- Multi-page crawling.
- Scheduled refresh jobs.
- Dealer filtering.
- Price normalization, currency handling, and numeric sorting.
- Image caching.
- Export results.
- Multiple watch sources.
- Query operators.
- CI workflow.

Each later idea needs a focused spec before implementation.
