# ADR-007: Use Server-Side Result Page Actions With Page Nonces

## Status

Accepted

## Date

2026-06-11

## Context

Generated WatchFacts result pages are the primary listing UI for Telegram and a
useful handoff surface for Hermes users. The detail modal currently copies
prompts for OpenWA draft creation and issue reporting, which forces the operator
back through Hermes/MCP for real side effects.

The project already has server-side implementations for OpenWA draft creation
and issue reporting. OpenWA uses an API key and must remain a server-to-server
integration. Result page links are public token URLs while they are valid, so any
real action from the page needs explicit abuse controls.

## Decision

Add result-page HTTP action routes on the WatchFacts MCP server and keep all side
effects server-side. Each generated page will have a random `action_nonce` stored
in a sidecar JSON file. Browser POST actions must provide the page token,
`action_nonce`, and a valid `result_id`. The server validates token TTL, nonce,
rate limits, and result identity before creating an OpenWA draft or recording a
feedback issue.

## Alternatives Considered

### Keep copy-only actions

- Pros: lowest security risk and no new HTTP surface.
- Cons: modal remains a helper, not a real workflow surface.
- Rejected because the desired UX is direct action from the result page.

### Browser calls OpenWA directly

- Pros: fewer WatchFacts server changes.
- Cons: exposes OpenWA API details or requires a separate browser auth layer.
- Rejected because OpenWA credentials must stay server-side.

### Require operator login for result pages

- Pros: strongest action protection.
- Cons: larger product and infrastructure change than needed for v1.
- Deferred; can be revisited if public-token action risk becomes unacceptable.

## Consequences

- Result page generation must write and clean up sidecar metadata.
- The MCP server gains JSON POST routes in addition to MCP tools and static page
  serving.
- Public result pages can perform actions while valid if the viewer has the full
  page and nonce. TTL and rate limits are required controls.
- OpenWA API keys, WatchFacts browser state, and database paths remain hidden from
  browser code.
