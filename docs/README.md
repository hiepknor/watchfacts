# WatchFacts Bot Documentation

This directory is the project knowledge base for humans and AI agents.

## Documents

| Document | Purpose |
| --- | --- |
| [Product Spec](product-spec.md) | Product goals, users, behavior, acceptance criteria, and non-goals |
| [Technical Spec](technical-spec.md) | Architecture, modules, data model, matching rules, and error handling |
| [Implementation Plan](implementation-plan.md) | Ordered implementation phases and verifiable tasks |
| [Roadmap](roadmap.md) | Milestones from foundation to production hardening |
| [Operations Guide](operations.md) | Local, Docker, login/session, data, logs, and deployment operations |
| [Security And Compliance](security-compliance.md) | Secrets, browser session state, WatchFacts access boundaries, and safe handling rules |
| [Contributing](contributing.md) | Development workflow, testing expectations, commits, and review rules |

## Architecture Decisions

Architecture Decision Records live in [decisions/](decisions/):

| ADR | Decision |
| --- | --- |
| [ADR-001](decisions/001-deterministic-matching.md) | Use deterministic matching instead of LLM extraction for core behavior |
| [ADR-002](decisions/002-authenticated-browser-session.md) | Use manual login and saved browser state for WatchFacts authentication |
| [ADR-003](decisions/003-sqlite-local-cache.md) | Use SQLite for local cache, dedupe, and query history |
| [ADR-004](decisions/004-docker-compose-runtime.md) | Use Docker Compose and Makefile as the primary runtime wrapper |

## Agent Usage

Agents should read documents selectively:

- For new features: start with `product-spec.md`, then `technical-spec.md`, then `implementation-plan.md`.
- For infrastructure changes: read `operations.md`, `security-compliance.md`, and ADR-004.
- For crawler changes: read `technical-spec.md`, `security-compliance.md`, and ADR-002.
- For matching/parser changes: read `technical-spec.md` and ADR-001.
- For commits: follow `AGENT.md` and the workflow in `contributing.md`.

Do not load every document into context by default. Load the smallest set that applies to the task.
