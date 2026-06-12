# AGENTS.md

Project-level instructions for AI coding agents working on `watchfacts`.

## Project Summary

`watchfacts` is a Python WatchFacts Telegram search runtime with an MCP bridge
and OpenWA handoff support.

Expected behavior:

- Receive a WatchFacts query from the Telegram bot primary flow, or from an MCP client.
- Crawl the WatchFacts trading page using an authenticated Playwright session.
- Extract listing data with deterministic parsing.
- Match listings by query tokens and regex-assisted rules.
- Deduplicate results.
- Return structured ranked listing details, including `result_id`, image, listing text, seller, posted date, source, and pagination metadata.
- Let MCP clients create OpenWA chat drafts from selected `result_id` handles.

Core constraint: this project does not require an LLM for core WatchFacts search.
Matching and extraction logic should remain deterministic unless the user
explicitly changes that direction. MCP clients must call the MCP runtime instead
of reimplementing WatchFacts search in prompts.

## Local Skills

This workspace includes project-local agent skills in `./.skills`.

Before starting non-trivial work:

1. Inspect `./.skills/using-agent-skills/SKILL.md`.
2. Pick the smallest applicable skill set for the task.
3. Follow each selected skill's workflow, including its verification step.

Common skill choices:

| Task | Skill |
| --- | --- |
| Clarify vague requirements | `idea-refine` |
| Define feature behavior | `spec-driven-development` |
| Break work into steps | `planning-and-task-breakdown` |
| Implement code incrementally | `incremental-implementation` |
| Work with tests | `test-driven-development` |
| Debug failures | `debugging-and-error-recovery` |
| Review changes | `code-review-and-quality` |
| Security-sensitive changes | `security-and-hardening` |
| Performance work | `performance-optimization` |
| Browser/runtime verification | `browser-testing-with-devtools` |
| Git commits, branches, versioning | `git-workflow-and-versioning` |
| Documentation and ADRs | `documentation-and-adrs` |
| Context/rules updates | `context-engineering` |

Do not treat skills as generic reading material. Use them as task workflows.

## Expected Project Layout

The README describes this intended layout:

```text
app/
  main.py
  telegram_bot.py
  mcp_server.py
  tool_runtime.py
  search_result.py
  scraper.py
  parser.py
  matcher.py
  matcher_normalization.py
  matcher_token_classification.py
  matcher_rules.py
  matcher_rulebook.py
  result_scoring.py
  similarity.py
  issues.py
  openwa_handoff.py
  ai_refiner.py
  match_debug.py
  dedupe.py
  db.py
  config.py
scripts/
  ops/
    login.py
  diagnostics/
    audit_quality.py
    benchmark_hard_cases.py
    debug_match.py
  fixtures/
    generate_audit_fixtures.py
    generate_issue_fixtures.py
data/
  bot.db
  watchfacts_state.json
docs/
logs/
Dockerfile
docker-compose.yml
docker-compose.watchfacts-mcp.yml
Makefile
requirements.txt
.env.example
README.md
SOUL.md
```

The actual repository may be incomplete. Always inspect the current filesystem before assuming a file exists.

## Commands

Use these commands when the matching files exist:

| Purpose | Command |
| --- | --- |
| Create virtualenv | `python3 -m venv .venv` |
| Activate virtualenv | `source .venv/bin/activate` |
| Install dependencies | `pip install -r requirements.txt` |
| Install Playwright browser | `playwright install chromium` |
| Create WatchFacts session | `python scripts/ops/login.py` |
| Run Telegram bot locally | `python -m app.main` |
| Initialize local runtime files | `make init` |
| Build Docker image | `make build` |
| Deploy MCP only | `make deploy-mcp` |
| Deploy watchfacts-bot only | `make deploy-bot` |
| Deploy watchfacts-bot and watchfacts-mcp | `make deploy` |
| Start Telegram bot Docker service | `make up` |
| Stop Docker services | `make down` |
| Follow MCP logs | `make mcp-logs` |
| Follow Telegram bot logs | `make logs` |
| Open container shell | `make shell` |
| Run repository checks | `make check` |
| Run authorized HTTPX WatchFacts smoke search | `make mcp-smoke` |

If tests or lint commands are added later, update this file and prefer those commands for verification.

The Telegram bot Docker entrypoint is `python -m app.main`. The MCP service
entrypoint is `python -m app.mcp_server`.

## Project Documentation

Use `docs/README.md` as the documentation index.

Load docs selectively:

- Project context: `SOUL.md`.
- New features: `docs/product-spec.md`, `docs/technical-spec.md`, and `docs/implementation-plan.md`.
- MCP changes: `SOUL.md`, `docs/technical-spec.md`, `docs/operations.md`, and `docs/security-compliance.md`.
- Crawler/auth changes: `docs/technical-spec.md`, `docs/security-compliance.md`, and `docs/decisions/002-authenticated-browser-session.md`.
- Matching/parser/dedupe changes: `docs/technical-spec.md` and `docs/decisions/001-deterministic-matching.md`.
- Docker/runtime changes: `docs/operations.md` and `docs/decisions/004-docker-compose-runtime.md`.
- Roadmap or task planning: `docs/roadmap.md` and `docs/implementation-plan.md`.

## Environment And Secrets

Expected `.env` keys:

```env
TELEGRAM_BOT_TOKEN=your_telegram_token
WATCHFACTS_URL=https://watchfacts.com/simon-match-making
HEADLESS=true
ENABLE_CRAWL4AI=true
ENABLE_OPENWA_CHAT_HANDOFF=false
OPENWA_BASE_URL=
OPENWA_API_KEY=
```

Rules:

- Never commit `.env`.
- Never commit real Telegram tokens, WatchFacts credentials, cookies, browser state, or session files.
- Never commit OpenWA API keys, OpenAI API keys, or MCP prefill files containing secrets.
- Treat `data/watchfacts_state.json` as sensitive because it contains authenticated browser state.
- Treat `data/bot.db` as local runtime data.
- Keep `logs/`, `.venv/`, `__pycache__/`, and generated runtime files out of commits.

## Compliance Boundaries

This bot must only be used with authorized WatchFacts access and a valid WatchFacts account.

Do not add code that:

- Bypasses login.
- Bypasses captcha.
- Bypasses Cloudflare.
- Bypasses anti-bot systems.
- Stores WatchFacts passwords in source code or config examples.

If a requested change appears to weaken these boundaries, stop and surface the concern before implementing.

## Engineering Rules

- Read relevant files before editing them.
- Prefer the existing project style over introducing new abstractions.
- Keep matching, parsing, dedupe, and crawling concerns separate.
- Keep changes narrowly scoped to the user request.
- Do not add a new dependency unless it is clearly justified and documented.
- Prefer deterministic parsing and matching over LLM-based extraction.
- Use structured parsers where possible; avoid brittle ad hoc string manipulation for HTML.
- For Playwright code, use explicit waits and stable selectors where available.
- For SQLite, use parameterized queries and keep schema changes documented.
- For Telegram handlers, avoid blocking calls in async paths.
- For MCP tools, keep tool names/schema stable and return structured payloads rather than human-only text.
- For MCP client answers, preserve `result_id`, use `offset` / `next_offset` for pagination, and never invent seller contacts, source links, images, prices, or OpenWA links.
- Preserve clear error handling around network, login/session, parsing, and Telegram API failures.

## Verification Expectations

Before finishing work, run the strongest relevant verification available in the current repo.

Preferred checks, when available:

```bash
python -m compileall app scripts
python -m pytest
docker compose build
```

For documentation-only changes:

```bash
git diff --check
```

For crawler or browser-session changes, also verify that the login/session assumptions are still valid. Do not run actions that require real credentials unless the user explicitly asks and the local environment is configured.

If a command cannot run because required files or dependencies are missing, report that clearly instead of pretending verification passed.

## Git Workflow

When asked to commit or push:

1. Use `./.skills/git-workflow-and-versioning/SKILL.md`.
2. Check `git status --short --branch`.
3. Stage only files relevant to the requested change.
4. Inspect `git diff --staged`.
5. Check for secrets in staged changes.
6. Run relevant verification.
7. Commit with an English conventional-style message.

Keep commits atomic. Do not commit `.skills/` unless the user explicitly asks to version local skills.

## PMO Continuity

PMO continuity for this workspace is project-scoped as `watchfacts`.

Use PMO only for explicit capture and continuity metadata. Do not auto-ingest source code into PMO memory.

Capture durable notes only when the user explicitly asks or when a handoff/checkpoint is clearly requested.

## When Context Conflicts

If README, source code, skills, or user instructions conflict:

1. The newest explicit user instruction wins.
2. Security and compliance boundaries still apply.
3. Existing source code patterns win over README guesses for implementation details.
4. Surface the conflict and ask when choosing silently would be risky.

Do not hide uncertainty. Name the assumption, explain the tradeoff, and proceed only when the risk is acceptable.
