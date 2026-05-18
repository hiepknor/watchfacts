# WatchFacts Telegram Bot

Self-hosted Telegram bot for searching WatchFacts listings with an authenticated browser session.

The bot receives a short watch query, searches WatchFacts through the logged-in browser session, parses the returned listings, matches deterministically, removes latest repost duplicates, stores query history in SQLite, and returns Telegram-friendly results with product image, listing text, seller, and posted date.

## Features

- Telegram bot integration
- WatchFacts authenticated search
- Playwright browser automation
- WatchFacts JSON search response parsing with HTML fallback
- BeautifulSoup + lxml HTML parsing
- Regex and token-based matching
- Duplicate listing filtering
- Summary-first Telegram pagination with "Xem kết quả" / "Xem thêm"
- Telegram message length guards for long listings
- WatchFacts session health check and owner alert when login state expires
- One-tap result feedback and owner issue review commands
- SQLite local cache
- Optional OpenAI controlled refinement for hard cases
- Docker deployment
- Fully async architecture
- No LLM required
- Free and self-hosted

## Stack

- Python
- python-telegram-bot
- Playwright
- BeautifulSoup4
- lxml
- Regex
- SQLite
- Docker

## Requirements

- Python 3.11+
- Docker, optional
- Telegram bot token
- Valid WatchFacts account
- Linux server or local machine

## Quick Start

```bash
git clone https://github.com/hiepknor/watchfacts-bot.git
cd watchfacts-bot

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

Create `.env`:

```bash
cp .env.example .env
```

Then edit `.env` with your real Telegram bot token.
Leave `TELEGRAM_ALLOWED_USER_IDS` empty if everyone may use the bot. Set it to
one or more Telegram user IDs, separated by commas, to restrict usage to those
owners only.
Set `TELEGRAM_RESULT_LIMIT` to control how many results are sent per Telegram
batch.
Set `TELEGRAM_MAX_CONCURRENT_SEARCHES` to control how many WatchFacts searches
may run at the same time; extra queries show a queue message and wait.
Set `SEARCH_CACHE_TTL_SECONDS` to reuse fresh identical search results before
calling WatchFacts again; the default is 300 seconds.

Create an authenticated WatchFacts browser session:

```bash
python scripts/login.py
```

Run the bot locally:

```bash
python -m app.main
```

Or run it with Docker:

```bash
make init
make deploy
```

## Commands

| Command | Description |
| --- | --- |
| `make init` | Create `data/`, `logs/`, and `.env` from `.env.example` when missing |
| `make verify-env` | Check `.env` and `data/watchfacts_state.json` before deploy |
| `make predeploy-check` | Run pytest plus lightweight repository checks |
| `make deploy` | Pull latest code, build, recreate the bot, and show startup logs |
| `make deploy SKIP_PULL=1` | Deploy local unpushed changes |
| `make pull` | Pull latest git changes unless `SKIP_PULL=1` |
| `make build` | Build the Docker image |
| `make up` | Start the bot with Docker Compose |
| `make down` | Stop Docker Compose services |
| `make restart` | Restart the bot service |
| `make logs` | Follow bot logs |
| `make ps` | Show Compose service status |
| `make shell` | Open a shell in the bot container |
| `make run` | Run the bot locally on the host |
| `make login` | Run the WatchFacts browser login locally on the host |
| `make check` | Run lightweight repository checks |
| `python scripts/login.py` | Open Chromium for manual WatchFacts login and save browser state |
| `python scripts/debug_match.py <query> <listing>` | Inspect matcher trace and result score locally |
| `python -m app.main` | Run the Telegram bot locally |
| `docker compose build` | Build the Docker image |
| `docker compose up -d` | Start the bot in the background |
| `docker compose logs -f` | Follow container logs |

## Project Structure

```text
watchfacts-bot/
├── app/
│   ├── main.py
│   ├── telegram_bot.py
│   ├── scraper.py
│   ├── parser.py
│   ├── matcher.py          # stable public matcher API
│   ├── matcher_normalization.py # normalization and tokenization helpers
│   ├── matcher_rules.py    # deterministic matcher implementation
│   ├── matcher_rulebook.py # rule taxonomy and extraction trace types
│   ├── dedupe.py
│   ├── db.py
│   └── config.py
├── scripts/
│   └── login.py
├── data/
│   ├── bot.db
│   └── watchfacts_state.json
├── docs/
├── logs/
├── Dockerfile
├── docker-compose.yml
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
python scripts/login.py
```

The script opens Chromium, lets you log in manually, and saves the authenticated session to:

```text
data/watchfacts_state.json
```

The bot reuses this session automatically when crawling WatchFacts.

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

Bot commands:

| Command | Purpose |
| --- | --- |
| `/start` | Open the visual intro and examples |
| `/help` | Show search flow, examples, and pagination actions |
| `/settings` | Show safe runtime settings without secrets |
| `/health` | Check WatchFacts session health without exposing cookies or browser state |
| `/issues` | List open feedback and suspicious result issues |
| `/issue F<id>` or `/issue S<id>` | Show one feedback or suspicious issue in detail |
| `/issue_done F<id>` or `/issue_done S<id>` | Mark an issue as fixed/reviewed |
| `/issue_ignore F<id>` or `/issue_ignore S<id>` | Ignore a false positive issue |
| `/issues_export` | Export open issues as JSON for regression tests |
| `/ai_suggestions` | List OpenAI suggestions waiting for owner review |
| `/ai_suggestion <id>` | Show one OpenAI suggestion with gate details |
| `/ai_accept <id>` | Accept a reviewed OpenAI suggestion for regression export |
| `/ai_ignore <id>` | Ignore an unsafe or unhelpful OpenAI suggestion |
| `/ai_suggestions_export` | Export accepted OpenAI suggestions as matcher fixtures |
| `/cancel` | Clear pending result buttons |

Generate a draft matcher regression test from exported issues or accepted AI
suggestions:

```bash
python scripts/generate_issue_fixtures.py issues.json > /tmp/test_exported_issues.py
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

The bot sends a result summary first. Press "Xem kết quả" to receive the first result batch, then use "Xem thêm" for the next batches.

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
playwright
crawl4ai
beautifulsoup4
lxml
python-dotenv
pytest
```

`ENABLE_CRAWL4AI` remains in config as a compatibility flag. Current production search uses WatchFacts JSON responses and HTML parsing.

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
