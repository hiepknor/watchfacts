# WatchFacts Runtime + MCP Bridge

Self-hosted WatchFacts search runtime for Hermes, MCP tools, OpenWA handoff,
and the legacy Telegram bot.

The project keeps WatchFacts search logic in a non-Telegram runtime. Hermes calls
that runtime through the `watchfacts-mcp` service, receives structured ranked
results, paginates with `offset` / `next_offset`, uses `image_url` for product
photos, and can create OpenWA chat drafts from `result_id`, `stable_listing_id`,
or absolute rank references returned by search.

The Telegram bot still exists as a supported legacy channel, but the current
production integration target is Hermes over MCP.

## Features

- Hermes MCP integration
- Legacy Telegram bot integration
- WatchFacts authenticated search
- HTTPX WatchFacts search client
- Playwright browser automation for login/session checks
- WatchFacts JSON search response parsing with HTML fallback
- BeautifulSoup + lxml HTML parsing
- Regex and token-based matching
- Duplicate listing filtering
- Structured MCP tools for search, health, chat draft handoff, issue review, and suspicious QA
- Offset-based MCP pagination with stable result ranks, short-lived `result_id` handles, and durable `stable_listing_id` references
- Product image propagation via `image_url`
- Summary-first Telegram pagination with "Show results" / "Load more"
- Telegram message length guards for long listings
- WatchFacts session health check and owner alert when login state expires
- One-tap result feedback and owner issue review commands
- SQLite local cache
- Non-Telegram search payload runtime for Hermes/MCP-style wrappers
- Docker Compose service for `watchfacts-mcp`
- Makefile deploy target for MCP + Hermes restart
- Optional OpenAI controlled refinement for hard cases
- Docker deployment
- Fully async architecture
- No LLM required
- Free and self-hosted

## Stack

- Python
- MCP / FastMCP-compatible server
- python-telegram-bot
- HTTPX
- Playwright
- BeautifulSoup4
- lxml
- Regex
- SQLite
- Docker
- Hermes, external deployment

## Requirements

- Python 3.11+
- Docker, optional
- Telegram bot token, only when running the legacy Telegram bot
- Valid WatchFacts account
- Linux server or local machine
- Hermes server, only when using the MCP integration

## Quick Start

