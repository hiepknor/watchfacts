# Product Spec: WatchFacts Runtime For Hermes

## Objective

Maintain a self-hosted WatchFacts search runtime that lets an authorized user
search WatchFacts trading listings through Hermes, for example:

```text
@onioaibot search WatchFacts 5712G 2015 full set
```

Hermes calls the WatchFacts MCP tools. The runtime uses an authenticated browser
session, extracts listings from WatchFacts JSON/HTML responses, matches listings
deterministically, deduplicates latest reposts, and returns structured ranked
results with seller, date, source link, product image, and short-lived result
handles.

The legacy Telegram bot remains available, but new business automation should
use the non-Telegram runtime and MCP bridge instead of reimplementing search.

## Users

- Primary user: a watch trader or collector using Hermes/Telegram to search WatchFacts.
- Operator: the person who deploys WatchFacts MCP, configures Hermes, manages `.env`, creates the browser login session, and monitors logs.
- Maintainer: a developer or AI agent extending crawler, parser, matcher, database, MCP tools, OpenWA handoff, or legacy Telegram behavior.

## User Stories

- As a Hermes user, I can send a model/reference query and receive relevant WatchFacts listings.
- As a Hermes user, I can ask for more results and receive the next page without losing the original query context.
- As a Hermes user, I can see product images when WatchFacts provides `image_url`.
- As a Hermes user, I can ask to contact a seller and let Hermes create an OpenWA chat draft from a selected `result_id`.
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

1. User sends a WatchFacts request to Hermes.
2. Hermes calls `search(query, limit=5, offset=0, include_similar=true)` on the WatchFacts MCP server.
3. Runtime validates and normalizes the query.
4. Runtime loads or reuses authenticated WatchFacts browser state.
5. Runtime posts the WatchFacts search form through HTTPX.
6. Runtime extracts listing candidates from JSON search responses or HTML fallback.
7. Runtime matches or scopes listings against query tokens.
8. Runtime removes repeated latest reposts.
9. Runtime scores eligible listings by quality first, then newest posted date inside the same quality group.
10. If OpenAI controlled intelligence is enabled, runtime may record or apply a guarded suggestion only after strict validation.
11. Runtime stores query/cache/dedupe/issue data in SQLite.
12. MCP payload returns ranked results with `result_id`, `rank`, `image_url`, `has_more`, and `next_offset`.
13. Hermes answers in Vietnamese and preserves the short-lived `result_id` for contact/feedback follow-ups.
14. For "load more", Hermes calls the same query with `offset=next_offset`.

## Functional Requirements

- Expose MCP tool `search(query, limit=5, offset=0, include_similar=true)`.
- Search payload must include pagination fields `offset`, `limit`, `has_more`, `next_offset`, and stable absolute `rank`.
- Search payload must include a short-lived `result_id` for follow-up actions.
- Search payload should include `image_url` when WatchFacts provides a product image.
- Expose MCP tool `health` for WatchFacts session, database, OpenWA, and search readiness.
- Expose MCP tool `create_chat_draft(query, result_id=None, rank=None)` for seller handoff through OpenWA.
- Expose MCP issue tools `report_issue`, `list_issues`, `get_issue`, `update_issue`, and `suspicious_summary`; issue reporting should accept `result_id` or `rank`.
- Accept plain-text Telegram messages as search queries in the legacy bot.
- Support `/start`, `/help`, `/settings`, and `/cancel`.
- Support `/health` for checking whether the saved WatchFacts session is valid.
- Support owner issue commands for user feedback and auto-QA queues:
  `/issues`, `/suspicious`, `/suspicious_summary`, `/issue <id>`,
  `/issues_export`, and `/suspicious_export`.
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
- Support Docker deployment of `watchfacts-mcp` on the same server/network as Hermes.
- Support Makefile deployment with `make deploy-hermes-mcp` for MCP + Hermes restart.
- Limit Telegram photo captions and text messages to platform-safe lengths.
- Notify the owner in Vietnamese when WatchFacts browser session state is missing or expired.
- Support one-tap feedback for incomplete/wrong results, owner issue review commands, suspicious-result auto-flagging, and regression fixture export. See [Continuous Improvement Spec](continuous-improvement.md).
- Support optional OpenAI-assisted refinement for suspicious, reported, or hard-to-scope results, controlled by explicit modes and validation gates.
- Support maintainer diagnostics for matcher trace and ranking reasons. See [Result Quality Scoring Spec](result-quality-scoring.md).
- Support a production quality audit loop for matcher, extraction, scoring, and quality-gate changes. See [Production Quality Audit Spec](production-quality-audit.md).

