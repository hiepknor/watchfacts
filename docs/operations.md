# Operations Guide

## First Server Deploy

1. Clone the repository.
2. Run `make init`.
3. Edit `.env` with WatchFacts, OpenWA, OpenAI, and optional Telegram values.
   `TELEGRAM_BOT_TOKEN` is required when running the primary Telegram bot.
4. Create browser state with `python scripts/ops/login.py`.
5. For MCP production, configure the trusted MCP client to call
   `http://watchfacts-mcp:8765/mcp` from the Docker network or
   `http://127.0.0.1:8765/mcp` from the host.
6. To enable generated result pages, set `RESULT_PAGE_PUBLIC_BASE_URL` to the
   public `/results` base URL and expose only that public route; keep `/mcp`
   reachable only by trusted internal clients.
   - Example for dedicated subdomain: `https://watchfacts.onio.cc/results`.
  - Reverse proxy should route only `/results/*` and keep `/mcp` private.
  - Log the `/results/*` route separately when the Caddy host supports it (for
    example `/var/log/caddy/watchfacts-results.log`).
  - Temporarily apply rate limiting at the app layer
    (`app/runtime/mcp_server.py`, public import path `app.mcp_server`) for
    result-page requests: max 60 requests/60 seconds/IP, block for 120 seconds
    when threshold is exceeded.
  - Keep the post-subdomain upgrade plan in place:
    - add a dedicated proxy health route (for example `/results/health`) outside
      the app
    - consolidate Caddy + app logs by `request_id` for faster troubleshooting
    - monitor `429/404/410` result-page responses by IP
  - If using a dedicated subdomain, validate quickly:
    - `curl -I https://watchfacts.onio.cc/results/health` should return 200
    - `curl https://watchfacts.onio.cc/mcp` should still be 404

7. Run `make deploy` for the standard `watchfacts-bot` + `watchfacts-mcp`
   deploy. Use `make deploy-mcp` for MCP only or `make deploy-bot` for bot only.
8. Inspect startup with `make mcp-logs` or `make logs` if needed.

The bot expects `data/watchfacts_state.json` to exist before the first real search.
Deploy targets also check for `.env` and browser state before they pull/build.

## Production Server Standard

The production server should keep `/opt/watchfacts` as a clean git checkout
tracking `origin/master`. Deploy should run as user `ubuntu`, not `sudo`, so
git-owned files do not become root-owned.

Standard deploy (recommended):

```bash
cd /opt/watchfacts
make deploy
```

This target:

- runs `git pull --ff-only`
- uses the shared Docker image `watchfacts:local`
- builds and force-recreates `watchfacts-bot` and `watchfacts-mcp`
- removes the legacy pre-rename bot container `watchfacts` before starting
  `watchfacts-bot`
- runs pytest, compile checks, and the default bounded quality audit inside the
  MCP Compose service
- prints Compose status and recent startup logs for each service

Use `make deploy-mcp` for `watchfacts-mcp` changes and `make deploy-bot` for
`watchfacts-bot` changes:

```bash
make deploy-mcp
make deploy-bot
```

Do not use `SKIP_PULL=1` for normal production deploys. If the server working
tree is dirty, fix the deploy checkout first rather than layering rsync changes
over it.

Runtime naming:

- Docker image: `watchfacts:local`
- Telegram service/container: `watchfacts-bot`
- MCP service/container: `watchfacts-mcp`
- Legacy container removed during bot deploy: `watchfacts`

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
- `TELEGRAM_RESULT_LIMIT=5` controls how many results each Telegram fallback
  button click sends when generated result pages are unavailable.
- `TELEGRAM_MAX_CONCURRENT_SEARCHES=1` serializes WatchFacts searches and shows
  a queue notice for extra concurrent queries.
- `SEARCH_CACHE_TTL_SECONDS=1800` serves repeated identical normalized searches
  from SQLite before calling WatchFacts again.
- `SEARCH_MAX_CONCURRENT_SEARCHES=1` serializes non-Telegram WatchFacts searches,
  including MCP requests, while identical queries still coalesce.
- Use `make mcp-prewarm` after deploy or from a light cron to warm common
  production query cache entries. Add `MCP_PREWARM_FORMAT=jsonl` when the output
  should be archived as an ops artifact.
- Existing production `.env` files are not overwritten by `.env.example`.
  After changing cache policy, run `make mcp-runtime-config` on the server and
  verify `search_cache_ttl_seconds=1800`.

Primary Telegram behavior:

- The bot sends a summary first, not the full result list.
- When `RESULT_PAGE_PUBLIC_BASE_URL` is configured and page generation
  succeeds, the summary includes only a `Mở trang kết quả` button. The generated
  result page is the primary listing UI.