```bash
git clone https://github.com/hiepknor/watchfacts.git
cd watchfacts

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

Create `.env`:

```bash
cp .env.example .env
```

Then edit `.env`.

For Hermes/MCP-only runtime, the key requirements are the WatchFacts URL, valid
browser state, and any OpenWA settings needed for `create_chat_draft`.

For the legacy Telegram bot, set the real Telegram bot token. Leave
`TELEGRAM_ALLOWED_USER_IDS` empty if everyone may use the bot. Set it to one or
more Telegram user IDs, separated by commas, to restrict usage to those owners
only.
Set `TELEGRAM_RESULT_LIMIT` to control how many results are sent per Telegram
batch.
Set `TELEGRAM_MAX_CONCURRENT_SEARCHES` to control how many WatchFacts searches
may run at the same time; extra queries show a queue message and wait.
Set `SEARCH_CACHE_TTL_SECONDS` to reuse fresh identical search results before
calling WatchFacts again; the default is 300 seconds.
Set `SEARCH_MAX_CONCURRENT_SEARCHES` to limit concurrent non-Telegram
WatchFacts searches, including Hermes/MCP requests; the default is 1.
Keep `WATCHFACTS_HTTP_CLIENT_ENABLED=true` for the normal Telegram and MCP
search runtime. Playwright is reserved for manual login/session checks in the
production path; disabling the HTTPX client disables query search instead of
falling back to Playwright.
Set `WATCHFACTS_FORM_CACHE_TTL_SECONDS` to control how long the HTTPX search
client reuses the authenticated WatchFacts search form action and CSRF token;
the default is 900 seconds.
The HTTPX client is reused within the process for connection pooling and reloads
cookies when `data/watchfacts_state.json` changes. Tune
`WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS`,
`WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS`,
`WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS`,
`WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS`, and
`WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS` only when production logs show
connection, pool, or timeout pressure. `WATCHFACTS_HTTP_WARMUP_ON_HEALTH=true`
lets MCP health prefetch the search form so the next HTTPX search can POST
without a cold form GET.

Create an authenticated WatchFacts browser session:

```bash
python scripts/ops/login.py
```

Run the bot locally:

```bash
python -m app.main
```

Run the legacy Telegram bot with Docker:

```bash
make init
make deploy-bot
```

Run MCP + legacy bot for standard deployment:

```bash
make deploy
```

When enabling OpenWA chat draft handoff for Hermes/MCP, configure the OpenWA
values in `.env` and deploy with the standard target:

```bash
make deploy
```

The legacy Telegram bot can still use `make deploy-bot OPENWA_COMPOSE=1` when it
needs the separate OpenWA compose override.

## Commands

| Command | Description |
| --- | --- |
| `make init` | Create `data/`, `logs/`, and `.env` from `.env.example` when missing |
| `make verify-env` | Check `.env` and `data/watchfacts_state.json` before deploy |
| `make predeploy-check` | Run pytest plus repository checks |
| `make deploy` | Alias for `make deploy-bot-mcp` |
| `make deploy-bot` | Pull latest code, build, recreate the legacy Telegram bot, and show startup logs |
| `make deploy-mcp` | Pull latest code, build, test, audit, and recreate `watchfacts-mcp` |
| `make deploy-bot-mcp` | Pull latest code, build, and deploy both legacy bot and MCP service |
| `make deploy-hermes-mcp` | Deploy `watchfacts-mcp`, wait for health, recreate Hermes, and run MCP smoke |
| `make deploy-bot OPENWA_COMPOSE=1` | Deploy legacy bot with the OpenWA network override |
| `make pull` | Pull latest git changes unless `SKIP_PULL=1` |
| `make build` | Build the Docker image |
| `make mcp-predeploy-check` | Run MCP predeploy checks inside the MCP Compose service |
| `make mcp-up` | Start `watchfacts-mcp` with the Hermes network override |
| `make mcp-logs` | Follow `watchfacts-mcp` logs |
| `make mcp-ps` | Show `watchfacts-mcp` status |
| `make mcp-smoke` | Run one authorized HTTPX WatchFacts search smoke check |
| `make mcp-smoke-set` | Validate MCP `search` shape for representative queries |
| `make quality-audit` | Run the default bounded quality audit query set |
| `make predeploy-quality-check` | Run local checks plus the default quality audit |
| `make restart-hermes` | Recreate Hermes after MCP schema/config changes |
| `make hermes-logs` | Follow Hermes logs |
| `make hermes-ps` | Show Hermes status |
| `make up` | Start the bot with Docker Compose |
| `make down` | Stop Docker Compose services |
| `make restart` | Restart the bot service |
| `make logs` | Follow bot logs |
| `make ps` | Show Compose service status |
| `make shell` | Open a shell in the bot container |
| `make run` | Run the bot locally on the host |
| `make login` | Run the WatchFacts browser login locally on the host |
| `make check` | Run repository checks |
| `python scripts/ops/login.py` | Open Chromium for manual WatchFacts login and save browser state |
| `python scripts/diagnostics/debug_match.py <query> <listing>` | Inspect matcher trace and result score locally |
| `python -m app.main` | Run the Telegram bot locally |
| `docker compose build` | Build the Docker image |
| `docker compose up -d` | Start the bot in the background |
| `docker compose logs -f` | Follow container logs |

## Project Structure

```text
watchfacts/
├── app/
│   ├── main.py
│   ├── telegram_bot.py
│   ├── mcp_server.py
│   ├── tool_runtime.py
│   ├── scraper.py
│   ├── search.py
│   ├── search_result.py
│   ├── openwa_handoff.py
│   ├── issues.py
│   ├── parser.py
│   ├── matcher.py          # stable public matcher API
│   ├── matcher_normalization.py # normalization and tokenization helpers
│   ├── matcher_token_classification.py # query/token classifiers
│   ├── matcher_rules.py    # deterministic matcher implementation
│   ├── matcher_rulebook.py # rule taxonomy and extraction trace types
│   ├── dedupe.py
│   ├── db.py
│   └── config.py
├── scripts/
│   ├── ops/
│   │   └── login.py
│   ├── diagnostics/
│   │   ├── audit_quality.py
│   │   ├── benchmark_hard_cases.py
│   │   └── debug_match.py
│   └── fixtures/
│       ├── generate_audit_fixtures.py
│       └── generate_issue_fixtures.py
├── data/
│   ├── bot.db
│   └── watchfacts_state.json
├── docs/
├── logs/
├── Dockerfile
├── docker-compose.yml
├── docker-compose.watchfacts-mcp.yml
├── Makefile
├── requirements.txt
├── .env.example
├── .env
├── .dockerignore
├── .gitignore
└── README.md
```

## Documentation

Detailed project docs live in [docs/](docs/README.md):

- [Project Soul](SOUL.md)
- [Product Spec](docs/product-spec.md)
- [Technical Spec](docs/technical-spec.md)
- [Implementation Plan](docs/implementation-plan.md)
- [Result Quality Scoring Spec](docs/result-quality-scoring.md)
- [Roadmap](docs/roadmap.md)
- [Operations Guide](docs/operations.md)
- [Security And Compliance](docs/security-compliance.md)
- [Contributing](docs/contributing.md)
- [Architecture Decisions](docs/decisions/)

## Authentication

The bot uses an authenticated browser session. It does not store the WatchFacts password inside the bot.

Run:

```bash
python scripts/ops/login.py
```

The script opens Chromium, lets you log in manually, and saves the authenticated session to:

```text
data/watchfacts_state.json
```

The bot reuses this session automatically when crawling WatchFacts.

## Hermes MCP Runtime

External wrappers can reuse the same search pipeline without requiring a
Telegram token:

```python
from app.tool_runtime import watchfacts_search_payload

