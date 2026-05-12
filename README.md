# WatchFacts Telegram Bot

Telegram bot for searching watch listings from WatchFacts using an authenticated browser session.

The bot receives a watch query from Telegram, searches WatchFacts with an authenticated browser session, removes duplicate reposts, and returns formatted results with product image, listing information, seller, and posted date.

## Features

- Telegram bot integration
- WatchFacts authenticated search
- Playwright browser automation
- Optional Crawl4AI extraction layer
- BeautifulSoup + lxml HTML parsing
- Regex and token-based matching
- Duplicate listing filtering
- Telegram result pagination with "Xem thêm"
- SQLite local cache
- Docker deployment
- Fully async architecture
- No LLM required
- Free and self-hosted

## Stack

- Python
- python-telegram-bot
- Playwright
- Crawl4AI
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
git clone https://github.com/yourusername/watchfacts-bot.git
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
| `make deploy` | Pull latest code, build, recreate the bot, and show startup logs |
| `make deploy SKIP_PULL=1` | Deploy local unpushed changes |
| `make build` | Build the Docker image |
| `make up` | Start the bot with Docker Compose |
| `make down` | Stop Docker Compose services |
| `make logs` | Follow bot logs |
| `make shell` | Open a shell in the bot container |
| `make check` | Run lightweight repository checks |
| `python scripts/login.py` | Open Chromium for manual WatchFacts login and save browser state |
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
│   ├── matcher.py
│   ├── dedupe.py
│   ├── db.py
│   ├── config.py
│   └── utils.py
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

Bot commands:

| Command | Purpose |
| --- | --- |
| `/start` | Open the visual intro and examples |
| `/help` | Show search flow, examples, and pagination actions |
| `/settings` | Show safe runtime settings without secrets |
| `/cancel` | Clear pending result buttons |

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

## Matching Logic

The bot uses deterministic matching. No AI or LLM is used.

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

## Crawl4AI

Crawl4AI is optional. It can provide cleaner markdown extraction, easier debugging, and a fallback extraction layer.

Playwright remains the primary crawler.

## Database

SQLite database location:

```text
data/bot.db
```

Used for:

- Cache
- Dedupe
- Query history

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

Expected `requirements.txt`:

```text
python-telegram-bot[job-queue]
playwright
crawl4ai
beautifulsoup4
lxml
python-dotenv
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
- Telegram inline buttons
- Image caching
- Export results
- Multiple watch sources

## License

MIT License
