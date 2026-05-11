# Technical Spec

## Tech Stack

- Python 3.11+
- `python-telegram-bot[job-queue]` for Telegram integration
- Playwright Chromium for authenticated browser automation
- Crawl4AI as an optional extraction/debugging layer
- BeautifulSoup4 + lxml for HTML parsing
- SQLite for local cache, dedupe, and query history
- Docker Compose for deployment
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
  utils.py         # small shared helpers only
scripts/
  login.py         # manual WatchFacts login and browser state creation
data/
  bot.db
  watchfacts_state.json
logs/
docs/
```

The repository may be scaffolded before all modules exist. Agents must inspect the filesystem before editing.

## Configuration

Expected environment:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | None | Telegram bot token |
| `WATCHFACTS_URL` | Yes | `https://watchfacts.com/simon-match-making` | WatchFacts page to crawl |
| `HEADLESS` | No | `true` | Browser headless mode |
| `ENABLE_CRAWL4AI` | No | `true` | Enable optional Crawl4AI extraction layer |

Configuration rules:

- Load from environment and `.env` during local development.
- Never log real tokens or secrets.
- Fail fast if required values are missing.
- Treat boolean env values case-insensitively.

## Runtime Architecture

```text
Telegram update
  -> telegram_bot handler
  -> query normalization
  -> scraper fetches WatchFacts HTML with saved browser state
  -> parser extracts listing candidates
  -> matcher filters listings by query tokens
  -> dedupe removes repeated listings
  -> db records query/cache/dedupe state
  -> telegram_bot formats and sends results
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

### `scraper.py`

Responsibilities:

- Launch Chromium with Playwright.
- Load `data/watchfacts_state.json`.
- Navigate to `WATCHFACTS_URL`.
- Wait for stable page content.
- Return raw HTML and optional debug metadata.

Boundaries:

- Do not bypass login, captcha, Cloudflare, or anti-bot systems.
- Do not read or log cookies/tokens except through Playwright's normal storage-state mechanism.
- Do not store WatchFacts credentials.

### `parser.py`

Responsibilities:

- Parse HTML with BeautifulSoup/lxml.
- Extract listing candidate objects.
- Normalize missing fields to `None` or empty strings consistently.
- Keep extraction deterministic and unit-testable with HTML fixtures.

### `matcher.py`

Responsibilities:

- Normalize user query and listing text.
- Tokenize query text.
- Apply case-insensitive all-token matching.
- Optionally use regex for robust model/reference matching.

Initial matching rule:

```text
listing matches query if every normalized query token appears in normalized listing text
```

### `dedupe.py`

Responsibilities:

- Build a stable dedupe key:

```text
normalized_text + seller + posted_date
```

- Remove duplicates within a result set.
- Provide deterministic normalization helpers.

### `db.py`

Responsibilities:

- Manage SQLite connection lifecycle.
- Create schema if missing.
- Persist query history and dedupe/cache data.
- Use parameterized SQL only.

## Data Model

Initial SQLite tables should cover:

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

Preferred tests once code exists:

- Unit tests for `matcher.py`, `dedupe.py`, and parser fixtures.
- Integration tests for SQLite schema and query/listing persistence.
- Handler-level tests for Telegram formatting and error branches.
- Optional browser smoke test for login/session flow when credentials are available.

Recommended commands:

```bash
python -m pytest
python -m compileall app scripts
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