payload = await watchfacts_search_payload(
    "5712g",
    limit=5,
    offset=0,
    include_similar=True,
    include_raw=False,
)
```

This path uses `load_search_settings()` internally. It still needs the
WatchFacts browser state in `data/watchfacts_state.json`, and it shares the same
SQLite cache and deterministic parser/matcher/scoring logic as the Telegram bot.

The MCP bridge exposes the runtime as structured tools for Hermes. Hermes config
should include only the tools this project owns, normally:

```yaml
mcp_servers:
  watchfacts:
    url: "http://watchfacts-mcp:8765/mcp"
    timeout: 120
    tools:
      include:
        - search
        - health
        - create_chat_draft
        - report_issue
        - list_issues
        - get_issue
        - update_issue
        - suspicious_summary
```

`search(query, limit=5, offset=0, include_similar=true)` returns ranked results,
`has_more`, and `next_offset`. Hermes should reuse the original query and pass
`offset=next_offset` for "load more" follow-ups. Search results include a
short-lived `result_id` cache handle, durable `stable_listing_id`, and absolute
`rank` for later handoff/feedback. Follow-up tools accept the returned
`result_id`, the returned `stable_listing_id`, or `rank` when the user says
"result 20".
Results include `image_url` for product photos when WatchFacts provides one.

Tool catalog:

| Tool | Purpose |
| --- | --- |
| `search` | Search WatchFacts and return paginated ranked results with `result_id`, `stable_listing_id`, and absolute `rank` references |
| `health` | Check WatchFacts session, database, OpenWA, and search readiness |
| `create_chat_draft` | Create an OpenWA chat draft from a prior `search` result by `result_id`, `stable_listing_id`, or `rank` |
| `report_issue` | Record result feedback for owner review |
| `list_issues` | List feedback and suspicious QA issues by `status=open/fixed/ignored/all` |
| `get_issue` | Read one feedback or suspicious issue by `F<id>` or `S<id>` with bounded raw context |
| `update_issue` | Mark an issue `open`, `fixed`, or `ignored` |
| `suspicious_summary` | Summarize open suspicious QA backlog |

## OpenAI Controlled Intelligence

The bot remains deterministic by default. The AI path is OpenAI-only:
OpenAI may suggest scoped result refinements for suspicious or reported cases,
but deterministic parser/matcher behavior remains the baseline and fallback.

Configuration:

```bash
HYBRID_AI_MODE=off
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
OPENAI_TIMEOUT_SECONDS=12
OPENAI_MAX_REFINES=3
```

Use `shadow` first to record suggestions without changing Telegram output, then
`review` for owner-visible suggestions. In `guarded`, OpenAI runs only for
eligible hard snippets or allowlisted suspicious reasons and can alter output
only when the suggestion is copied from the raw listing text and passes local
query, substring, separator, length, confidence, and risk gates.

The older local model experiment has been removed from the supported runtime.
Do not add new production behavior that depends on local model files.

## Telegram Usage

By default, any Telegram user who can message the bot can use it. To make the
bot owner-only, set `.env` like:

```bash
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Multiple owners are supported:

```bash
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
```

Control the number of results sent per button click:

```bash
TELEGRAM_RESULT_LIMIT=5
```

Control concurrent WatchFacts searches:

```bash
TELEGRAM_MAX_CONCURRENT_SEARCHES=1
```

Control fresh search cache TTL:

```bash
SEARCH_CACHE_TTL_SECONDS=300
```

Control Hermes/MCP WatchFacts search concurrency:

```bash
SEARCH_MAX_CONCURRENT_SEARCHES=1
```

Control the HTTPX search client and WatchFacts form cache:

```bash
WATCHFACTS_HTTP_CLIENT_ENABLED=true
WATCHFACTS_FORM_CACHE_TTL_SECONDS=900
WATCHFACTS_HTTP_CONNECT_TIMEOUT_SECONDS=10
WATCHFACTS_HTTP_POOL_TIMEOUT_SECONDS=10
WATCHFACTS_HTTP_KEEPALIVE_EXPIRY_SECONDS=60
WATCHFACTS_HTTP_READ_TIMEOUT_SECONDS=30
WATCHFACTS_HTTP_FAILURE_COOLDOWN_SECONDS=60
WATCHFACTS_HTTP_WARMUP_ON_HEALTH=true
```

Bot commands:

| Command | Purpose |
| --- | --- |
| `/start` | Open the visual intro and examples |
| `/help` | Show search flow, examples, and pagination actions |
| `/settings` | Show safe runtime settings without secrets |
| `/health` | Check WatchFacts session health without exposing cookies or browser state |
| `/issues` | List open user feedback issues |
| `/suspicious` | List high-severity auto-suspicious QA flags |
| `/suspicious_summary` | Show auto-suspicious breakdown by reason, severity, and query count |
| `/issue F<id>` or `/issue S<id>` | Show one feedback or suspicious issue in detail |
| `/issue_done F<id>` or `/issue_done S<id>` | Mark an issue as fixed/reviewed |
| `/issue_ignore F<id>` or `/issue_ignore S<id>` | Ignore a false positive issue |
| `/issues_export` | Export open user feedback issues as JSON for regression tests |
| `/suspicious_export` | Export auto-suspicious QA flags as JSON for regression tests |
| `/ai_suggestions` | List OpenAI suggestions waiting for owner review |
| `/ai_suggestion <id>` | Show one OpenAI suggestion with gate details |
| `/ai_accept <id>` | Accept a reviewed OpenAI suggestion for regression export |
| `/ai_ignore <id>` | Ignore an unsafe or unhelpful OpenAI suggestion |
| `/ai_suggestions_export` | Export accepted OpenAI suggestions as matcher fixtures |
| `/cancel` | Clear pending result buttons |