- The older "Xem kết quả" and "Xem thêm" buttons are fallback-only behavior when
  result-page generation is disabled or fails.
- When another query is already running, the bot tells the user their query is
  waiting and then runs it automatically when a search slot is free.
- Photo captions are limited to Telegram's caption size; long text fallback messages are also truncated safely.
- In group chats, normal messages are ignored unless the bot is mentioned at the beginning or the user replies to a bot message.

MCP client behavior:

- Initial search should call `search(query=<full user text>, limit=5, offset=0, include_similar=true)`.
- "Load more" should call `search` again with the same query and previous
  `next_offset`.
- Use the returned `result_id` for immediate `create_chat_draft` and issue
  reporting. Use the returned `stable_listing_id` when the follow-up may cross a
  restart or when the client preserved that field instead. If the user says
  "result 20", pass `rank=20`.
- Use `image_url` as the product image when present.
- Do not invent seller contact, result ids, source links, prices, image links, or OpenWA links.

Issue queues:

- Production issue review should go through MCP tools first, not SSH or direct
  database inspection.
- Use `list_issues(issue_type="all", status="open", limit=20)` to see the
  active queue. Use `status="fixed"`, `status="ignored"`, or `status="all"`
  for review history.
- Use `get_issue(issue_ref="F15")` or `get_issue(issue_ref="S15")` to inspect
  one issue. The MCP payload may include bounded `raw_context`, but it must not
  expose `.env`, browser state, cookies, full HTML, or secrets.
- Use `suspicious_summary()` to group the auto-QA backlog by reason and
  severity. Convert confirmed patterns into regression tests before changing
  matcher rules.
- Use `update_issue(issue_ref, status, notes)` only after triage. Mark `fixed`
  after the fix is tested, deployed, and verified. Mark `ignored` only with a
  note explaining why no code change is needed.

Example MCP-client operator prompts:

```text
List open WatchFacts issues.
View issue F15.
Classify this issue: bad_extraction, wrong_reference, wrong_descriptor, bad_rank,
missing_price, stale_cache, or source_lacks_info?
Propose a regression test for this issue, no code change yet.
Mark issue F15 fixed with notes: commit <sha>, deploy date <date>, audit query passed.
Mark issue S8 ignored with notes: raw source has no additional price data.
```

MCP clients should call the tools above. They should not inspect `data/bot.db`
directly and must not reimplement WatchFacts matching in prompts.

OpenWA chat handoff:

- For MCP production, set OpenWA values in `.env` and deploy with `make deploy`.
- Use `OPENWA_BASE_URL=http://openwa-api:2785` for server-to-server API calls
  and the public dashboard URL, for example `https://openwa.onio.cc`, for
  `OPENWA_DASHBOARD_URL`.
- Set `ENABLE_OPENWA_CHAT_HANDOFF=true`, `OPENWA_API_KEY` to an OpenWA operator
  key, `OPENWA_CHAT_DRAFT_ENDPOINT=/api/chats/drafts`, and
  `OPENWA_DOCKER_NETWORK=openwa-network`.
- The Telegram bot can use `make deploy-bot OPENWA_COMPOSE=1` if it
  needs to join the separate OpenWA compose network.

Result page modal actions:

- Result page action development is specified in
  `docs/result-page-actions-plan.md` and ADR-007.
- Generated result pages can create OpenWA drafts and record feedback issues
  from the detail modal.
- Browser requests must call same-origin routes under `/results/{token}/actions/`
  and must include the page-scoped `action_nonce`.
- OpenWA draft creation still uses server-side `.env` values:
  `ENABLE_OPENWA_CHAT_HANDOFF`, `OPENWA_BASE_URL`, `OPENWA_API_KEY`,
  `OPENWA_DASHBOARD_URL`, and `OPENWA_CHAT_DRAFT_ENDPOINT`.
- Report issue works even when OpenWA is disabled because it records feedback in
  the WatchFacts SQLite issue queue.
- Older result pages without action URLs or nonce fall back to copy-helper
  controls until they expire.
- To smoke test after deploy:
  1. Run a WatchFacts search that generates a result page.
  2. Open the result page and open one result's More detail modal.
  3. Submit a report issue and verify it appears through `list_issues` or
     `get_issue`.
  4. If OpenWA is enabled, create a draft and verify the returned dashboard link.
  5. Check browser console for CSP/connect-src errors.
