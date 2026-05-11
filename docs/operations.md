# Operations Guide

## Local Setup

```bash
make init
```

This creates:

- `data/`
- `logs/`
- `.env` copied from `.env.example` when `.env` is missing

Edit `.env` with real operator values. Do not commit `.env`.

## Docker Build

```bash
make build
```

The image installs:

- Python 3.11 runtime
- Python dependencies from `requirements.txt`
- Playwright Chromium and browser dependencies

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

Status:

```bash
make ps
```

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
tar -czf watchfacts-data-backup.tgz data
```

Restore:

```bash
tar -xzf watchfacts-data-backup.tgz
```

Treat backups as sensitive if they contain browser state.

## First Server Deploy

1. Clone repository.
2. Run `make init`.
3. Edit `.env`.
4. Create `data/watchfacts_state.json` with `python scripts/login.py`.
5. Run `make build`.
6. Run `make up`.
7. Inspect `make logs`.

## Current Limitation

The Docker entrypoint is:

```bash
python -m app.main
```

Until `app/main.py` exists, `make build` can pass but `make up` cannot run the bot successfully.
