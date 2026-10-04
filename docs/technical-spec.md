# Technical Specification

## System Boundary

WatchFacts is a Telegram-first deterministic search runtime. It has no public
search API and no MCP transport. A separate HTTP service exposes only generated
result pages and their bounded server-side actions.

```text
Telegram -> SearchUseCase -> WatchFacts fetch -> parser -> matcher
         -> scoring -> dedupe -> cache/issues -> result-page artifact

Browser -> watchfacts-web -> result page / feedback action
```

## Packages

```text
app/
  main.py
  web_server.py
  runtime/
    telegram_bot.py
    web_server.py
    tool_runtime.py
  application/
    search_use_case.py
    search_payload_use_case.py
    issue_triage_use_case.py
    result_reference_use_case.py
  searching/
    search.py
    parser.py
    matcher.py
    matcher_rules.py
    query_intent.py
    result_scoring.py
    dedupe.py
    similarity.py
    search_result.py
  integrations/
    scraper.py
    watchfacts_http.py
    ai_refiner.py
  infrastructure/
    search_cache_repository.py
    result_reference_repository.py
    issue_repository.py
    ai_suggestion_repository.py
    openai_client.py
  results/
    result_pages.py
```

Root-level compatibility imports remain for stable internal imports. New code
should use the layered package paths.

## Runtime Services

### `watchfacts-bot`

- Entrypoint: `python -m app.main`.
- Loads Telegram configuration and the shared search workflow.
- Owns user authorization, queueing, Telegram formatting, pagination callbacks,
  owner review commands and feedback callbacks.
- Generates result-page artifacts when configured.

### `watchfacts-web`

- Entrypoint: `python -m app.web_server`.
- Listens on container port 8766, published to legacy-compatible host-local
  port 8765 only.
- Exposes:
  - `GET /healthz`
  - `GET /results/{token}`
  - `POST /results/{token}/actions/report`
- Does not expose search, issue review, browser state, or database access.

## Search Pipeline

`WatchFactsSearchWorkflow`:

1. Normalizes and classifies the query.
2. Builds a deterministic retrieval plan.
3. Reads fresh SQLite search cache or coalesces identical in-flight work.
4. Fetches WatchFacts using the authorized HTTPX session, with the documented
   Playwright session boundary.
5. Parses JSON/HTML into listing candidates.
6. Applies deterministic matching and extraction rules.
7. Deduplicates latest reposts and repeated text.
8. Scores and ranks eligible results.
9. Groups bounded similar results.
10. Records cache, query metrics, suspicious issues, and audit events.

OpenAI refinement is optional. It may change output only in guarded mode after
schema, confidence, substring, separator, query-match, and risk validation.

## Result Identity

- `result_id`: short-lived handle tied to query, rank, and listing snapshot.
- `stable_listing_id`: restart-tolerant identity derived from source and
  normalized listing evidence.
- source URL/listing number: upstream evidence when available.

Result pages and Telegram callbacks must never invent identity or listing data.

## Result Pages

Generation writes:

```text
data/result_pages/{token}.html
data/result_pages/{token}.json
```

The sidecar contains sanitized displayed fields and an action nonce. It must not
contain raw HTML, cookies, browser state, API keys, or unbounded raw listings.

Action validation order:

1. Rate limit client/token/action.
2. Validate token syntax and page TTL.
3. Load and validate the sidecar.
4. Parse bounded JSON.
5. Compare the action nonce in constant time.
6. Resolve the requested result only inside the page payload.
7. Delegate to the issue use case.

## Persistence

SQLite stores:

- query history and listings;
- query-result ranking;
- search cache and result-reference cache;
- feedback and suspicious issues;
- AI refinement suggestions.

Connections enable foreign keys and a bounded busy timeout. SQL must remain
parameterized. Schema changes require tests and documentation.

## Important Configuration

| Key | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | required for bot | Telegram credential |
| `TELEGRAM_ALLOWED_USER_IDS` | empty/public | Optional user allowlist |
| `WATCHFACTS_URL` | trading page | Authorized source page |
| `SEARCH_CACHE_TTL_SECONDS` | `1800` | Shared search cache TTL |
| `SEARCH_MAX_CONCURRENT_SEARCHES` | `1` | Direct-runtime search backpressure |
| `SEARCH_RETRIEVAL_CONCURRENCY` | `1` | Retrieval branch concurrency |
| `RESULT_PAGE_PUBLIC_BASE_URL` | empty | Enables generated result pages |
| `RESULT_PAGE_TTL_SECONDS` | `86400` | Result-page lifetime |
| `RESULT_PAGE_STORAGE_DIR` | `data/result_pages` | Shared artifact directory |
| `HYBRID_AI_MODE` | `off` | Optional controlled AI mode |

## Compatibility And Diagnostics

`app/runtime/tool_runtime.py` remains a transport-neutral structured-payload
adapter used by direct diagnostics and contract tests. It must not grow a new
network protocol or duplicate search behavior.

Diagnostic scripts call the shared runtime directly:

- `runtime_smoke.py`
- `benchmark_search_queries.py`
- `prewarm_search_cache.py`
- `audit_quality.py`

## Verification

```bash
python -m pytest -q
python -m compileall app scripts
docker compose config
```

For production search changes also run direct-runtime smoke, cold/hot benchmark,
quality audit, web health, Telegram smoke, and result-page action smoke.