- Rollback options:
  - Disable OpenWA handoff with `ENABLE_OPENWA_CHAT_HANDOFF=false` if draft
    creation fails.
  - Revert the result-page modal action UI commit to fall back to copy utilities
    if the action UI fails.
  - Disable result pages with `RESULT_PAGE_PUBLIC_BASE_URL=` if page generation
    or sidecar storage is unsafe.

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

Restart `watchfacts-bot` after updating code:

```bash
make deploy-bot
```

Restart WatchFacts MCP after updating MCP code or schema:

```bash
make deploy-mcp
```

`make deploy-bot` runs `git pull --ff-only`, builds the Docker image, runs
pytest and compile checks inside the Compose image, force-recreates the
`watchfacts-bot` container, prints Compose status, and shows recent startup
logs.

`make deploy-mcp` does the same for `watchfacts-mcp`, runs the bounded quality
audit gate, force-recreates the MCP container, waits for health, then prewarms
representative MCP search queries on a best-effort basis before showing recent
MCP logs. The prewarm step covers both hard quality cases and benchmark/common
brand queries, reducing first-query latency by populating the shared SQLite
search cache used by both MCP and the Telegram bot. Set
`MCP_POSTDEPLOY_PREWARM=0` to skip all warmup, or
`MCP_POSTDEPLOY_PREWARM_BENCHMARK_DEFAULTS=0` to skip the extra benchmark/common
brand warmup pass. Prewarm verifies the hot-cache path by default; set
`MCP_PREWARM_VERIFY_HOT=0` to skip the second verification pass.

`make deploy` deploys both `watchfacts-bot` and `watchfacts-mcp`.

Status:

```bash
make ps
make mcp-ps
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

Offline audit triage:

```bash
python scripts/diagnostics/audit_quality.py "5205r green" --format jsonl --limit 10 > audit-report.jsonl
python scripts/diagnostics/ai_audit_triage.py audit-report.jsonl
```

The triage command always emits a deterministic artifact summary. Add
`--use-openai` only when you want OpenAI to classify recurring issue patterns
from bounded/redacted audit evidence:

```bash
python scripts/diagnostics/ai_audit_triage.py audit-report.jsonl --use-openai
make ai-audit-triage AI_AUDIT_ARTIFACT=audit-report.jsonl AI_AUDIT_TRIAGE_OPENAI=1
```

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
make quality-audit
python scripts/diagnostics/audit_quality.py --limit 5
docker compose exec -T watchfacts-bot python scripts/diagnostics/audit_quality.py --limit 5
```

Run focused queries:

```bash
python scripts/diagnostics/audit_quality.py "5712r" "RM65-01 Lebron" --limit 10
```

Run a pasteable MCP latency and surface-quality benchmark against the running
MCP service:

```bash
make mcp-benchmark
MCP_BENCHMARK_FORMAT=jsonl make mcp-benchmark > mcp-benchmark.jsonl
make mcp-runtime-config
make mcp-prewarm
MCP_PREWARM_FORMAT=jsonl make mcp-prewarm > mcp-prewarm.jsonl
docker compose -f docker-compose.yml -f docker-compose.watchfacts-mcp.yml exec -T watchfacts-mcp \
  python scripts/diagnostics/benchmark_mcp_queries.py \
  --query "rm07-01 rg" --query "rm07-01 rose gold" \
  --query "126500ln white" --query "daytona panda" \
  --format markdown --allow-empty
docker compose -f docker-compose.yml -f docker-compose.watchfacts-mcp.yml exec -T watchfacts-mcp \
  python scripts/diagnostics/benchmark_mcp_queries.py \
  --query "rm07-01 rg snow" --query "rm07-01 rose gold snow" \
  --repeat 2 --format markdown --allow-empty
```

Use the benchmark for pass/fail, latency, result counts, intent/cache
diagnostics, stage timing summaries, top-result snippets, canonical query,
brand/reference/collection/nickname recognition, descriptor metadata, and
retrieval expansion reason codes. The default benchmark set covers `rm07-01`
alias pairs plus representative Rolex, Patek Philippe, and Audemars Piguet
multi-brand queries. For default benchmark runs, the command also requires at
least one canonical alias group and fails when alias-equivalent `total_count`
values differ by more than 10%; tune this with `--alias-total-delta-ratio` or
use `--skip-alias-recall-check` for exploratory custom runs. Use `--repeat 2`
for a quick cold/warm cache comparison; run 1 should show the uncached fetch
path when the search cache is stale, while run 2 should normally show the
cache-hit path. Use `audit_quality.py` when a query needs quality-group,
scoring, image attribution, or raw-to-final funnel evidence.

## Search Engine Deploy Gate

Use this gate for changes to query recognition, retrieval planning, parsing,
matching, dedupe, ranking, result serialization, or cache-affecting diagnostics.

