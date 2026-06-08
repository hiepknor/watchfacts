# WatchFacts Runtime Documentation

This directory is the project knowledge base for humans and AI agents.

Current production direction: WatchFacts search logic lives in a reusable
runtime, Hermes accesses it through the `watchfacts-mcp` Docker service, and the
Telegram bot is a supported legacy channel while the business flow moves to
Hermes.

## Documents

| Document | Purpose |
| --- | --- |
| [Project Soul](../SOUL.md) | Short operational context for humans and agents joining the project |
| [Product Spec](product-spec.md) | Product goals, users, behavior, acceptance criteria, and non-goals |
| [Technical Spec](technical-spec.md) | Architecture, modules, data model, matching rules, and error handling |
| [System Design Review](system-design-review.md) | Architecture review snapshot, strengths, risks, and prioritized follow-ups |
| [Continuous Improvement Spec](continuous-improvement.md) | Feedback buttons, issue storage, suspicious-result detection, and regression loop |
| [Result Quality Scoring Spec](result-quality-scoring.md) | Next-phase ranking, matcher diagnostics, score reasons, and refactor guardrails |
| [Production Quality Audit Spec](production-quality-audit.md) | Production query audit loop, issue classification, ambiguous price policy, and deploy verification |
| [Implementation Plan](implementation-plan.md) | Ordered implementation phases and verifiable tasks |
| [Roadmap](roadmap.md) | Milestones from foundation to production hardening |
| [Post-Subdomain Upgrade Plan](post-subdomain-upgrade-plan.md) | Recommended upgrade sequence after moving result templates to dedicated public subdomain |
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
| [ADR-005](decisions/005-controlled-hybrid-ai-refinement.md) | Use OpenAI controlled AI for result refinement |

## Agent Usage

Agents should read documents selectively:

- For new features: start with `product-spec.md`, then `technical-spec.md`, then `implementation-plan.md`.
- For architecture review or system-risk triage: read `system-design-review.md`, then the relevant spec or ADR it references.
- For Hermes/MCP changes: read `../SOUL.md`, `technical-spec.md`, `operations.md`, and `security-compliance.md`.
- For infrastructure changes: read `operations.md`, `security-compliance.md`, and ADR-004.
- For crawler changes: read `technical-spec.md`, `security-compliance.md`, and ADR-002.
- For matching/parser changes: read `technical-spec.md` and ADR-001.
- For result ranking or next-phase matcher diagnostics: read `result-quality-scoring.md`, `technical-spec.md`, and ADR-001.
- For production query audits or quality gate changes: read `production-quality-audit.md`, `result-quality-scoring.md`, and `operations.md`.
- For feedback/reporting improvements: read `continuous-improvement.md`, `technical-spec.md`, and `security-compliance.md`.
- For OpenAI controlled refinement: read `roadmap.md` Milestone 7, `implementation-plan.md` Phase 7, `technical-spec.md`, `security-compliance.md`, and ADR-005.
- For deploys: use `make deploy-hermes-mcp` on the server unless the task is explicitly about the legacy Telegram bot.
- For commits: follow `AGENTS.md` and the workflow in `contributing.md`.

Do not load every document into context by default. Load the smallest set that applies to the task.