Generate a draft matcher regression test from exported issues or accepted AI
suggestions:

```bash
python scripts/fixtures/generate_issue_fixtures.py issues.json > /tmp/test_exported_issues.py
```

Send a watch query to the bot:

```text
228253a choco
```

In a group, the bot ignores normal chat messages. Start a group search by
mentioning the bot at the beginning of the message or by replying to a bot
message:

```text
@bot_username 228253a choco
```

Example response:

```text
🏷️ 228253A choco N2 467000hkd

👤 HK STOCKS

📅 20/04/2026
```

The bot sends a result summary first. Press "Show results" to receive the first
result batch, then use "Load more" for the next batches.

## Search And Matching Logic

The bot uses deterministic matching. No AI or LLM is required for core search.

For a query like:

```text
228253a choco
```

The listing must contain both tokens:

- `228253a`
- `choco`

Matching is:

- Case-insensitive
- Token-based
- Regex-assisted
- Strict for model/reference tokens, including compound references such as `7118/1200A`
- Scoped to the relevant product segment when a WatchFacts card contains multiple listings
- Tolerant of common WatchFacts/Telegram listing quirks such as emoji, keycap digit prices, compact dates, year descriptors, and seller metadata boundaries

## Deduplication

Persistent listing identity is stored with:

```text
normalized_text + seller + posted_date
```

Normalization includes:

- Lowercasing
- Trimming spaces
- Collapsing repeated whitespace
- Normalizing punctuation

Search results also run a latest-repost pass that groups by normalized listing text and seller, ignores repost date for that grouping, and keeps the newest posted date when the same seller reposts the same item.

Final output ranking is quality-first, then newest posted date descending inside
the same quality group. Clean results outrank missing-price or suspicious
results; lower-quality results are demoted, not hidden.

## Database

SQLite database location:

```text
data/bot.db
```

Used for:

- Query history
- Listing history
- Dedupe
- Search result reference cache for `result_id`, `stable_listing_id`, and rank follow-ups

## Docker Deployment

The Docker entrypoint expects the application module at `app/main.py`.

Build and start the service:

```bash
make init
make build
make up
```

Follow logs:

```bash
make logs
```

Example `docker-compose.yml` service:

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    env_file:
      - .env
```

## Dependencies

Current `requirements.txt`:

```text
python-telegram-bot[job-queue]
mcp>=1.0,<2
httpx
playwright
crawl4ai
beautifulsoup4
lxml
python-dotenv
pytest
```

`ENABLE_CRAWL4AI` remains in config as a compatibility flag. Current production
search uses WatchFacts JSON responses and HTML parsing, with HTTPX for search
POSTs and Playwright retained for login and session health.

Benchmark authorized HTTPX search latency without printing cookies, CSRF tokens,
query output, or response bodies:

```bash
python scripts/diagnostics/benchmark_watchfacts_http.py --query "5712g" --warmup --repeat 3
```

Run a single authorized HTTPX smoke search with the default query `5712g`:

```bash
make mcp-smoke
```

Validate the deployed MCP `search` response shape for representative queries:

```bash
make mcp-smoke-set
```

## Ignored Files

Recommended `.gitignore` entries:

```gitignore
.env
__pycache__/
data/watchfacts_state.json
data/bot.db
logs/
.venv/
```

## Compliance

Use this project only with:

- Authorized access
- A valid WatchFacts account
- Compliance with WatchFacts Terms

The bot does not:

- Bypass login
- Bypass captcha
- Bypass Cloudflare
- Bypass anti-bot systems

## Recommended Server

Minimum:

- 1 vCPU
- 1 GB RAM
- Ubuntu 22.04

Recommended:

- 2 vCPU
- 2 GB RAM
- Ubuntu 22.04

## Future Improvements

- Multi-page crawling
- Scheduled refresh jobs
- Dealer filtering
- Price normalization
- Image caching
- Export results
- Multiple watch sources

## License

MIT License
