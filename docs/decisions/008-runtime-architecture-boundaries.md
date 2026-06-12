# ADR-008: Adopt Layered Runtime Architecture Boundaries

## Status

Accepted

## Date

2026-06-12

## Context

WatchFacts has grown from a Telegram bot into a shared runtime with:

- primary Telegram search delivery through `watchfacts-bot`
- structured MCP access through `watchfacts-mcp`
- generated result pages with server-side actions
- OpenWA chat draft handoff
- SQLite search cache, result references, feedback, and issue queues
- deterministic parser, matcher, dedupe, scoring, and audit artifacts
- optional OpenAI refinement and offline AI audit triage

The current implementation is functional and tested, but feature work now tends
to cross several concerns at once. For example, an OpenWA or result-page change
can touch Telegram formatting, MCP payloads, result identity, SQLite cache, and
action-sidecar behavior. AI work has also expanded beyond result refinement into
offline audit triage, which needs a clearer provider and data-safety boundary.

At the same time, the production system has strict constraints:

- Core WatchFacts search must remain deterministic and auditable.
- Telegram and MCP must share one search implementation.
- WatchFacts access must use authorized saved browser state only.
- OpenAI must remain optional and must not receive secrets, browser state, full
  HTML, or unbounded raw logs.
- Deploy commands and service names should remain stable.

## Decision

Adopt a layered architecture target with these boundaries:

```text
interfaces -> application -> domain
interfaces -> application -> infrastructure
application -> domain
application -> infrastructure ports
domain -> no interfaces, no infrastructure
```

The target layers are:

- **interfaces**: Telegram adapter, MCP adapter, result-page HTTP/actions, and
  diagnostics CLIs.
- **application**: use-cases that coordinate search, result pages, OpenWA
  handoff, issue triage, and audit/AI triage.
- **domain**: query intent, listing model, parser contracts, matcher rules,
  scoring policy, dedupe identity, issue taxonomy, and audit event contracts.
- **infrastructure**: WatchFacts HTTP/session, SQLite repositories, OpenWA API
  client, OpenAI client, result page storage, and runtime config.

This is a migration target, not a one-shot rewrite. Current public imports and
entrypoints remain stable:

- `python -m app.main`
- `python -m app.mcp_server`
- `app.search`
- `app.matcher`
- `app.tool_runtime`
- `app.telegram_bot`
- `app.mcp_server`

Implementation should proceed in small phases:

1. Lock contracts with tests.
2. Add application use-case shells.
3. Move direct SQLite usage behind focused repositories.
4. Extract a shared OpenAI provider boundary.
5. Slim Telegram and MCP modules into transport adapters.

## Consequences

Positive:

- Search behavior remains shared while adapters become simpler.
- AI can grow in offline/review/handoff use-cases without becoming core search.
- Result identity, cache, and follow-up behavior can be tested at application
  boundaries instead of transport boundaries only.
- SQLite and external APIs become easier to reason about because side effects
  live in infrastructure.
- Future refactors can be staged without breaking deploy commands.

Tradeoffs:

- Short term, there will be some wrapper code around existing modules.
- Strict layering requires discipline; compatibility imports must remain until a
  migration explicitly removes them.
- Repository boundaries may look unnecessary while SQLite is the only backend,
  so wrappers should stay small and concrete at first.
- Contract tests must be maintained carefully to prevent refactors from hiding
  behavior changes.

## Alternatives Considered

### Keep Current Structure

Pros:

- No migration cost.
- Current tests and deploy flow already pass.
- Familiar to existing operators.

Cons:

- New features continue to cross Telegram, MCP, SQLite, result pages, and AI.
- It is harder to tell where AI triage, OpenWA draft copy, and result-page
  actions should live.
- Direct database usage remains spread across runtime boundaries.

Rejected as the long-term target because it increases maintenance cost as
features expand.

### Big-Bang Rewrite

Pros:

- Could produce a clean package structure quickly.
- Removes some compatibility shims sooner.

Cons:

- High risk of breaking search correctness, result identity, auth/session, and
  deploy behavior.
- Hard to review and revert.
- Conflicts with the project's current production-hardening posture.

Rejected. The system should migrate in small, test-backed phases.

### LLM-Centric Search Redesign

Pros:

- Could handle some messy listing formats with less hand-written rule work.

Cons:

- Conflicts with deterministic search requirements.
- Adds latency and operational dependency.
- Harder to audit and regression test.
- Increases risk of invented details.

Rejected for core search. AI remains useful for offline triage, owner review,
OpenWA draft copy, and guarded refinement under explicit controls.

## Related Documents

- [Architecture Redesign](../architecture-redesign.md)
- [ADR-001: Deterministic Matching](001-deterministic-matching.md)
- [ADR-005: Controlled Hybrid AI Refinement](005-controlled-hybrid-ai-refinement.md)
- [ADR-006: Result Identity And Follow-Up Caching](006-result-identity-and-followup-caching.md)
- [ADR-007: Result Page Server-Side Actions](007-result-page-server-side-actions.md)
