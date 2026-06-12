# WatchFacts Runtime Architecture Redesign

Status: proposed implementation target

Date: 2026-06-12

## Objective

Define the next architecture target for WatchFacts without rewriting the system
in one large change. The goal is to make future search-quality, OpenWA, result
page, AI, and deployment work easier to evolve while preserving the current
production guarantees:

- `watchfacts-bot` remains the primary Telegram runtime.
- `watchfacts-mcp` remains a supporting structured integration surface.
- Core WatchFacts search stays deterministic, auditable, and shared.
- AI remains optional and controlled, not the primary matcher or ranker.
- WatchFacts access continues to use authorized saved browser state only.

Success means maintainers can change one concern, such as OpenWA handoff or AI
audit triage, without needing to understand Telegram handlers, MCP routes,
parser internals, and SQLite persistence at the same time.

## Current Shape

The current system already has useful separation:

```text
Telegram / MCP / diagnostics
  -> WatchFactsSearchWorkflow
  -> WatchFacts HTTP/session
  -> parser
  -> matcher
  -> dedupe
  -> scoring
  -> grouping
  -> SQLite cache/issues
  -> result pages / OpenWA / audit tooling
```

The risk is not that the current system is broken. The risk is that production
features now cross several modules:

- Telegram and MCP both call the same runtime but still own formatting and
  follow-up details.
- `Database` is used from multiple runtime boundaries.
- AI refinement, AI audit triage, and future AI handoff drafts do not yet share
  a clean provider boundary.
- Result-page actions, MCP follow-up tools, and Telegram workflows all depend on
  stable result identity and cache behavior.

## Target Architecture

Use a layered architecture with explicit adapters, application use-cases,
domain logic, and infrastructure.

```text
interfaces/
  telegram bot adapter
  mcp server adapter
  result page HTTP/action adapter
  diagnostics CLI adapters

application/
  search use-cases
  result page use-cases
  OpenWA handoff use-cases
  issue and feedback use-cases
  audit and AI triage use-cases

domain/
  query intent
  listing model
  parser contracts
  matcher rules
  scoring policy
  dedupe identity
  issue taxonomy
  audit event contracts

infrastructure/
  WatchFacts HTTP/session client
  SQLite repositories
  OpenWA API client
  OpenAI client
  result page storage
  deployment/runtime config
```

This is a migration target. The codebase does not need to move to these exact
package names immediately. Current packages can be aligned in stages:

| Target Layer | Current Modules |
| --- | --- |
| interfaces | `app/runtime/telegram_bot.py`, `app/runtime/mcp_server.py`, diagnostics scripts, result-page routes |
| application | `app/runtime/tool_runtime.py`, selected parts of `app/searching/search.py` |
| domain | `app/searching/*`, `app/searching/issues.py`, audit contracts |
| infrastructure | `app/integrations/*`, `app/db.py`, `app/results/result_pages.py`, config |

## Dependency Rules

The long-term direction is one-way dependency flow:

```text
interfaces -> application -> domain
interfaces -> application -> infrastructure
application -> domain
application -> infrastructure ports
infrastructure -> domain data types only when needed
domain -> no interfaces, no infrastructure
```

Rules:

- Domain modules must not import Telegram, MCP, OpenWA, OpenAI, Playwright,
  HTTPX clients, or SQLite.
- Interfaces must not reimplement search, matching, scoring, dedupe, or parser
  behavior.
- Application use-cases may coordinate domain and infrastructure, but should not
  format Telegram messages or MCP payloads directly.
- Infrastructure modules should own side effects and failure translation:
  WatchFacts HTTP/session, SQLite, OpenWA, OpenAI, and result-page storage.
- Public compatibility imports such as `app.search`, `app.matcher`,
  `app.tool_runtime`, `app.telegram_bot`, and `app.mcp_server` should remain
  until an explicit migration removes them.

## Proposed Use-Case Boundaries

### SearchUseCase

Responsibilities:

- Normalize and classify the query.
- Check fresh search cache and coalesce in-flight identical queries.
- Fetch WatchFacts search payload through a WatchFacts search port.
- Parse, match, dedupe, score, group, and audit results.
- Persist query/cache/issue metrics through repositories.
- Return structured domain/application results.

