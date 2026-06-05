# Operations Guide

## First Server Deploy

1. Clone the repository.
2. Run `make init`.
3. Edit `.env` with WatchFacts, OpenWA, OpenAI, and optional Telegram values.
   `TELEGRAM_BOT_TOKEN` is only required when running the legacy Telegram bot.
4. Create browser state with `python scripts/ops/login.py`.
5. For Hermes/MCP production, configure Hermes to call
   `http://watchfacts-mcp:8765/mcp`.
6. Run `make deploy-hermes-mcp`.
7. Inspect startup with `make mcp-logs` or `make hermes-logs` if needed.

The bot expects `data/watchfacts_state.json` to exist before the first real search.
Deploy targets also check for `.env` and browser state before they pull/build.

## Production Server Standard

The production server should keep `/opt/watchfacts-bot` as a clean git checkout
tracking `origin/master`. Deploy should run as user `ubuntu`, not `sudo`, so
git-owned files do not become root-owned.

Standard deploy:

```bash
cd /opt/watchfacts-bot
make deploy-hermes-mcp
```

This target:

- runs `git pull --ff-only`
- builds `watchfacts-mcp`
- runs pytest and compile checks inside the MCP Compose service
- force-recreates `watchfacts-mcp`
- recreates Hermes so it reloads MCP config/schema

Do not use `SKIP_PULL=1` for normal production deploys. If the server working
tree is dirty, fix the deploy checkout first rather than layering rsync changes
over it.

Hermes config lives outside this repository, normally at:

```text
/opt/hermes-agent/data/config.yaml
/opt/hermes-agent/data/watchfacts_prefill.json
```

Expected Hermes MCP config shape:

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

Hermes/MCP behavior:

- Initial search should call `search(query=<full user text>, limit=5, offset=0, include_similar=true)`.
- "Xem thêm" should call `search` again with the same query and previous `next_offset`.
- Use `result_id` for `create_chat_draft` and issue reporting.
- Use `image_url` as the product image when present.
- Do not invent seller contact, result ids, source links, prices, image links, or OpenWA links.

Issue queues:

- `/issues` is the operator queue for user feedback. Treat these as production
  reports that need triage first.
- `/suspicious` is the QA queue for auto-detected extraction risks. It defaults
  to high-severity flags; use `/suspicious all` only when doing a broader audit.
- `/suspicious_summary` shows the auto-QA backlog grouped by reason and
  severity. Convert confirmed patterns into regression tests before changing
  matcher rules.
- Use `/issues_export` for user-reported regression fixtures and
  `/suspicious_export` for auto-QA fixture work.

OpenWA chat handoff:

- For Hermes/MCP production, set OpenWA values in `.env` and deploy with
  `make deploy-hermes-mcp`.
- Use `OPENWA_BASE_URL=http://openwa-api:2785` for server-to-server API calls
  and the public dashboard URL, for example `https://openwa.onio.cc`, for
  `OPENWA_DASHBOARD_URL`.
- Set `ENABLE_OPENWA_CHAT_HANDOFF=true`, `OPENWA_API_KEY` to an OpenWA operator
  key, `OPENWA_CHAT_DRAFT_ENDPOINT=/api/chats/drafts`, and
  `OPENWA_DOCKER_NETWORK=openwa-network`.
- The legacy Telegram bot can still use `make deploy OPENWA_COMPOSE=1` if it
  needs to join the separate OpenWA compose network.

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

Restart legacy Telegram bot after updating code:

```bash
make deploy
```

Restart WatchFacts MCP and reload Hermes after updating code or MCP schema:

```bash
make deploy-hermes-mcp
```

`make deploy-bot` runs `git pull --ff-only`, builds the Docker image, runs
pytest and compile checks inside the Compose image, force-recreates the legacy
bot container, prints Compose status, and shows recent startup logs.

`make deploy-hermes-mcp` does the same for `watchfacts-mcp`, then recreates
Hermes.

Status:

```bash
make ps
make mcp-ps
make hermes-ps
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
