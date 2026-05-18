# Technical Spec

## Tech Stack

- Python 3.11+
- `python-telegram-bot[job-queue]` for Telegram integration
- Playwright Chromium for authenticated browser automation
- WatchFacts authenticated JSON search response parsing with HTML fallback
- BeautifulSoup4 + lxml for HTML parsing
- SQLite for local cache, dedupe, and query history
- Docker Compose for deployment
- Optional OpenAI API integration for controlled AI refinement
- Makefile for repeatable local commands

## Intended Project Structure

```text
app/
  main.py          # application entrypoint
  telegram_bot.py  # Telegram handlers and message formatting
  scraper.py       # Playwright browser/session/crawl logic
  parser.py        # HTML/listing extraction
  matcher.py       # stable public matcher API
  matcher_rules.py # deterministic matcher implementation
  matcher_rulebook.py # rule taxonomy and extraction trace types
  result_scoring.py # planned final quality and recency ordering boundary
  dedupe.py        # listing identity and duplicate filtering
  db.py            # SQLite schema and persistence
  config.py        # environment/config loading
  issues.py        # suspicious-result heuristics for issue collection
  ai_refiner.py    # optional OpenAI-backed result refinement boundary
scripts/
  login.py         # manual WatchFacts login and browser state creation
data/
  bot.db
  watchfacts_state.json
logs/
docs/
```

The repository is implemented through the production-hardening milestone. Agents must still inspect the filesystem before editing because behavior changes quickly.

## Configuration

Expected environment:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | None | Telegram bot token |
| `TELEGRAM_ALLOWED_USER_IDS` | No | Empty | Comma-separated owner Telegram user ids; empty allows everyone |
| `TELEGRAM_RESULT_LIMIT` | No | `5` | Number of results to send per Telegram pagination batch |
| `TELEGRAM_MAX_CONCURRENT_SEARCHES` | No | `1` | Maximum WatchFacts searches running at the same time; extra queries wait with a queue notice |
| `WATCHFACTS_URL` | Yes | `https://watchfacts.com/simon-match-making` | WatchFacts page to crawl |
| `HEADLESS` | No | `true` | Browser headless mode |
| `ENABLE_CRAWL4AI` | No | `true` | Reserved compatibility flag; current runtime uses WatchFacts JSON/HTML parsing |
| `SEARCH_CACHE_TTL_SECONDS` | No | `300` | Fresh-result cache lifetime for identical normalized searches before calling WatchFacts again |
| `HYBRID_AI_MODE` | No | `off` | Controlled AI mode: `off`, `shadow`, `review`, or `guarded`; only `guarded` can alter search output |
| `OPENAI_API_KEY` | Required when AI mode is not `off` | None | OpenAI API key; never logged or shown in Telegram |
| `OPENAI_MODEL` | No | Cost-conscious current model | Model used for structured refinement suggestions |
| `OPENAI_TIMEOUT_SECONDS` | No | `12` | Maximum OpenAI request time before deterministic fallback |
| `OPENAI_MAX_REFINES` | No | `3` | Maximum snippets sent to OpenAI per query |

Configuration rules:

- Load from environment and `.env` during local development.
- Never log real tokens or secrets.
- Fail fast if required values are missing.
- Treat boolean env values case-insensitively.
- Restrict Telegram handlers to configured user ids when `TELEGRAM_ALLOWED_USER_IDS` is non-empty.
- Validate Telegram result limit as a positive integer.
- Use `SEARCH_CACHE_TTL_SECONDS` to reduce repeated WatchFacts backend calls for identical normalized searches.
- Remove local model runtime support from the production path.
- Keep `HYBRID_AI_MODE=off` by default; use `shadow` or `review` to collect safe suggestions before considering `guarded`.
- Require `OPENAI_API_KEY` only when OpenAI-assisted modes are enabled.
- Never log or display `OPENAI_API_KEY`.

## Runtime Architecture

```text
Telegram update
  -> telegram_bot handler
  -> scraper loads saved browser state and posts the WatchFacts search form when available
  -> parser extracts listing candidates from JSON response or HTML fallback
  -> matcher filters or scopes listings by query tokens
  -> dedupe removes repeated latest reposts
  -> result scoring orders eligible listings by quality and recency
  -> suspicious-result detector records likely extraction issues
  -> optional OpenAI controlled refiner records suggestions or applies guarded refinements
  -> db records query/cache/dedupe state
  -> telegram_bot sends a summary first, then paginated result batches
```

