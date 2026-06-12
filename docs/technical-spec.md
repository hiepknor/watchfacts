# Technical Spec

## Tech Stack

- Python 3.11+
- MCP server for structured tool access
- `python-telegram-bot[job-queue]` for Telegram integration
- HTTPX for lightweight authenticated WatchFacts search requests
- Playwright Chromium for authenticated login and session checks
- WatchFacts authenticated JSON search response parsing with HTML fallback
- BeautifulSoup4 + lxml for HTML parsing
- SQLite for local cache, dedupe, and query history
- Docker Compose for deployment
- Shared Docker image `watchfacts:local`
- `watchfacts-bot` as the primary user-facing Telegram runtime
- MCP clients as a supporting structured integration surface
- OpenWA chat draft API for seller handoff
- Optional OpenAI API integration for controlled AI refinement
- Makefile for repeatable local commands

## Intended Project Structure

```text
app/
  main.py          # application entrypoint
  config.py        # environment/config loading
  db.py            # SQLite schema and persistence
  runtime/
    telegram_bot.py # primary Telegram handlers and message formatting
    mcp_server.py   # WatchFacts MCP tools and result-page routes
    tool_runtime.py # non-Telegram payload runtime used by MCP and diagnostics
  searching/
    search.py       # deterministic search workflow
    search_result.py # shared search result dataclass
    parser.py       # HTML/listing extraction
    matcher.py      # stable public matcher implementation
    matcher_normalization.py # normalization and tokenization helpers
    matcher_token_classification.py # query intent and token classifiers
    matcher_rules.py # deterministic matcher implementation
    matcher_rulebook.py # rule taxonomy and extraction trace types
    result_scoring.py # final quality and recency ordering boundary
    dedupe.py       # listing identity and duplicate filtering
    issues.py       # suspicious-result heuristics for issue collection
  integrations/
    scraper.py      # HTTPX search plus Playwright browser/session helpers
    watchfacts_http.py # authenticated lightweight WatchFacts HTTP client
    watchfacts_forms.py # WatchFacts form token/action discovery
    openwa_handoff.py # OpenWA chat draft API boundary
    ai_refiner.py   # optional OpenAI-backed result refinement boundary
  results/
    result_pages.py # generated result-page artifacts and action sidecars
  templates/
    result_page.html
  static/
    result_page.css
    result_page.js
scripts/
  *.py             # compatibility wrappers for older script paths
  ops/
    login.py       # manual WatchFacts login and browser state creation
  diagnostics/
    audit_quality.py # safe production/local quality audit script
    benchmark_hard_cases.py # hard-case matcher benchmark
    debug_match.py # local matcher/score debug tool
  fixtures/
    generate_audit_fixtures.py # audit JSON to scoring regression helper
    generate_issue_fixtures.py # issue export to matcher regression helper
data/
  bot.db
  watchfacts_state.json
logs/
docs/
```

Top-level modules such as `app.matcher`, `app.search`, `app.tool_runtime`,
`app.telegram_bot`, and `app.mcp_server` remain compatibility/public import
paths for older clients, tests, scripts, and Docker entrypoints. New internal
code should import from the domain packages above.

The repository is implemented through the production-hardening milestone. Agents must still inspect the filesystem before editing because behavior changes quickly.

## Configuration

