# Operations Guide

## Runtime Topology

Production has two services built from the same image:

- `watchfacts-bot`: Telegram polling and search orchestration.
- `watchfacts-web`: generated result pages and server-side actions.

`watchfacts-web` listens on container port `8766` and is bound to the legacy-
compatible host address `127.0.0.1:8765`. Caddy may expose `/results/*`; no
general search API is exposed. The former MCP transport is retired.

Both services mount `./data` and `./logs`, so Telegram can write result-page
artifacts that the web service reads.

## Initial Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
make init
```

Edit `.env`. At minimum, configure a real `TELEGRAM_BOT_TOKEN` and review
`TELEGRAM_ALLOWED_USER_IDS`; an empty allowlist makes the bot public.

Create the authorized WatchFacts session on a machine with a browser:

```bash
make login
```

This creates `data/watchfacts_state.json`. Treat it as a credential and never
commit or paste it into logs.

## Local Runtime

```bash
make run
python -m app.web_server
```

The web readiness endpoint is:

```text
http://127.0.0.1:8766/healthz
```

Direct Python execution listens on `8766`; Docker publishes that container port
as legacy-compatible host port `8765`. Readiness loads runtime configuration,
initializes SQLite, and verifies that result-page storage is writable. It returns
`503` without exposing paths or credentials when those dependencies fail.

## Deployment

Standard deployment:

```bash
make deploy
```

Scoped deployment:

```bash
make deploy-bot
make deploy-web
```

Deploy targets verify environment files, pull with `--ff-only`, build the
image, run tests/compile checks, recreate the requested service, and show logs.
The web deploy also waits for health and prewarms search cache on a best-effort
basis.

## Service Operations

```bash
make up
make down
make restart
make logs
make ps

make web-up
make web-down
make web-restart
make web-logs
make web-ps
make web-wait-healthy
```

## Search Verification

Local repository gate:

```bash
make check
```

Authorized WatchFacts HTTP smoke:

```bash
make watchfacts-http-smoke
```

Direct shared-runtime checks inside `watchfacts-web`:

```bash
make runtime-smoke
make search-prewarm
make search-prewarm-benchmark-defaults
make search-benchmark
make search-cold-budget
make runtime-config
```

These commands call the Python search runtime directly. They do not depend on a
network tool protocol. `search-cold-budget` clears search cache rows before each
focused query; run a final hot benchmark afterward when collecting deployment
evidence.

Complete post-deploy gate:

```bash
make production-postdeploy-check
```

## Result Pages

Enable result pages with:

```env
RESULT_PAGE_PUBLIC_BASE_URL=https://watchfacts.onio.cc/results
RESULT_PAGE_TTL_SECONDS=86400
RESULT_PAGE_MAX_RESULTS=200
RESULT_PAGE_STORAGE_DIR=data/result_pages
```

Caddy must proxy only `/results/*` to `127.0.0.1:8765`. The generated page and
JSON sidecar share a random token and expire together. Generation writes both to
temporary files and atomically publishes HTML only after the sidecar is ready.

Result-page schema changes invalidate older stored HTML safely. An incompatible
page returns `410 Gone` and its matching HTML/JSON artifact pair is removed on
access.

Smoke test:

1. Send a Telegram search.
2. Open the generated result-page link.
3. Open a result detail modal.
4. Submit feedback and verify it via Telegram owner commands.

Rollback options:

- Set `RESULT_PAGE_PUBLIC_BASE_URL=` to fall back to Telegram result batches.
- Telegram fallback pagination retains at most 100 pending sessions for 30
  minutes; older sessions return the normal expired-result response.
- Roll Caddy back with `deploy/caddy/reload-caddy-safe.sh`.

## Owner Review

Issue and AI review remain Telegram owner workflows:

- `/issues`, `/issue F1`, `/issue_done F1`, `/issue_ignore F1`
- `/suspicious`, `/suspicious_summary`
- `/ai_suggestions`, `/ai_suggestion 1`, `/ai_accept 1`, `/ai_ignore 1`

Never review production issues by exposing SQLite over a public API.

## Backup And Restore

Back up:

- `.env` through a secret manager or encrypted operator backup;
- `data/watchfacts_state.json` securely;
- `data/bot.db`;
- optionally active `data/result_pages/` artifacts.

Stop services or use SQLite's backup mechanism before copying a live database.
After restore, validate file ownership, run `make check`, deploy, and execute the
post-deploy gate.

## Troubleshooting

- Missing/expired session: rerun `make login`, securely copy browser state, and
  redeploy.
- Web unhealthy: inspect `make web-logs`, storage permissions, host port 8765,
  container port 8766, and `/healthz`.
- No result-page link: verify `RESULT_PAGE_PUBLIC_BASE_URL` and writable
  `RESULT_PAGE_STORAGE_DIR`.
- Search regression: run `make quality-audit`, a focused direct benchmark, and
  convert confirmed evidence into a fixture before changing broad matcher rules.
