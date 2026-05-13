# Operations Guide

## First Server Deploy

1. Clone the repository.
2. Run `make init`.
3. Edit `.env` with the real Telegram bot token and WatchFacts URL if needed.
   Set `TELEGRAM_ALLOWED_USER_IDS` to the owner Telegram user id if the bot
   should be private. Leave it empty to allow everyone.
4. Create browser state with `python scripts/login.py`.
5. Run `make deploy`.
6. Inspect startup with `make logs` if needed.

The bot expects `data/watchfacts_state.json` to exist before the first real search.
`make deploy` also checks for `.env` and browser state before it pulls/builds.

## Local Setup

```bash
make init
```

This creates:

- `data/`
- `logs/`
- `.env` copied from `.env.example` when `.env` is missing

Edit `.env` with real operator values. Do not commit `.env`.

Access control:

- `TELEGRAM_ALLOWED_USER_IDS=` means all Telegram users may use the bot.
- `TELEGRAM_ALLOWED_USER_IDS=123456789` means only that Telegram user id may use it.
- Multiple owner ids can be comma-separated.
- `TELEGRAM_RESULT_LIMIT=5` controls how many results each Telegram button click sends.
- `TELEGRAM_MAX_CONCURRENT_SEARCHES=1` serializes WatchFacts searches and shows
  a queue notice for extra concurrent queries.
- `SEARCH_CACHE_TTL_SECONDS=300` serves repeated identical normalized searches
  from SQLite before calling WatchFacts again.

Telegram behavior:

- The bot sends a summary first, not the full result list.
- Use the "Xem kết quả" and "Xem thêm" buttons to send result batches.
- When another query is already running, the bot tells the user their query is
  waiting and then runs it automatically when a search slot is free.
- Photo captions are limited to Telegram's caption size; long text fallback messages are also truncated safely.
- In group chats, normal messages are ignored unless the bot is mentioned at the beginning or the user replies to a bot message.

## Docker Build

```bash
make build
```

The image installs:

- Python 3.11 runtime
- Python dependencies from `requirements.txt`
- Playwright Chromium and browser dependencies

Run the normal checks before building when code changed:

```bash
.venv/bin/python -m pytest -q
make check
```

## Start And Stop

Start:

```bash
make up
```

Stop:

```bash
make down
```

Restart:

```bash
make restart
```

Restart after updating code:

```bash
make deploy
```

`make deploy` runs `git pull --ff-only`, builds the Docker image, force-recreates
the bot container, prints Compose status, and shows recent startup logs.

If you are deploying local unpushed changes, use:

```bash
make deploy SKIP_PULL=1
```

Status:

```bash
make ps
```

## Local LLM Experiment

This branch includes an optional llama.cpp service for testing a local Gemma GGUF
model. The bot does not call the model by default; `LOCAL_LLM_ENABLED=false`
keeps production matching deterministic.

Place the GGUF file outside git, for example:

```bash
mkdir -p models
# put Gemma4 E2B GGUF here, matching LLAMA_CPP_MODEL_FILE in .env
```

Set or adjust these `.env` values:

```bash
LOCAL_LLM_ENABLED=true
LOCAL_LLM_BASE_URL=http://llama-cpp:8080
LOCAL_LLM_MODEL=gemma-4-e2b-Q4_K_M.gguf
LOCAL_LLM_TIMEOUT_SECONDS=8
LOCAL_LLM_MAX_REFINES=3
LLAMA_CPP_MODELS_DIR=./models
LLAMA_CPP_MODEL_FILE=gemma-4-e2b-Q4_K_M.gguf
```

Start only the local LLM service:

```bash
make llm-up
```

Run a smoke request against the OpenAI-compatible chat endpoint:

```bash
make llm-smoke
```

`make llm-smoke` runs from the host and uses `http://localhost:8080` unless
`LOCAL_LLM_SMOKE_BASE_URL` is set. The bot container should use
`LOCAL_LLM_BASE_URL=http://llama-cpp:8080`.

Inspect or stop it:

```bash
make llm-logs
make llm-down
```

The Compose service uses the official llama.cpp server image and mounts
`LLAMA_CPP_MODELS_DIR` read-only at `/models`. Do not commit model files.

## Logs

Follow logs:

```bash
make logs
```

Logs should never include:

- Telegram token
- WatchFacts credentials
- cookies
- local storage
- full browser storage state

Useful event names:

- `event=bot.starting`
- `event=query.start`
- `event=query.end`
- `event=query.error`
- `event=telegram.search_error`

Query logs include lengths and result counts, not the raw Telegram query text.

Telegram send failures should be rare because result text is capped before sending. If they appear, inspect the error type rather than pasting full listing text or secrets into logs.

## Browser Login State

The bot expects authenticated browser state at:

```text
data/watchfacts_state.json
```

Create it with:

```bash
python scripts/login.py
```

The login script should open Chromium and let the operator log in manually. The bot must not store WatchFacts passwords.

## Data Files

Runtime files:

| Path | Purpose | Git |
| --- | --- | --- |
| `.env` | Local secrets/config | ignored |
| `data/watchfacts_state.json` | Authenticated browser state | ignored |
| `data/bot.db` | SQLite cache/history | ignored |
| `logs/` | Runtime logs | ignored |

## Backup

Back up:

```bash
mkdir -p backups
tar -czf backups/watchfacts-data-$(date +%Y%m%d-%H%M%S).tgz data
```

Restore:

```bash
make down
tar -xzf watchfacts-data-backup.tgz
make up
```

Treat backups as sensitive if they contain browser state.

## Restore Notes

- Restore `.env` separately if needed; it is not part of the `data/` backup.
- Recreate `data/watchfacts_state.json` with `python scripts/login.py` if the restored session is expired.
- `data/bot.db` is the SQLite query history/cache.
