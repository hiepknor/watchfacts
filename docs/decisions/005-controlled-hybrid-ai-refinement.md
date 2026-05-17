# ADR-005: Use OpenAI Controlled AI For Result Refinement

## Status

Accepted

## Date

2026-05-15

## Context

WatchFacts listing text is messy: one raw block may contain many products, emoji separators, compact dates, shorthand prices, multiple currencies, and seller commentary. Deterministic matcher fixes have improved quality, but each new pattern still requires manual review and a code/test change.

The operator wants the bot to become smarter over time without requiring constant admin intervention. At the same time, the project must preserve safety:

- Search results should stay deterministic and auditable by default.
- The bot must not invent listing details.
- Feedback should not trigger autonomous code changes or deploys.
- Secrets, cookies, browser state, and full page HTML must not be sent to or stored by AI systems.
- The production runtime should not depend on local model files or machine-specific CPU/RAM capacity.

## Decision

Use a controlled OpenAI approach:

1. Deterministic parser/matcher remains the default source of truth.
2. OpenAI may be added as a second-opinion/refiner only for suspicious, reported, or hard-to-scope cases.
3. Initial rollout must be shadow mode: AI suggestions are recorded for comparison and review, not shown to users.
4. Owner review mode may expose AI suggestions in issue review/digests.
5. Guarded user-facing AI correction is allowed only after confidence gates, production evidence, and regression tests exist.
6. Every accepted AI suggestion should become a deterministic regression fixture where practical.
7. Local model runtime support should be removed from the supported production path.

## Required Guards

An AI suggestion must be rejected or stored only for review if it fails any of these checks:

- Missing query reference or normalized equivalent.
- Missing required query descriptors.
- Suggested text cannot be traced to the raw listing snippet.
- Crosses known item separators or next-product boundaries.
- Expands into unrelated product, seller metadata, or commentary.
- Contains secrets, cookies, browser state, full page HTML, or unrelated user data.
- Exceeds Telegram-safe output limits.
- Model times out or returns malformed output.

## Alternatives Considered

### Keep Deterministic Rules Only

Pros:

- Safest and easiest to reason about.
- Excellent regression testing story.
- No added model latency or prompt safety concerns.

Cons:

- Maintainer must inspect and encode every new pattern.
- Admin still has to triage noisy issue queues.
- Some messy formats are expensive to handle with rules alone.

Rejected as the only long-term strategy because it does not reduce operator workload enough.

### Use AI As Primary Extractor

Pros:

- Could handle novel formats faster.
- Less hand-written matcher logic.

Cons:

- Higher risk of invented details.
- Harder to test and explain.
- Adds latency and operational dependency.
- Conflicts with the project boundary that core behavior should remain deterministic.

Rejected because correctness and auditability are more important than broad language flexibility.

### Keep Local Model Runtime As Provider

Pros:

- No external API dependency.
- Can run fully self-hosted when the host has enough resources.

Cons:

- Requires model files, extra Compose service, memory/CPU sizing, and local smoke tests.
- Quality and latency depend heavily on the operator machine.
- Adds a second provider path to prompts, config, docs, tests, and incident debugging.
- Does not simplify the bot's core risk: suggestions still need strict validation.

Rejected for the supported runtime because a single OpenAI provider is simpler to operate and test.

### Controlled OpenAI

Pros:

- Keeps deterministic behavior as the baseline.
- Lets OpenAI accelerate triage, clustering, and candidate correction.
- Can start safely in shadow mode.
- Converts accepted suggestions into tests and deterministic rules.
- Avoids local model deployment and service maintenance.

Cons:

- More moving parts: modes, gates, logs, review UI, and metrics.
- Requires careful prompt/data minimization.
- Guarded production use needs real evidence before enabling.

Accepted because it improves long-term quality while preserving control.

## Consequences

- Future AI work must include explicit runtime modes: `off`, `shadow`, `review`, and optionally `guarded`.
- OpenAI must be optional and disabled by default.
- Search must still work when OpenAI is unavailable.
- Owner/admin tooling should focus on issue clustering, review summaries, and regression fixture generation.
- The team should prefer deterministic matcher improvements whenever an AI suggestion reveals a repeatable pattern.
- Local model docs, config, Makefile targets, Compose service, and smoke scripts should be removed as part of the OpenAI migration.
