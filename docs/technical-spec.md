# Technical Spec

## Tech Stack

- Python 3.11+
- `python-telegram-bot[job-queue]` for Telegram integration
- Playwright Chromium for authenticated browser automation
- WatchFacts authenticated JSON search response parsing with HTML fallback
- BeautifulSoup4 + lxml for HTML parsing
- SQLite for local cache, dedupe, and query history
- Docker Compose for deployment
- Optional llama.cpp service for local LLM experiments
- Makefile for repeatable local commands

## Intended Project Structure

```text
app/
  main.py          # application entrypoint
  telegram_bot.py  # Telegram handlers and message formatting
  scraper.py       # Playwright browser/session/crawl logic
  parser.py        # HTML/listing extraction
  matcher.py       # query normalization and deterministic matching
  dedupe.py        # listing identity and duplicate filtering
  db.py            # SQLite schema and persistence
  config.py        # environment/config loading
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
| `WATCHFACTS_URL` | Yes | `https://watchfacts.com/simon-match-making` | WatchFacts page to crawl |
| `HEADLESS` | No | `true` | Browser headless mode |
| `ENABLE_CRAWL4AI` | No | `true` | Reserved compatibility flag; current runtime uses WatchFacts JSON/HTML parsing |
| `LOCAL_LLM_ENABLED` | No | `false` | Enables local LLM experiment code paths when explicitly implemented |
| `LOCAL_LLM_BASE_URL` | No | `http://localhost:8080` | Local llama.cpp server URL; use `http://llama-cpp:8080` inside Docker Compose |
| `LOCAL_LLM_MODEL` | No | `gemma-4-E2B-it-Q8_0.gguf` | Local model identifier sent to the chat API |
| `LOCAL_LLM_TIMEOUT_SECONDS` | No | `30` | Local LLM HTTP timeout for experiments; `8` is recommended for bot trials with CPU inference |
| `LOCAL_LLM_MAX_REFINES` | No | `3` | Maximum snippets refined by the local LLM per query |
| `LLAMA_CPP_IMAGE` | No | `ghcr.io/ggml-org/llama.cpp:server` | Docker image for the experimental llama.cpp service |
| `LLAMA_CPP_PORT` | No | `8080` | Host port for llama.cpp |
| `LLAMA_CPP_MODELS_DIR` | No | `./models` | Host directory containing GGUF files; ignored by git |
| `LLAMA_CPP_MODEL_FILE` | No | `gemma-4-E2B-it-Q8_0.gguf` | GGUF filename mounted under `/models` |
| `LLAMA_CPP_CTX_SIZE` | No | `4096` | llama.cpp context size |
| `LLAMA_CPP_PREDICT` | No | `256` | llama.cpp max generated token count |

Configuration rules:

- Load from environment and `.env` during local development.
- Never log real tokens or secrets.
- Fail fast if required values are missing.
- Treat boolean env values case-insensitively.
- Restrict Telegram handlers to configured user ids when `TELEGRAM_ALLOWED_USER_IDS` is non-empty.
- Validate Telegram result limit as a positive integer.
- Keep local LLM settings optional and disabled by default.

## Runtime Architecture

```text
Telegram update
  -> telegram_bot handler
  -> scraper loads saved browser state and posts the WatchFacts search form when available
  -> parser extracts listing candidates from JSON response or HTML fallback
  -> matcher filters or scopes listings by query tokens
  -> dedupe removes repeated latest reposts
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
- Ignore normal group chat messages unless the bot is mentioned or replied to.
- Protect Telegram sends by limiting photo captions to 1024 characters and text messages to 4096 characters.

### `scraper.py`

Responsibilities:

- Launch Chromium with Playwright.
- Load `data/watchfacts_state.json`.
- Navigate to `WATCHFACTS_URL`.
- Wait for stable page content.
- Submit the WatchFacts search form when present.
- Return search response text plus metadata indicating whether the server already filtered results.

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

### `matcher.py`

Responsibilities:

- Normalize user query and listing text.
- Tokenize query text.
- Apply case-insensitive all-token matching.
- Use regex for robust model/reference matching, including compound references.
- Extract the relevant product segment from stock-list cards that contain multiple listings.
- Treat year/date/price-like numeric query tokens as descriptors instead of independent references.
- Normalize Unicode mark characters so keycap digit prices such as `$8️⃣0️⃣k` match normal price queries.
- Stop product segment extraction before seller/member metadata boundaries.

Matching rule:

```text
listing matches query if every normalized query token appears in the relevant listing text,
with stricter handling for model/reference tokens
```

When WatchFacts returns server-filtered JSON search results, the workflow keeps those results and only uses the matcher to scope display text. This avoids over-filtering server matches that are relevant but do not contain every local descriptor in the same form.

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
- Use parameterized SQL only.

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

## Error Handling

Expected error categories:

- Config error: missing token, missing URL, invalid boolean.
- Login/session error: missing or expired browser state.
- Crawl error: navigation timeout, unexpected page state.
- Parse error: no listing container found or extraction failure.
- Telegram error: message send failure, invalid chat state.
- Database error: SQLite unavailable or schema migration issue.

User-facing messages should be concise. Logs should include enough detail for operators without leaking secrets.

## Testing Strategy

Preferred tests:

- Unit tests for `matcher.py`, `dedupe.py`, and parser fixtures.
- Integration tests for SQLite schema and query/listing persistence.
- Handler-level tests for Telegram formatting and error branches.
- Optional browser smoke test for login/session flow when credentials are available.

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
- Adding LLM behavior.
- Changing the dedupe identity.
- Changing data retention behavior.
- Adding dependencies beyond the current stack.

Never:

- Commit real secrets or browser session state.
- Bypass WatchFacts access controls.
- Store WatchFacts passwords.
- Log cookies, tokens, or full storage state.
