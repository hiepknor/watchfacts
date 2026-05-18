# Product Spec: WatchFacts Telegram Bot

## Objective

Maintain a self-hosted Telegram bot that lets an authorized user search WatchFacts trading listings by sending natural watch search text such as `228253a choco`.

The bot uses an authenticated browser session, extracts listings from WatchFacts JSON/HTML responses, matches listings deterministically, deduplicates latest reposts, and returns concise Telegram-friendly summaries plus paginated result batches.

## Users

- Primary user: a watch trader or collector with a valid WatchFacts account.
- Operator: the person who deploys the bot, manages `.env`, creates the browser login session, and monitors logs.
- Maintainer: a developer or AI agent extending crawler, parser, matcher, database, or Telegram behavior.

## User Stories

- As a Telegram user, I can send a model/reference query and receive relevant WatchFacts listings.
- As a Telegram user, I can include multiple terms and get listings that contain all required tokens.
- As an operator, I can log in to WatchFacts manually once and let the bot reuse the saved session.
- As an operator, I can run the bot locally or through Docker Compose.
- As an operator, I can report incomplete or wrong Telegram results with one tap so they can become future regression cases.
- As an operator, I can ask the bot to list suspicious or reported result issues for review.
- As a maintainer, I can test matching, parsing, and dedupe behavior without calling external services.
- As a maintainer, I can convert reported issue cases into deterministic tests.
- As an operator, I can optionally enable OpenAI-assisted refinement for hard cases without making AI required for normal search.
- As a maintainer, I can inspect why a result matched, how it was extracted, and why it ranked above or below nearby results.
- As a maintainer, I can run a repeatable production-quality audit query set and turn confirmed issues into regression tests.

## Core Flow

1. User sends a text query to the Telegram bot.
2. Bot validates and normalizes the query.
3. Bot loads or reuses an authenticated WatchFacts browser state.
4. Bot crawls the configured WatchFacts URL.
5. Bot extracts listing candidates from JSON search responses or HTML fallback.
6. Bot matches or scopes listings against query tokens.
7. Bot removes repeated latest reposts.
8. Bot scores eligible listings by quality first, then newest posted date inside the same quality group.
9. If OpenAI controlled intelligence is enabled, bot may record or apply a guarded suggestion only after strict validation.
10. Bot stores query/cache/dedupe/issue data in SQLite.
11. Bot returns a summary with an inline "Xem kết quả" button.
12. User requests batches with "Xem kết quả" / "Xem thêm".

## Functional Requirements

- Accept plain-text Telegram messages as search queries.
- Support `/start`, `/help`, `/settings`, and `/cancel`.
- Support `/health` for checking whether the saved WatchFacts session is valid.
- Support owner issue commands `/issues`, `/issue <id>`, and `/issues_export`.
- Ignore normal group chat messages unless the bot is mentioned at the start or the user replies to a bot message.
- Support optional Telegram user-id allowlist.
- Normalize query text for case-insensitive matching.
- Require all query tokens to appear in a listing unless a later spec defines advanced operators.
- Handle messy watch listing text with emoji, keycap digits, compact dates, compound references, seller/member metadata, and multi-product stock-list cards.
- Extract listing fields when available:
  - image URL
  - listing text
  - seller
  - posted date
  - source URL or stable listing identifier if available
- Deduplicate listings by normalized listing text, seller, and posted date.
- Deduplicate same-seller reposts in search output by keeping the newest posted date.
- Rank final output by explicit quality signals first, then newest posted date descending inside the same quality group.
- Demote missing-price and suspicious results without hiding them.
- Persist local cache, query history, and dedupe records in SQLite.
- Reuse `data/watchfacts_state.json` for authenticated browser state.
- Support Docker Compose deployment with persistent `data/` and `logs/` volumes.
- Limit Telegram photo captions and text messages to platform-safe lengths.
- Notify the owner in Vietnamese when WatchFacts browser session state is missing or expired.
- Support one-tap feedback for incomplete/wrong results, owner issue review commands, suspicious-result auto-flagging, and regression fixture export. See [Continuous Improvement Spec](continuous-improvement.md).
- Support optional OpenAI-assisted refinement for suspicious, reported, or hard-to-scope results, controlled by explicit modes and validation gates.
- Support maintainer diagnostics for matcher trace and ranking reasons. See [Result Quality Scoring Spec](result-quality-scoring.md).
- Support a production quality audit loop for matcher, extraction, scoring, and quality-gate changes. See [Production Quality Audit Spec](production-quality-audit.md).

