# WatchFacts Telegram Bot

Telegram bot for searching watch listings from WatchFacts using an authenticated browser session.

The bot receives a watch query from Telegram, crawls the WatchFacts trading page, extracts matching listings, removes duplicates, and returns formatted results with product image, listing information, seller, and posted date.

## Features

- Telegram bot integration
- WatchFacts authenticated crawling
- Playwright browser automation
- Optional Crawl4AI extraction layer
- BeautifulSoup + lxml HTML parsing
- Regex and token-based matching
- Duplicate listing filtering
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

Create an authenticated WatchFacts browser session:

```bash
python scripts/login.py
```

Run the bot locally:

```bash
python -m app.main
```

## Commands

| Command | Description |
| --- | --- |
| `make init` | Create `data/`, `logs/`, and `.env` from `.env.example` when missing |
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

Send a watch query to the bot:

```text
228253a choco
```

Example response:

```text
📸 Ảnh sản phẩm:
https://image-url.jpg

🏷️ Thông tin:
228253A choco N2 467000hkd

👤 Người đăng:
HK STOCKS

📅 Ngày đăng:
April 20, 2026
```

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

## Deduplication

Listings are deduplicated with:

```text
normalized_text + seller + posted_date
```

Normalization includes:

- Lowercasing
- Trimming spaces
- Collapsing repeated whitespace
- Normalizing punctuation

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