Predeploy gate:

```bash
make search-engine-predeploy-check
```

This runs repository whitespace checks, the full test suite, Python compile
checks, and the focused hard-case audit set:

```text
rm07-01 rg snow
rm07-01 rose gold
rm07-01 white gold
rm07-01 mother of pearl
```

When a search change affects a known query class, capture before/after evidence
with JSONL so the delta is reviewable:

```bash
python scripts/diagnostics/audit_quality.py \
  "rm07-01 rg snow" \
  "rm07-01 rose gold" \
  "rm07-01 white gold" \
  "rm07-01 mother of pearl" \
  --format jsonl --limit 5 > before-search-engine.jsonl

python scripts/diagnostics/audit_quality.py \
  "rm07-01 rg snow" \
  "rm07-01 rose gold" \
  "rm07-01 white gold" \
  "rm07-01 mother of pearl" \
  --format jsonl --limit 5 > after-search-engine.jsonl

python scripts/diagnostics/audit_quality.py \
  --compare-jsonl before-search-engine.jsonl after-search-engine.jsonl
```

If the change can alter cached output ordering, eligibility, extraction,
scoring, quality gates, serialized result shape, or search diagnostics consumed
by deploy gates, bump `SEARCH_CACHE_VERSION` in `app/searching/search.py`.
Deploy notes should state either the old/new cache version or why no bump was
needed.

Deploy and postdeploy verification:

```bash
make deploy-mcp
make search-engine-postdeploy-check
```

`make search-engine-postdeploy-check` runs `make mcp-smoke-set` and
`make mcp-benchmark`, so MCP search response shape, alias recall, cache status,
stage timings, and top-result snippets are checked against the running service.
For changes that affect Telegram presentation or bot-owned code paths, deploy
the bot after the MCP gate passes:

```bash
make deploy-bot
```

Do not treat unresolved code failures as bot deploy blockers. Code failures
should be fixed before deploy by the predeploy gate; after that, bot deploy
should only be blocked by runtime configuration issues such as missing
`TELEGRAM_BOT_TOKEN`, `.env`, browser state, or other operator-managed secrets.

Write machine-readable output for handoff or later fixture work:

```bash
python scripts/diagnostics/audit_quality.py --format json --limit 5 > audit-report.json
python scripts/diagnostics/audit_quality.py --format jsonl --limit 5 > audit-report.jsonl
python scripts/diagnostics/audit_quality.py --summarize-jsonl audit-report.jsonl
python scripts/diagnostics/audit_quality.py --compare-jsonl before.jsonl after.jsonl
```

Generate draft quality/scoring regression tests from audit JSON or JSONL:

```bash
python scripts/fixtures/generate_audit_fixtures.py audit-report.json > tests/test_audit_regressions.py
python scripts/fixtures/generate_audit_fixtures.py audit-report.jsonl > tests/test_audit_regressions.py
```

The audit fixture generator is for quality group, suspicious-result, missing
price, and ranking evidence. For extraction bugs that need full raw listing
text, export issues from Telegram and use:

```bash
python scripts/fixtures/generate_issue_fixtures.py issues-export.json > tests/test_issue_regressions.py
```

Checklist:

- Run `make search-engine-predeploy-check` before deploy when query
  recognition, retrieval, matcher, extraction, scoring, quality gates,
  diagnostics, or serialized result shape changes.
- Convert confirmed issues into regression tests before implementing fixes.
- Run focused tests, then the full suite.
- Bump `SEARCH_CACHE_VERSION` in `app/searching/search.py` when extraction, scoring,
  quality gates, ranking, or serialized result shape can change cached output.
- Deploy with `make deploy` for the standard production `watchfacts-bot` +
  `watchfacts-mcp` deploy. Use `make deploy-mcp` or `make deploy-bot` for
  scoped service deploys.
- Verify the container is healthy and the production git HEAD matches the
  deployed commit.
- Rerun the focused production audit after deploy and keep `make mcp-smoke-set`
  passing.
- Capture unresolved findings to PMO or docs before ending the work.

Audit reports must not print `.env`, API keys, Telegram tokens, WatchFacts
cookies, browser state, full page HTML, or unbounded raw listings.

## Production Access Policy

- Use MCP tools as the default production review surface for WatchFacts issues.
- Use `ubuntu@43.153.208.222` for deploy and operational commands only.
- Do not use `audit@43.153.208.222` as the primary review path.
- If emergency audit SSH is needed later, create a restricted read-only user
  that is not in the Docker group and cannot read `.env`,
  `data/watchfacts_state.json`, or other secrets.

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
