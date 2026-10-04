# WatchFacts Telegram Runtime

Self-hosted WatchFacts search runtime with a Telegram interface, generated
result pages, and deterministic matching.

## What It Does

- Receives WatchFacts searches through the Telegram bot.
- Reuses an authorized Playwright browser session for WatchFacts access.
- Parses, matches, scores, and deduplicates listings deterministically.
- Preserves result identity, image, source, seller, date, and pagination data.
- Generates browser result pages served by a small HTTP service.
- Supports feedback, suspicious-result review, quality audits, and benchmarks.
- Optionally applies tightly guarded OpenAI refinement to hard cases; core
  search does not require an LLM.

The former MCP transport has been retired. Search behavior belongs to the
shared Python runtime and must not be reimplemented in prompts or UI code.

## Stack

- Python 3.11+
- python-telegram-bot
- HTTPX and Playwright
- BeautifulSoup4 and lxml
- SQLite
- Starlette and Uvicorn for result pages
- Docker Compose

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
make init
```

Configure `.env`, then create the authorized browser state:

```bash
make login
```

Run the bot locally:

```bash
make run
```

Deploy both production services:

```bash
make deploy
```

Scoped deploys:

```bash
make deploy-bot
make deploy-web
```

## Architecture

- `app/runtime/telegram_bot.py`: Telegram interface and owner workflows.
- `app/runtime/web_server.py`: result-page HTTP routes and server-side actions.
- `app/application/`: shared search, issue, and result-reference use cases.
- `app/searching/`: deterministic parsing, matching, scoring, dedupe, and identity.
- `app/integrations/`: WatchFacts HTTP/session and optional AI adapters.
- `app/results/`: result-page generation, storage, sanitization, and rendering.
- `scripts/diagnostics/`: direct-runtime smoke, audit, prewarm, and benchmark tools.

Production services:

- `watchfacts-bot`: primary user-facing Telegram runtime.
- `watchfacts-web`: localhost-only HTTP service for `/results/*`.

## Common Commands

| Command | Purpose |
| --- | --- |
| `make check` | Run tests, compile checks, and Compose validation |
| `make quality-audit` | Run the bounded search-quality audit |
| `make runtime-smoke` | Validate representative direct-runtime searches |
| `make search-benchmark` | Benchmark representative queries |
| `make search-cold-budget` | Benchmark cold-path queries |
| `make search-prewarm` | Warm common search-cache entries |
| `make deploy` | Deploy bot and web services |
| `make logs` | Follow bot logs |
| `make web-logs` | Follow result web-service logs |

See [docs/README.md](docs/README.md) for the documentation index and
[docs/operations.md](docs/operations.md) for production procedures.

## Security

Use this project only with authorized WatchFacts access. Never bypass login,
captcha, Cloudflare, or anti-bot controls. Never commit `.env`, browser state,
cookies, database files, API keys, logs, or generated result pages.

## License

Private project. Add a license before public distribution.
