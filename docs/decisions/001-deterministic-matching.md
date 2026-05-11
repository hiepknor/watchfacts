# ADR-001: Use Deterministic Matching For Core Search

## Status

Accepted

## Date

2026-05-12

## Context

The bot needs to match Telegram search queries against WatchFacts listings. The README and project context state that no LLM is required. Users need predictable behavior for reference numbers, model names, color descriptors, and seller listing text.

## Decision

Use deterministic, case-insensitive, token-based matching with regex assistance for model/reference normalization.

Initial rule:

```text
listing matches query if every normalized query token appears in normalized listing text
```

## Alternatives Considered

### LLM-Based Extraction Or Ranking

- Pros: Can interpret messy natural language.
- Cons: Non-deterministic, harder to test, may add cost and external service dependency.
- Rejected for initial core behavior.

### Full-Text Search Engine

- Pros: Strong ranking and query operators.
- Cons: Adds operational complexity beyond local bot needs.
- Rejected for initial local/self-hosted scope.

### Simple Substring Matching Only

- Pros: Easy to implement.
- Cons: Too brittle for whitespace, punctuation, and case differences.
- Rejected in favor of normalized token matching.

## Consequences

- Matching is predictable and testable.
- Unit tests can cover important watch query examples.
- Ranking may be basic until future specs define scoring.
- Advanced query features require explicit specs before implementation.