Expected environment:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram runtime only | None | Telegram bot token |
| `TELEGRAM_ALLOWED_USER_IDS` | No | Empty | Comma-separated owner Telegram user ids; empty allows everyone |
| `TELEGRAM_RESULT_LIMIT` | No | `5` | Number of results to send per Telegram pagination batch |
| `TELEGRAM_MAX_CONCURRENT_SEARCHES` | No | `1` | Maximum WatchFacts searches running at the same time; extra queries wait with a queue notice |
| `WATCHFACTS_URL` | Yes | `https://watchfacts.com/simon-match-making` | WatchFacts page to crawl |
| `HEADLESS` | No | `true` | Browser headless mode |
| `ENABLE_CRAWL4AI` | No | `true` | Reserved compatibility flag; current runtime uses WatchFacts JSON/HTML parsing |
| `SEARCH_CACHE_TTL_SECONDS` | No | `1800` | Fresh-result cache lifetime for identical normalized searches before calling WatchFacts again |
| `SEARCH_MAX_CONCURRENT_SEARCHES` | No | `1` | Maximum non-Telegram WatchFacts searches running at the same time; identical queries still coalesce |
| `WATCHFACTS_HTTP_CLIENT_ENABLED` | No | `true` | Enable the lightweight HTTPX search client used by the normal Telegram/MCP runtime |
| `WATCHFACTS_FORM_CACHE_TTL_SECONDS` | No | `900` | Lifetime for cached WatchFacts search form action and CSRF token used by HTTPX |
| `WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS` | No | `10` | HTTPX connect timeout for WatchFacts requests |
| `WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS` | No | `10` | HTTPX connection-pool acquisition timeout |
| `WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS` | No | `60` | HTTPX keepalive expiry for pooled WatchFacts connections |
| `WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS` | No | `30` | HTTPX read timeout cap for form/cache requests |
| `WATCHFACTS_HTTP_SEARCH_READ_TIMEOUT_SECONDS` | No | `120` | HTTPX read timeout cap for search POSTs (usually longer than generic read timeout) |
| `WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS` | No | `60` | Time to skip HTTPX after a failed HTTPX attempt |
| `WATCHFACTS_HTTP_WARMUP_ON_HEALTH` | No | `true` | Allow MCP health to prefetch and cache the WatchFacts search form |
| `RESULT_PAGE_PUBLIC_BASE_URL` | No | None | Public base URL for generated result pages; feature stays disabled when empty |
| `RESULT_PAGE_TTL_SECONDS` | No | `86400` | Lifetime for generated result page files |
| `RESULT_PAGE_MAX_RESULTS` | No | `200` | Maximum ranked results embedded in one generated result page |
| `RESULT_PAGE_STORAGE_DIR` | No | `data/result_pages` | Directory for generated static result page HTML files |
| `HYBRID_AI_MODE` | No | `off` | Controlled AI mode: `off`, `shadow`, `review`, or `guarded`; only `guarded` can alter search output |
| `OPENAI_API_KEY` | Required when AI mode is not `off` | None | OpenAI API key; never logged or shown in Telegram |
| `OPENAI_MODEL` | No | Cost-conscious current model | Model used for structured refinement suggestions |
| `OPENAI_TIMEOUT_SECONDS` | No | `12` | Maximum OpenAI request time before deterministic fallback |
| `OPENAI_MAX_REFINES` | No | `3` | Maximum snippets sent to OpenAI per query |
| `ENABLE_OPENWA_CHAT_HANDOFF` | No | `false` | Enable OpenWA chat draft creation |
| `OPENWA_BASE_URL` | Required for OpenWA handoff | None | Internal OpenWA API base URL, for example `http://openwa-api:2785` |
| `OPENWA_API_KEY` | Required for OpenWA handoff | None | OpenWA operator API key |
| `OPENWA_DASHBOARD_URL` | No | None | Public OpenWA dashboard URL used for returned links |
| `OPENWA_CHAT_DRAFT_ENDPOINT` | No | `/api/chats/drafts` | OpenWA draft endpoint path |

Configuration rules:

- Load from environment and `.env` during local development.
- Never log real tokens or secrets.
- Fail fast if required values are missing for the selected runtime.
- Treat boolean env values case-insensitively.
- Restrict Telegram handlers to configured user ids when `TELEGRAM_ALLOWED_USER_IDS` is non-empty.
- Load MCP/search runtime settings without requiring `TELEGRAM_BOT_TOKEN`.
- Validate Telegram result limit as a positive integer.
- Use `SEARCH_CACHE_TTL_SECONDS` to reduce repeated WatchFacts backend calls for identical normalized searches.
- Use `SEARCH_MAX_CONCURRENT_SEARCHES` to serialize or bound MCP WatchFacts browser searches.
- Keep `WATCHFACTS_HTTP_CLIENT_ENABLED=true` for normal Telegram/MCP search; disabling it disables query search instead of falling back to Playwright.
- Use `WATCHFACTS_FORM_CACHE_TTL_SECONDS` to reduce repeated WatchFacts form GETs while still refreshing on CSRF/auth failures.
- Set `RESULT_PAGE_PUBLIC_BASE_URL` only when the MCP service is reachable through a public reverse proxy path for `/results/`; leave it empty to preserve legacy responses without page links.
- Result page actions use the existing page token, page TTL, a page-scoped
  `action_nonce`, and action rate limits. Browser code calls only same-origin
  result-page action routes.
- Reuse the process-level HTTPX client for connection pooling; reload cookies and clear form cache when `data/watchfacts_state.json` changes.
- Cap HTTPX read time so slow HTTPX attempts fail fast without launching Playwright.
- Expose only safe HTTPX status metadata in `health`: enabled flag, form-cache freshness, error type, coarse timings, HTTP version, cooldown state, and timestamps.
- Do not expose cookies, CSRF tokens, query text, response bodies, or raw WatchFacts payloads in HTTPX health or diagnostics output.
- Remove local model runtime support from the production path.
- Keep `HYBRID_AI_MODE=off` by default; use `shadow` or `review` to collect safe suggestions before considering `guarded`.
- Require `OPENAI_API_KEY` only when OpenAI-assisted modes are enabled.
- Never log or display `OPENAI_API_KEY`.

## Runtime Architecture

```text
Telegram user request
  -> watchfacts-bot / telegram_bot handler
  -> WatchFactsSearchWorkflow
  -> scraper loads saved browser state and posts the WatchFacts search form through HTTPX
  -> parser extracts listing candidates from JSON response or HTML fallback
  -> matcher filters or scopes listings by query tokens
  -> dedupe removes repeated latest reposts
  -> result scoring orders eligible listings by quality and recency
  -> suspicious-result detector records likely extraction issues
  -> optional OpenAI controlled refiner records suggestions or applies guarded refinements
  -> db records query/cache/dedupe state
  -> result page generation when configured
  -> Telegram summary plus result-page link, with fallback listing batches
```

Supporting MCP path:

```text
MCP client request
  -> watchfacts-mcp tool: search / health / create_chat_draft / issue tools
  -> tool_runtime payload function
  -> same scraper, parser, matcher, dedupe, scoring, cache, and issue logic
  -> MCP payload returns structured results, pagination, images, and result handles
  -> client replies in Vietnamese and may call OpenWA/issue tools later
```

Telegram fallback path:

```text
Telegram update
  -> telegram_bot handler
  -> same search workflow
  -> telegram_bot generates a result page when configured
  -> telegram_bot sends a summary plus result-page link
  -> telegram_bot falls back to paginated result batches only when page generation is unavailable
```

Production quality audits reuse the same search workflow and scoring modules.
They must not duplicate matching, scraping, scoring, or OpenAI logic.

## Module Contracts

### `config.py`

Responsibilities:

- Read environment variables.
- Expose typed config values.
- Validate required settings.
- Define stable runtime paths for `data/`, logs, database, and browser state.

### `runtime/telegram_bot.py`

Responsibilities:

- Register primary Telegram command/message handlers.
- Validate empty or unsupported user input.
- Call the search workflow asynchronously.
- Generate the result page link used as the primary Telegram result surface when
  configured.
- Format listing results for Telegram only as fallback behavior when result-page
  generation is unavailable.
- Catch user-facing errors and return clear messages.
- Notify configured owners when WatchFacts browser session state is missing or expired.
- Provide `/health` to check WatchFacts session validity without exposing browser state.
- Ignore normal group chat messages unless the bot is mentioned or replied to.
- Protect Telegram sends by limiting photo captions to 1024 characters and text messages to 4096 characters.
- Attach feedback callbacks to results, handle feedback issue callbacks, and provide owner issue review commands.