## Non-Functional Requirements

- No LLM is required for core behavior.
- Hermes must not reimplement WatchFacts search logic; it should call MCP tools.
- MCP tool output must be structured enough for Hermes to answer without inventing seller contact, result ids, source links, prices, product images, or OpenWA links.
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
- Building a WatchFacts web UI in the current phase.
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
| Run repository checks | `make check` |
| Run bot locally | `python -m app.main` |
| Run login locally | `python scripts/ops/login.py` |
| Deploy legacy Telegram bot | `make deploy-bot` |
| Deploy WatchFacts MCP | `make deploy-mcp` |
| Deploy WatchFacts MCP and restart Hermes | `make deploy-hermes-mcp` |
| Restart Hermes | `make restart-hermes` |

Telegram commands:

| Command | Purpose |
| --- | --- |
| `/start` | Show intro and examples |
| `/help` | Show usage flow and pagination actions |
| `/settings` | Show safe runtime settings |
| `/health` | Check WatchFacts browser-session health |
| `/issues` | List open user feedback issues |
| `/suspicious` | List high-severity auto-suspicious QA flags |
| `/suspicious_summary` | Show auto-suspicious breakdown by reason, severity, and query count |
| `/issue F<id>` or `/issue S<id>` | Show one feedback or suspicious issue in detail |
| `/issue_done F<id>` or `/issue_done S<id>` | Mark an issue as fixed/reviewed |
| `/issue_ignore F<id>` or `/issue_ignore S<id>` | Ignore a false positive issue |
| `/issues_export` | Export open user feedback issues as JSON for regression tests |
| `/suspicious_export` | Export auto-suspicious QA flags as JSON for regression tests |
| `/cancel` | Clear pending result buttons |

In group chats, normal text messages are ignored. A search must mention the bot
at the start of the message or reply to a bot message.

## Success Criteria

- A Hermes user can request WatchFacts search and receive matching listings.
- A Hermes user can ask for more results and the MCP runtime returns the next page through `offset`.
- Product image URLs are passed through when available and never invented.
- A selected result can be handed off to OpenWA through `create_chat_draft`.
- A legacy Telegram user can still send a query and receive matching listings.
- Matching is case-insensitive, token-based, and covered by tests.
- Output order prioritizes quality before recency, and recency is newest-first inside each quality group.
- Duplicate listings are suppressed across a single search result set.
- Missing config or missing browser state produces actionable operator errors.
- Expired WatchFacts session notifies the owner without exposing cookies or browser state.
- Reported or suspicious result issues can be reviewed and converted into regression tests after the continuous-improvement milestone is implemented.
- OpenAI-assisted suggestions, when enabled, are schema-validated, safely logged, and never required for search availability.
- Docker image builds successfully.
- `make init`, `make build`, `make check`, and `make deploy-hermes-mcp` work on the production server.
- `.env`, `data/watchfacts_state.json`, `data/bot.db`, and `logs/` stay out of git.

## Open Questions

- Should the bot add query operators for optional terms, quoted phrases, or negative filters?
- Should query history be retained forever or pruned by age/count?
- Should price normalization support currency conversion or numeric sorting?
- Should multi-page crawling be enabled beyond the current WatchFacts search response?
- Should feedback buttons be shown to all authorized users or owner-only?
- Should suspicious result flags appear in normal summaries or only in owner review commands?
- Should matcher/score diagnostics be exposed only through local scripts or also through owner-only Telegram commands?