## Module Contracts

### `config.py`

Responsibilities:

- Read environment variables.
- Expose typed config values.
- Validate required settings.
- Define stable runtime paths for `data/`, logs, database, and browser state.

### `telegram_bot.py`

Responsibilities:

- Register Telegram command/message handlers.
- Validate empty or unsupported user input.
- Call the search workflow asynchronously.
- Format listing results for Telegram.
- Catch user-facing errors and return clear messages.
- Notify configured owners when WatchFacts browser session state is missing or expired.
- Provide `/health` to check WatchFacts session validity without exposing browser state.
- Ignore normal group chat messages unless the bot is mentioned or replied to.
- Protect Telegram sends by limiting photo captions to 1024 characters and text messages to 4096 characters.
- Attach feedback callbacks to results, handle feedback issue callbacks, and provide owner issue review commands.

### `scraper.py`

Responsibilities:

- Launch Chromium with Playwright.
- Load `data/watchfacts_state.json`.
- Navigate to `WATCHFACTS_URL`.
- Wait for stable page content.
- Submit the WatchFacts search form when present.
- Return search response text plus metadata indicating whether the server already filtered results.
- Check whether the saved browser session is valid without logging cookies or storage state.

Boundaries:

- Do not bypass login, captcha, Cloudflare, or anti-bot systems.
- Do not read or log cookies/tokens except through Playwright's normal storage-state mechanism.
- Do not store WatchFacts credentials.

### `parser.py`

Responsibilities:

- Parse WatchFacts JSON search responses and fallback HTML with BeautifulSoup/lxml.
- Extract listing candidate objects.
- Normalize missing fields to `None` or empty strings consistently.
- Keep extraction deterministic and unit-testable with HTML fixtures.

### `matcher.py`, `matcher_rules.py`, and `matcher_rulebook.py`

Responsibilities:

- Keep `app.matcher` as the stable public API for search, dedupe, AI gates, and tests.
- Keep deterministic matcher implementation in `matcher_rules.py`.
- Keep matcher rule taxonomy, priorities, and trace data types in `matcher_rulebook.py`.
- Normalize user query and listing text.
- Tokenize query text.
- Apply case-insensitive all-token matching.
- Use regex for robust model/reference matching, including compound references.
- Extract the relevant product segment from stock-list cards that contain multiple listings.
- Treat year/date/price-like numeric query tokens as descriptors instead of independent references.
- Normalize Unicode mark characters so keycap digit prices such as `$8️⃣0️⃣k` match normal price queries.
- Stop product segment extraction before seller/member metadata boundaries.
- Expose `explain_extraction()` for local debugging and future owner-visible diagnostics.

Matching rule:

```text
listing matches query if every normalized query token appears in the relevant listing text,
with stricter handling for model/reference tokens
```

Rule order:

```text
query -> reference -> descriptor -> price -> product boundary
      -> metadata boundary -> date/condition detail -> noise -> cleanup
```

New matcher changes should preserve this order unless a spec explains why a
rule must run earlier. Each recurring production issue should become a focused
regression test and, where useful, a traceable rule id in the rulebook.

When WatchFacts returns server-filtered JSON search results, the workflow keeps those results and only uses the matcher to scope display text. This avoids over-filtering server matches that are relevant but do not contain every local descriptor in the same form.

### `result_scoring.py`

Responsibilities:

- Rank already-eligible search results after matching, dedupe, and suspicious
  detection.
- Keep quality as the primary ordering signal.
- Sort newest posted date descending inside the same quality group.
- Demote missing-price and suspicious results without removing them.
- Return structured score reasons suitable for tests and diagnostics.
- Preserve original source order as a stable final tie-breaker.

Initial ordering contract:

```text
quality_group ASC
posted_date DESC
exact_reference_score DESC
descriptor_score DESC
price_evidence_score DESC
original_rank ASC
```

Quality groups:

```text
0 = clean result with no suspicious issues
1 = result with only missing_price_evidence
2 = result with other suspicious issues
```

The scoring layer must not admit results that deterministic matching rejected.
OpenAI guarded refinement may improve shown text after validation, but must not
become an uncontrolled ranking authority.

Detailed spec:

- [Result Quality Scoring And Matcher Diagnostics Spec](result-quality-scoring.md)

### `match_debug.py`

Responsibilities:

- Combine `explain_extraction()` trace data with structured result scoring.
- Format local debug output for one query/listing pair.
- Cap output length so the same formatter can later be reused in Telegram if an
  owner-only command is added.

The initial debug surface is local-only through `scripts/debug_match.py`.
Telegram exposure is deferred until the production bot has a configured
`TELEGRAM_ALLOWED_USER_IDS` owner allowlist.

### `dedupe.py`

Responsibilities:

- Build a stable dedupe key:

```text
normalized_text + seller + posted_date
```

- Remove exact duplicates within a persisted result set.
- Remove repost duplicates in the Telegram result set by grouping normalized listing text and seller, then keeping the newest posted date.
- Provide deterministic normalization helpers.

### `db.py`

Responsibilities:

- Manage SQLite connection lifecycle.
- Create schema if missing.
- Persist query history and dedupe/cache data.
- Persist result feedback, suspicious-result flags, issue review status, and fixture export metadata.
- Use parameterized SQL only.

### `ai_refiner.py`

Responsibilities:

- Provide the optional OpenAI-backed refinement boundary.
- Accept only minimal safe inputs: query, deterministic shown text, bounded raw listing snippet, and issue/suspicion reason codes.
- Use the raw listing snippet, when available, for allowlisted suspicious cases so OpenAI can recover traceable details that deterministic extraction may have omitted.
- Request structured JSON output with fields such as `relevant`, `selected_text`, `confidence`, `reasons`, and `risk_flags`.
- Apply local validation before any suggestion can affect user-facing output.
- In `shadow`, `review`, and `guarded`, record changed suggestion attempts with gate status and reasons.
- In `review`, surface suggestions only to owner commands; normal users still receive deterministic output.
- Dedupe suggestions by normalized query, raw listing hash, model, and prompt version.
- Return deterministic fallback on timeout, API error, malformed output, unsafe output, or low confidence.

Boundaries:

- Do not send `.env`, Telegram tokens, WatchFacts cookies, browser state, full storage state, or full page HTML to OpenAI.
- Do not let OpenAI call WatchFacts, Telegram, the database, or deployment commands.
- Do not treat model output as authoritative unless local gates pass.

## Data Model

SQLite tables:

### `queries`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `query_text` | text | Original query |
| `normalized_query` | text | Normalized query |
| `created_at` | text | ISO timestamp |
| `result_count` | integer | Number of matched listings |

### `listings`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `dedupe_key` | text unique | Stable duplicate key |
| `listing_text` | text | Raw listing text |
| `normalized_text` | text | Normalized text |
| `seller` | text nullable | Seller name |
| `posted_date` | text nullable | Display date |
| `image_url` | text nullable | Product image |
| `source_url` | text nullable | Listing/page URL |
| `first_seen_at` | text | ISO timestamp |
| `last_seen_at` | text | ISO timestamp |

### `query_results`

| Column | Type | Notes |
| --- | --- | --- |
| `query_id` | integer | References `queries.id` |
| `listing_id` | integer | References `listings.id` |
| `rank` | integer | Result order |

### `search_cache`

Fresh final-result cache used before calling WatchFacts for repeated identical
normalized searches.

| Column | Type | Notes |
| --- | --- | --- |
| `cache_key` | text primary key | Versioned normalized query/runtime key |
| `query_text` | text | Original query that populated the cache |
| `normalized_query` | text | Normalized query |
| `result_json` | text | Serialized final deduped/grouped `SearchResult` list |
| `result_count` | integer | Number of cached primary results |
| `created_at` | text | ISO timestamp |
| `expires_at` | text | ISO timestamp checked before reuse |
| `last_used_at` | text | ISO timestamp updated on cache hit |

### `result_feedback`

See [Continuous Improvement Spec](continuous-improvement.md) for the full schema. The table should persist one-tap user feedback against the exact Telegram result shown to the user.

Minimum fields:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `query_text` | text | Original query |
| `result_rank` | integer | Rank within the search result set |
| `reason` | text | `missing_info`, `wrong_result`, `correct`, or future reason |
| `listing_text` | text | Text shown to the user |
| `raw_listing_text` | text nullable | Original candidate text when available |
| `seller` | text nullable | Seller display value |
| `source_url` | text nullable | WatchFacts source URL |
| `issue_status` | text | `open`, `reviewed`, `fixed`, `ignored` |

### `suspicious_results`

This table should persist deterministic auto-flags for results that look likely to be incomplete, such as text ending with a standalone currency token.