## Non-Functional Requirements

- No LLM is required for core behavior.
- Matching must be deterministic and testable.
- Ranking must be deterministic, quality-first, and covered by regression tests.
- Continuous improvement must be evidence collection and review, not autonomous code mutation.
- Production quality changes should be backed by audit evidence and regression fixtures.
- AI-assisted refinement, if enabled, must use OpenAI API as the only supported AI provider and must be controlled by explicit modes, confidence gates, owner review, and deterministic fallback.
- OpenAI integration must be disabled by default and must not be required for normal search.
- Telegram handlers should remain async and avoid blocking network/browser work on the event loop.
- Secrets and browser session files must never be committed.
- The bot must fail clearly when configuration or login state is missing.
- The bot should log operational events without leaking secrets, cookies, tokens, or full session state.
- Long WatchFacts listings should not cause Telegram batch sending failures.

## Non-Goals

- Bypassing login, captcha, Cloudflare, or anti-bot controls.
- Scraping WatchFacts without authorized access.
- Storing WatchFacts passwords in source code or config examples.
- Building a web UI in the initial version.
- Running local AI models in the production runtime.
- Adding OpenAI ranking, summarization, or extraction as an uncontrolled primary result source.
- Letting AI become the uncontrolled source of truth for listing extraction.
- Sorting only by newest date while ignoring result quality.
- Letting the bot automatically rewrite matcher/parser code or deploy fixes based on feedback.
- Supporting multiple watch sources before the WatchFacts path is stable.

## Commands

| Purpose | Command |
| --- | --- |
| Initialize local runtime files | `make init` |
| Build Docker image | `make build` |
| Start Docker service | `make up` |
| Stop Docker service | `make down` |
| Follow logs | `make logs` |
| Open container shell | `make shell` |
| Run lightweight checks | `make check` |
| Run bot locally | `python -m app.main` |
| Run login locally | `python scripts/login.py` |
| Deploy latest code | `make deploy` |
| Deploy local unpushed code | `make deploy SKIP_PULL=1` |

Telegram commands:

| Command | Purpose |
| --- | --- |
| `/start` | Show intro and examples |
| `/help` | Show usage flow and pagination actions |
| `/settings` | Show safe runtime settings |
| `/health` | Check WatchFacts browser-session health |
| `/issues` | List open feedback and suspicious result issues |
| `/issue F<id>` or `/issue S<id>` | Show one feedback or suspicious issue in detail |
| `/issue_done F<id>` or `/issue_done S<id>` | Mark an issue as fixed/reviewed |
| `/issue_ignore F<id>` or `/issue_ignore S<id>` | Ignore a false positive issue |
| `/issues_export` | Export open issues as JSON for regression tests |
| `/cancel` | Clear pending result buttons |

In group chats, normal text messages are ignored. A search must mention the bot
at the start of the message or reply to a bot message.

## Success Criteria

- A user can send a Telegram query and receive matching listings.
- A user gets a result summary first, then explicit paginated batches.
- Matching is case-insensitive, token-based, and covered by tests.
- Output order prioritizes quality before recency, and recency is newest-first inside each quality group.
- Duplicate listings are suppressed across a single search result set.
- Missing config or missing browser state produces actionable operator errors.
- Expired WatchFacts session notifies the owner without exposing cookies or browser state.
- Reported or suspicious result issues can be reviewed and converted into regression tests after the continuous-improvement milestone is implemented.
- OpenAI-assisted suggestions, when enabled, are schema-validated, safely logged, and never required for search availability.
- Docker image builds successfully.
- `make init`, `make build`, and `make check` work on a fresh clone.
- `.env`, `data/watchfacts_state.json`, `data/bot.db`, and `logs/` stay out of git.

## Open Questions

- Should the bot add query operators for optional terms, quoted phrases, or negative filters?
- Should query history be retained forever or pruned by age/count?
- Should price normalization support currency conversion or numeric sorting?
- Should multi-page crawling be enabled beyond the current WatchFacts search response?
- Should feedback buttons be shown to all authorized users or owner-only?
- Should suspicious result flags appear in normal summaries or only in owner review commands?
- Should matcher/score diagnostics be exposed only through local scripts or also through owner-only Telegram commands?