### `runtime/mcp_server.py`

Responsibilities:

- Expose the WatchFacts runtime as MCP tools.
- Serve generated result pages at `GET /results/{token}`.
- Serve result-page action routes for modal actions when enabled:
  `POST /results/{token}/actions/openwa-draft` and
  `POST /results/{token}/actions/report`.
- Keep tool names short and stable: `search`, `health`, `create_chat_draft`,
  `report_issue`, `list_issues`, `get_issue`, `update_issue`,
  `suspicious_summary`.
- Use `query`, `limit`, `offset`, and `include_similar` for `search`.
- Use `issue_type`, `status`, `limit`, and optional `min_severity` for
  `list_issues`.
- Use `include_raw_context` for `get_issue` when a client needs a bounded,
  redacted raw snippet for triage.
- Let follow-up tools accept the short-lived `result_id`, the returned
  `stable_listing_id`, or absolute `rank` when the user refers to a result number.
- Return structured JSON-like payloads without Telegram formatting concerns.
- Avoid leaking raw listings unless a specific safe diagnostic path explicitly
  requests bounded, redacted context.
- Validate result-page action token, expiry, nonce, result identity, and rate
  limits before performing any side effect.
- Keep OpenWA API keys server-side and return only safe draft metadata to the
  browser.

### `runtime/tool_runtime.py`

Responsibilities:

- Provide non-Telegram payload functions used by MCP tools and diagnostics.
- Reuse the same scraper, parser, matcher, dedupe, scoring, cache, suspicious,
  OpenAI, and SQLite behavior as the Telegram workflow.
- Support offset-based pagination with `has_more` and `next_offset`.
- Return short-lived `result_id` cache handles and durable `stable_listing_id`
  values for later handoff or issue reporting.
- Resolve follow-up references from in-memory cache first, then SQLite
  `result_reference_cache` by `result_id`, `stable_listing_id`, or rank before
  re-running a search.
- Include product `image_url` when available.

### `results/result_pages.py`

Responsibilities:

- Generate static HTML result-page artifacts from sanitized result payloads.
- Keep MCP/search structured JSON as the canonical output; result pages are
  presentation/export artifacts linked from that output.
- Load the HTML shell from `app/templates/result_page.html` so presentation
  changes are reviewable outside the Python runtime module.
- Load CSS/JS from `app/static/result_page.css` and
  `app/static/result_page.js`, then embed them into generated pages so result
  pages remain standalone behind `/results/{token}`.
- Keep result-page interactions in plain JavaScript so production CSP can avoid
  `unsafe-eval`; do not introduce a frontend runtime that evaluates template
  expressions in the browser.
- Include additive `result_page_schema_version` metadata in the embedded page
  payload for future presentation/schema migrations.
- Generate and clean up sidecar JSON for result-page actions.
- Keep embedded payloads bounded and script-safe.
- Normalize image/source URLs against the configured WatchFacts URL.
- Redact sensitive-looking text before embedding or storing page payloads.
- Provide server helpers for loading action sidecars by token without changing
  normal HTML page serving.

Result-page action contract:

```text
POST /results/{token}/actions/openwa-draft
POST /results/{token}/actions/report
```

Common request rules:

- Token must match the result page token pattern.
- Page must exist and not be expired.
- Request body must include the page-scoped `action_nonce`.
- `result_id` must match one result from the sidecar payload.
- All errors must be safe JSON and must not include stack traces, settings, API
  keys, cookies, browser state, raw HTML, or `.env` data.

OpenWA action body:

```json
{
  "action_nonce": "...",
  "result_id": "watchfacts-result..."
}
```

Report action body:

```json
{
  "action_nonce": "...",
  "result_id": "watchfacts-result...",
  "reason": "missing_info | wrong_result | other",
  "notes": "optional"
}
```

### `search_result.py`

Responsibilities:

