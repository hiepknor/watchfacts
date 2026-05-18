# Operations Guide

## First Server Deploy

1. Clone the repository.
2. Run `make init`.
3. Edit `.env` with the real Telegram bot token and WatchFacts URL if needed.
   Set `TELEGRAM_ALLOWED_USER_IDS` to the owner Telegram user id if the bot
   should be private. Leave it empty to allow everyone.
4. Create browser state with `python scripts/ops/login.py`.
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

`make deploy` runs `git pull --ff-only`, builds the Docker image, runs pytest
and compile checks inside the Compose image, force-recreates the bot container,
prints Compose status, and shows recent startup logs.

If you are deploying local unpushed changes, use:

```bash
make deploy SKIP_PULL=1
```

Status:

```bash
make ps
```

Docker Compose also configures a lightweight container healthcheck and rotates
JSON logs with `max-size=10m` and `max-file=5`.

## OpenAI Controlled Intelligence

The bot remains deterministic by default. OpenAI-assisted refinement is an
optional controlled layer for suspicious, reported, or hard-to-scope results.
Normal search must continue to work when OpenAI is disabled,
unavailable, slow, or uncertain.

`.env` values:

```bash
HYBRID_AI_MODE=off
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
OPENAI_TIMEOUT_SECONDS=12
OPENAI_MAX_REFINES=3
```

Mode guidance:

- `off`: deterministic-only production behavior.
- `shadow`: call OpenAI for eligible snippets and record suggestions without changing Telegram output.
- `review`: show OpenAI suggestions only in owner review flows.
- `guarded`: call OpenAI only for eligible hard snippets or allowlisted suspicious reasons, then apply suggestions only after strict local validation gates pass.

Owner review commands:

- `/ai_suggestions`: list OpenAI suggestions with `review_status=open`.
- `/ai_suggestion <id>`: inspect deterministic text, AI suggestion, raw snippet, gate status, and linked issue.
- `/ai_accept <id>`: mark a suggestion as reviewed and ready for regression export.
- `/ai_ignore <id>`: ignore an unsafe or unhelpful suggestion.
- `/ai_suggestions_export`: export accepted suggestions as JSON for `scripts/fixtures/generate_issue_fixtures.py`.

Operational rules:

- Store `OPENAI_API_KEY` only in `.env` or the deployment secret store.
- Do not paste the key into Telegram, logs, issue exports, prompts, or docs.
- Rotate the key if it appears in logs or chat history.
- Keep OpenAI timeouts short enough that Telegram users still receive deterministic fallback promptly.
- Track suggestion accept/reject counts before considering `guarded`.
- In `guarded`, suggestions must be copied from the raw listing text and pass query, substring, separator, length, confidence, and risk checks before they can change user-facing output.
- Accepted AI suggestions should become deterministic matcher tests before the matching rules are changed for that pattern.

The older local model experiment has been removed from the supported runtime.
Do not add new production behavior that depends on local model files.

## Production Quality Audit

Use the production quality audit loop whenever matcher, extraction, scoring, or
quality-gate behavior changes.

Reference spec:

- [Production Quality Audit Loop Spec](production-quality-audit.md)

Default audit query set:

```text
5205r 2026
126500ln white 2026
7118/1200a grey
Fpj Elegante Titanium
228235a choco
5712r
5205r green
5726/1a
RM65-01 Lebron
116500 panda
```

Run the default set locally or inside the production container:

```bash
python scripts/diagnostics/audit_quality.py --limit 5
docker compose exec -T bot python scripts/diagnostics/audit_quality.py --limit 5
```

Run focused queries:

```bash
python scripts/diagnostics/audit_quality.py "5712r" "RM65-01 Lebron" --limit 10
```

Write machine-readable output for handoff or later fixture work:

```bash
python scripts/diagnostics/audit_quality.py --format json --limit 5 > audit-report.json
```

Generate draft quality/scoring regression tests from audit JSON:

```bash
python scripts/fixtures/generate_audit_fixtures.py audit-report.json > tests/test_audit_regressions.py
```

The audit fixture generator is for quality group, suspicious-result, missing
price, and ranking evidence. For extraction bugs that need full raw listing
text, export issues from Telegram and use:

```bash
python scripts/fixtures/generate_issue_fixtures.py issues-export.json > tests/test_issue_regressions.py
```

Checklist:

- Run the audit query set before changing broad rules when production behavior
  is in doubt.
- Convert confirmed issues into regression tests before implementing fixes.
- Run focused tests, then the full suite.
- Bump `SEARCH_CACHE_VERSION` when scoring or quality gates can change cached
  output.
- Deploy with `make deploy`.
- Verify the container is healthy and the production git HEAD matches the
  deployed commit.
- Rerun the focused production audit after deploy.
- Capture unresolved findings to PMO or docs before ending the work.

Audit reports must not print `.env`, API keys, Telegram tokens, WatchFacts
cookies, browser state, full page HTML, or unbounded raw listings.

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
python scripts/ops/login.py
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
- Recreate `data/watchfacts_state.json` with `python scripts/ops/login.py` if the restored session is expired.
- `data/bot.db` is the SQLite query history/cache.