It should not:

- Format Telegram messages.
- Build MCP-specific payloads.
- Create OpenWA drafts.
- Call OpenAI unless the configured mode explicitly requires a controlled
  refinement path.

### ResultPageUseCase

Responsibilities:

- Create result-page artifacts from sanitized result payloads.
- Validate page token, nonce, rate limits, and expiry.
- Resolve action targets by `result_id`, `stable_listing_id`, or rank.
- Delegate side effects to OpenWA or issue use-cases.

It should not:

- Parse WatchFacts HTML.
- Re-run matching rules.
- Expose server-side secrets to browser code.

### OpenWAHandoffUseCase

Responsibilities:

- Build safe draft payloads from selected result data.
- Preserve original structured fields: title, seller, source, price text when
  present, and result identity.
- Optionally use AI later to draft human-friendly message copy from already
  verified fields.

It should not:

- Invent seller contacts.
- Invent prices.
- Depend on Telegram-specific formatting.

### IssueTriageUseCase

Responsibilities:

- Record feedback and suspicious results.
- List, inspect, update, and export issues.
- Link AI suggestions or audit findings to deterministic regression fixtures.

It should not:

- Mutate matcher rules automatically.
- Deploy changes automatically.

### AuditTriageUseCase

Responsibilities:

- Summarize audit JSON/JSONL artifacts deterministically.
- Optionally call OpenAI with bounded/redacted evidence for offline issue
  classification.
- Produce fixture hints and maintainer next steps.

It should not:

- Run in the request path.
- Change user-facing search results.
- Receive cookies, browser state, `.env`, full HTML, or logs with secrets.

## AI Boundary

AI should be split into use-case specific helpers instead of a single broad
"AI" feature:

| Capability | Runtime Path | Can Affect User Output | Default |
| --- | --- | --- | --- |
| Audit triage | offline/manual CLI | no | enabled without OpenAI, OpenAI opt-in |
| Issue triage | owner/manual | no | future |
| Brand/model token curation | maintainer/manual | no until reviewed rule lands | future |
| OpenWA draft copy | selected handoff action | yes, but only message copy | future opt-in |
| Search result refinement | search path | only in guarded mode | off |

AI output is evidence or draft text. It is not authoritative. Accepted patterns
should become deterministic tests and rules whenever they reveal repeatable
matcher/parser behavior.

## Data And Identity

Keep three identity levels distinct:

| Identity | Scope | Purpose |
| --- | --- | --- |
| `result_id` | short-lived query/rank/listing handle | user follow-up in current cache window |
| `stable_listing_id` | more durable listing identity | restart-resilient follow-up and issue correlation |
| WatchFacts source URL/listing number | upstream source reference | source verification and dedupe evidence |

SQLite remains appropriate for this scale, but access should gradually move
behind small repository methods used by application use-cases.

## Deployment And Runtime Operations

Service names remain:

- `watchfacts-bot`
- `watchfacts-mcp`

Docker image remains:

- `watchfacts:local`

Deploy commands remain:

```bash
make deploy-bot
make deploy-mcp
make deploy
```

Readiness should be expressed as separate checks:

- database ready
- WatchFacts browser state present and valid
- WatchFacts HTTP client ready or cooling down
- result page storage writable when configured
- OpenWA ready when enabled
- AI optional readiness when explicitly enabled

Search runtime readiness must not depend on OpenAI.

## Migration Plan

### Phase A: Contract Baseline

Acceptance:

- Public imports remain stable.
- MCP payload contract tests cover result IDs, pagination, source/image fields,
  diagnostics, and OpenWA follow-up handles.
- Result page action contract tests cover nonce, token, and sidecar payload
  behavior.
- Search audit JSON/JSONL fixtures cover summary and stage-event shape.

Verification:

```bash
python -m pytest tests/test_public_import_contracts.py tests/test_tool_runtime.py tests/test_audit_quality.py
python -m compileall app scripts
```

### Phase B: Application Use-Case Shells

Create small use-case wrappers without moving core logic yet:

- `SearchUseCase`
- `OpenWAHandoffUseCase`
- `IssueTriageUseCase`
- `AuditTriageUseCase`

Initial implementation lives under `app/application/`. Runtime and diagnostics
adapters can call these shells while existing deterministic search, payload
formatting, SQLite schema, OpenWA client, and audit parsing logic remain in
their current modules.

Acceptance:

- Telegram, MCP, and diagnostics still call the same search behavior.
- No result ordering, eligibility, or payload changes unless tests pin them.

### Phase C: Repository Boundary

Move direct `Database` usage behind focused repository methods:

- search cache repository
- result reference repository
- issue repository
- AI suggestion repository

Initial implementation lives under `app/infrastructure/` and keeps the existing
SQLite schema. Runtime and application layers use these repositories for result
references, feedback/suspicious issues, search cache/query metrics, and AI
suggestion review records while remaining compatible with existing `Database`
test injection.

Acceptance:

- Existing SQLite schema remains compatible.
- No migration is required unless a future feature explicitly needs one.

### Phase D: AI Provider Boundary

Extract a shared OpenAI client used by:

- controlled result refinement
- offline audit triage
- future issue triage
- future OpenWA draft copy

Initial implementation lives in `app/infrastructure/openai_client.py`.
Controlled result refinement and offline audit triage both use this shared
Responses API client while keeping their prompt construction, schemas,
redaction, and local validation gates in their existing use-case modules.

Acceptance:

- OpenAI remains optional.
- `HYBRID_AI_MODE=off` keeps search deterministic.
- No secret is sent to model payloads.

### Phase E: Interface Slimming

Reduce Telegram/MCP modules to adapters:

- parse user/API inputs
- call use-cases
- render Telegram or MCP/result-page responses
- handle transport-specific errors

Initial implementation adds `SearchPayloadUseCase` for the MCP/tool-runtime
search path. `app/runtime/tool_runtime.py` still validates MCP-style arguments
and serializes the response payload, while search execution, result caching,
pagination metadata, diagnostics attachment, and optional result-page generation
are coordinated through the application use-case.

Acceptance:

- No parser, matcher, scoring, or dedupe logic lives in adapters.
- Bot and MCP deploy smoke pass.

## Testing Strategy

Use test layers that match architecture layers:

| Layer | Test Style |
| --- | --- |
| domain | fast unit tests for parser, matcher, scoring, dedupe, query intent |
| application | integration tests with fake WatchFacts/OpenWA/OpenAI ports |
| infrastructure | focused tests for HTTP/session parsing, SQLite persistence, OpenWA API payloads |
| interfaces | Telegram handler tests, MCP contract tests, result page browser/action tests |
| operations | Makefile dry runs, Docker compose config, deploy smoke, MCP benchmark |

Every behavior-changing migration must show before/after evidence:

```bash
python -m pytest -q
python -m compileall app scripts
python scripts/diagnostics/audit_quality.py "5205r green" --format jsonl --limit 5
python scripts/diagnostics/ai_audit_triage.py audit-report.jsonl
```

## Non-Goals

- No big-bang rewrite.
- No framework migration as part of this architecture phase.
- No database replacement.
- No LLM-first search, ranking, or extraction.
- No bypass of WatchFacts login, captcha, Cloudflare, or anti-bot boundaries.
- No removal of public compatibility imports without a migration plan.

## Open Questions

- Should use-case classes live under a new `app/application/` package, or should
  the current `app/runtime/tool_runtime.py` evolve first?
- Should repository interfaces be formal protocols now, or only concrete
  wrapper classes until a second storage backend exists?
- Should AI OpenWA draft copy be implemented before AI issue triage, given its
  direct operator value?
- Should production set a longer `SEARCH_CACHE_TTL_SECONDS` for common watches,
  or keep freshness at 30 minutes and rely on prewarm?

## Success Criteria

This redesign phase is complete when:

- ADR-008 records the architecture boundary decision.
- This document is linked from the documentation index.
- Follow-up implementation tasks can be completed in small commits.
- Existing production deploy commands and runtime contracts remain unchanged.
- Future agents can identify where to add search, OpenWA, AI, audit, or result
  page changes without rereading the entire repository.