- Hold the shared result dataclass used across Telegram, MCP, and diagnostics.
- Keep product fields stable enough for ranking, formatting, OpenWA handoff, and issue storage.
- Generate short-lived `result_id` values from query/rank/listing snapshots.
- Generate `stable_listing_id` values from source URL plus normalized listing
  text, falling back to normalized listing payload fields when no source URL is
  available.

### `scraper.py`

Responsibilities:

- Load `data/watchfacts_state.json`.
- Use HTTPX for authenticated WatchFacts search POSTs in the normal Telegram/MCP runtime.
- Cache WatchFacts search form action and CSRF token briefly, and refresh on token/auth failures.
- Reuse an HTTPX client manager with bounded connection pool limits and explicit connect/read/write/pool timeouts.
- Serialize form refreshes so concurrent uncached searches do not stampede the WatchFacts form endpoint.
- Launch Chromium with Playwright for login/session checks.
- Navigate to `WATCHFACTS_URL` when checking saved browser-session validity.
- Wait for stable page content.
- Submit the WatchFacts search form when present.
- Return search response text plus metadata indicating whether the server already filtered results.
- Check whether the saved browser session is valid without logging cookies or storage state.

Boundaries:

- Do not bypass login, captcha, Cloudflare, or anti-bot systems.
- Do not log or persist cookies/tokens. HTTPX may read the operator-created Playwright storage state into memory only for authenticated WatchFacts requests.
- Do not store WatchFacts credentials.

### `searching/parser.py`

Responsibilities:

- Parse WatchFacts JSON search responses and fallback HTML with BeautifulSoup/lxml.
- Extract listing candidate objects.
- Normalize missing fields to `None` or empty strings consistently.
- Keep extraction deterministic and unit-testable with HTML fixtures.

### `searching/matcher.py`, `searching/matcher_rules.py`, `searching/matcher_token_classification.py`, and `searching/matcher_rulebook.py`

Responsibilities:

- Keep `app.matcher` as the stable public API for search, dedupe, AI gates, and tests.
- Keep implementation modules under `app/searching/`.
- Keep normalization and tokenization helpers in `matcher_normalization.py`.
- Keep query intent and token classification helpers in
  `matcher_token_classification.py`.
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
- Use query-aware relevance tie-breaks for selected reference, descriptor
  locality, and visible price evidence only after quality and posted date are
  equal.
- Use the same quality/relevance/price score fields when choosing the primary
  result inside a similar-result group, without letting grouping override the
  already-established posted-date order.
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
- [Production Quality Audit Loop Spec](production-quality-audit.md)

### `scripts/diagnostics/audit_quality.py`

Responsibilities:

- Run a curated production/local query set through the normal search workflow.
- Print bounded top-result snippets and scoring diagnostics for maintainers.
- Include result count, posted date, quality group, relevance scores, price
  evidence, and score reason codes.
- Include additive JSONL metadata for query intent, candidate decision, fuzzy
  score, guardrail action, stable audit id, and dedupe keep/drop relation.
- Summarize and compare saved JSONL artifacts with DuckDB without requiring
  WatchFacts credentials.
- Avoid printing secrets, browser state, full page HTML, or unbounded raw
  listings.
- Support focused production verification after matcher, extraction, scoring, or
  quality-gate changes.

### `scripts/fixtures/generate_audit_fixtures.py`

Responsibilities:

- Read `scripts/diagnostics/audit_quality.py --format json` and `--format jsonl`
  output.
- Generate draft quality/scoring pytest cases for non-clean audit rows by
  default.
- Support `--include-clean` for locking accepted clean shorthand examples.
- Keep extraction fixtures on the existing `/issues_export` path when full raw
  listing text is required.

### `match_debug.py`

Responsibilities:

- Combine `explain_extraction()` trace data with structured result scoring.
- Format local debug output for one query/listing pair.
- Cap output length so the same formatter can later be reused in Telegram if an
  owner-only command is added.

The initial debug surface is local-only through `scripts/diagnostics/debug_match.py`.
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
- Tests or smoke checks for production audit formatting once the audit script is
  implemented.
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
