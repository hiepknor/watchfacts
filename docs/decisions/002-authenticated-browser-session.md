# ADR-002: Use Manual Login And Saved Browser State

## Status

Accepted

## Date

2026-05-12

## Context

WatchFacts requires authenticated access. The project must not store passwords or bypass access controls. Operators need a practical way to authenticate the browser used by Playwright.

## Decision

Provide a `scripts/ops/login.py` flow that opens Chromium for manual operator login and saves Playwright storage state to:

```text
data/watchfacts_state.json
```

The bot reuses this state when crawling.

## Alternatives Considered

### Store Username And Password In `.env`

- Pros: Fully automated login.
- Cons: Stores sensitive WatchFacts credentials and increases security risk.
- Rejected.

### Scrape Without Login

- Pros: Simpler.
- Cons: Does not satisfy authenticated WatchFacts access and could cross compliance boundaries.
- Rejected.

### External Browser Profile Mount

- Pros: Uses an existing operator browser session.
- Cons: More fragile across machines and harder to document safely.
- Rejected for initial version.

## Consequences

- Operators perform manual login when state is missing or expired.
- Browser state becomes sensitive runtime data and must be ignored by git.
- The bot must produce clear errors for missing or expired state.