Minimum fields:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `query_text` | text | Original query |
| `result_rank` | integer | Rank within the search result set |
| `reason` | text | Suspicion code |
| `severity` | integer | 1 low, 2 medium, 3 high |
| `listing_text` | text | Text shown to the user |
| `raw_listing_text` | text nullable | Original candidate text when available |
| `reviewed_at` | text nullable | Set when owner reviews |

### `ai_refinement_suggestions`

This table stores OpenAI suggestion attempts for owner review and regression
fixture export. It must not store prompts containing secrets or full browser
state.

Minimum fields:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `query_text` | text | Original query |
| `normalized_query` | text | Normalized query |
| `result_rank` | integer | Rank within the result set |
| `mode` | text | `shadow`, `review`, or `guarded` |
| `model` | text | OpenAI model used |
| `prompt_version` | text | Prompt/schema version for dedupe |
| `raw_listing_hash` | text | Hash of bounded raw snippet or deterministic text |
| `suggestion_key` | text | Dedupe key from query, raw hash, model, and prompt version |
| `issue_type` / `issue_id` | nullable | Link to related feedback or suspicious issue when available |
| `deterministic_text` | text | Text the bot would otherwise show |
| `suggested_text` | text | Text suggested by OpenAI after local parsing |
| `raw_listing_text` | text nullable | Bounded raw listing snippet |
| `gate_status` | text | `accepted` or `rejected` |
| `gate_reasons` | text | JSON reason array |
| `review_status` | text | `open`, `accepted`, or `ignored` |

## Continuous Improvement Architecture

Feedback and AI review workflow:

```text
Telegram result batch
  -> feedback callback or suspicious detector
  -> db stores issue evidence
  -> owner reviews with /issues and /issue <id>
  -> owner exports issue fixtures
  -> owner reviews AI suggestions with /ai_suggestions and /ai_suggestion <id>
  -> owner accepts AI suggestions with /ai_accept <id>
  -> owner exports accepted AI fixtures with /ai_suggestions_export
  -> maintainer converts fixtures to tests
  -> matcher/parser fix is committed and deployed
```

Design constraints:

- Feedback collection must never change matcher/parser behavior at runtime.
- Feedback callbacks must be authorized.
- Issue formatting must be safe for Telegram and must not reveal cookies, tokens, browser state, or full page HTML.
- Suspicious detection must be deterministic and covered by unit tests.
- Issue exports should be small, stable, and suitable for regression tests.

## Error Handling

Expected error categories:

- Config error: missing token, missing URL, invalid boolean.
- Login/session error: missing or expired browser state.
- WatchFacts session health error: invalid state detected by `/health` or during search; owner should be notified in Vietnamese.
- Crawl error: navigation timeout, unexpected page state.
- Parse error: no listing container found or extraction failure.
- Telegram error: message send failure, invalid chat state.
- Database error: SQLite unavailable or schema migration issue.

User-facing messages should be concise. Logs should include enough detail for operators without leaking secrets.

## Testing Strategy

Preferred tests:

- Unit tests for `matcher.py`, `dedupe.py`, and parser fixtures.
- Unit tests for result scoring, including quality-first ordering and date-desc
  ordering inside a quality group.
- Integration tests for SQLite schema and query/listing persistence.
- Handler-level tests for Telegram formatting and error branches.
- Handler-level tests for `/health`, owner alerts, and future feedback callbacks.
- Unit tests for suspicious-result detection rules.
- Database tests for future feedback and issue-review tables.
- Optional browser smoke test for login/session flow when credentials are available.
- OpenAI refiner unit tests with stubbed client responses; tests must not call the live OpenAI API.

Recommended commands:

```bash
.venv/bin/python -m pytest
make check
make build
```

## Boundaries

Always:

- Keep matching deterministic.
- Use parameterized SQL.
- Keep browser session state in `data/watchfacts_state.json`.
- Keep `.env`, `data/`, and `logs/` out of git.

Ask first:

- Adding external services.
- Adding AI behavior beyond the OpenAI controlled refiner.
- Changing the quality-first ranking contract.
- Changing the dedupe identity.
- Changing data retention behavior.
- Adding dependencies beyond the current stack.

Never:

- Commit real secrets or browser session state.
- Bypass WatchFacts access controls.
- Store WatchFacts passwords.
- Log cookies, tokens, or full storage state.
- Send secrets, browser state, full page HTML, or raw credentials to OpenAI.
