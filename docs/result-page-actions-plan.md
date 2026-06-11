# Result Page Real Actions Plan

## Purpose

Turn the result-page detail modal from a copy-helper surface into a real action
surface. The modal should let an operator create an OpenWA chat draft and report
a result issue directly from the generated result page, without exposing server
secrets or reimplementing WatchFacts search in browser code.

This plan was written before implementation and now records the first production
version. Future changes should preserve the server-side action boundary unless a
new ADR supersedes ADR-007.

Implementation status as of 2026-06-11:

- Phase 1 complete: generated pages write `{token}.html` plus `{token}.json`
  sidecars with a page-scoped `action_nonce`.
- Phase 2 complete: the MCP server exposes OpenWA draft and report action POST
  routes with token, expiry, nonce, result identity, and rate-limit validation.
- Phase 3 complete: the detail modal calls the action routes directly and keeps
  copy/source/similar controls as utilities.
- Phase 4 complete: operations and technical docs document the deployed behavior
  and smoke-test expectations.

## Current State

Generated result pages are static HTML served by `GET /results/{token}` from the
MCP service. Each page has a sidecar JSON file used only by server-side action
routes. The modal provides these actions:

- `Create OpenWA draft`: posts to the MCP server and creates an OpenWA draft
  server-side when OpenWA handoff is enabled.
- `Report issue`: posts to the MCP server and records a feedback issue.
- `Copy URL`: copies the WatchFacts source URL when present.
- `+N similar`: toggles similar listings in the modal.

Fallback copy buttons are still present only when an older page payload does not
include action URLs or a page nonce.

The server-side capabilities are reused from MCP/runtime code:

- `create_chat_draft` / `watchfacts_create_chat_draft_payload()` creates OpenWA
  chat drafts server-side.
- `report_issue` / `watchfacts_report_issue_payload()` records feedback issues.
- Result pages already contain sanitized listing fields, `result_id`, source URL,
  seller, seller phone, image URL, and rank.

## Target Behavior

The detail modal should expose real actions:

- Primary action: `Create OpenWA draft`.
- Report action: `Report issue` with a small in-modal form.
- Utility actions: `Copy URL`, `Copy ID`, and similar-list toggle.

OpenWA and report actions must call the WatchFacts MCP HTTP server, not OpenWA or
SQLite directly from browser code. The browser must never receive OpenWA API keys,
WatchFacts cookies, database paths, raw browser state, or `.env` values.

## Public HTTP Action Contract

The MCP server exposes two result-page action routes:

```text
POST /results/{token}/actions/openwa-draft
POST /results/{token}/actions/report
```

Both routes use the existing result page token and require an `action_nonce` that
was generated with the page payload. The route must reject invalid tokens, expired
pages, invalid nonce values, missing results, invalid inputs, and rate-limited
requests with safe JSON responses.

OpenWA request body:

```json
{
  "action_nonce": "page-scoped nonce",
  "result_id": "watchfacts-result..."
}
```

Report request body:

```json
{
  "action_nonce": "page-scoped nonce",
  "result_id": "watchfacts-result...",
  "reason": "missing_info | wrong_result | other",
  "notes": "optional operator notes"
}
```

Success response examples:

```json
{
  "ok": true,
  "status": "created",
  "result_id": "watchfacts-result...",
  "draft_id": "...",
  "chat_id": null,
  "dashboard_url": "https://openwa.example/chats/drafts/..."
}
```

```json
{
  "ok": true,
  "status": "recorded",
  "result_id": "watchfacts-result...",
  "issue_ref": "F123",
  "issue": {
    "id": 123,
    "issue_type": "feedback",
    "status": "open",
    "reason": "wrong_result"
  }
}
```

Error responses should be structured and non-secret-bearing:

```json
{
  "ok": false,
  "error": "expired | not_found | invalid_nonce | invalid_result | validation_error | openwa_unavailable | rate_limited",
  "message": "Safe user-facing explanation"
}
```

## Data Model And Storage

Generated pages write a result-page sidecar JSON file next to the HTML file. The
sidecar is the action source of truth.

Recommended file layout:

```text
data/result_pages/{token}.html
data/result_pages/{token}.json
```

The sidecar should contain only sanitized data already safe enough for the result
page plus server-only action metadata:

```json
{
  "action_nonce": "random secret nonce",
  "payload": {
    "query": "...",
    "created_at": "...",
    "expires_at": "...",
    "results": []
  }
}
```

Rules:

- Cleanup must remove expired `.html` and `.json` files together.
- `read_result_page_html()` should preserve existing behavior for normal page
  viewing.
- New action helpers may load the sidecar and validate expiry.
- Sidecar payload must not include raw WatchFacts HTML, cookies, browser state,
  OpenWA keys, Telegram tokens, or `.env` values.
- OpenWA payload construction should use sanitized result-page fields. If a field
  is missing, omit it instead of inventing it.

## Security And Abuse Controls

The result page URL is public while the token is valid. Treat every action POST as
untrusted.

Required controls:

- Token format validation using the existing token rules.
- Page TTL validation before any side effect.
- Per-page `action_nonce` validation for every POST.
- Rate limit by client IP and token/action.
- Safe JSON errors only; do not return stack traces or config details.
- Server-side OpenWA calls only; never expose `OPENWA_API_KEY`.
- No browser-side direct database access.
- No WatchFacts recrawl from action routes.

Accepted residual risk for v1:

- Anyone with the full live result page can perform actions until the page
  expires. The nonce prevents blind POSTs but is visible to someone who can load
  the page. Keep TTL and rate limits meaningful.

## UI Behavior

### OpenWA draft

- Replace `Copy OpenWA` with `Create OpenWA draft`.
- Button states:
  - idle: `Create OpenWA draft`
  - pending: `Creating...`
  - success: `Draft created`
  - error: show inline error and allow retry
- On success, show `Open draft` when `dashboard_url` is returned.
- Disable duplicate submission while pending.

### Report issue

- Replace `Copy Report` with an in-modal report form.
- Required reason select values:
  - `wrong_result`
  - `missing_info`
  - `other`
- Optional notes textarea.
- Submit states:
  - idle: `Submit report`
  - pending: `Submitting...`
  - success: `Reported as F{id}`
  - error: inline safe error and retry
- Disable repeat submit for the same result in the current page session after a
  successful report.

### Utility actions

Keep these actions as non-primary utilities:

- `Copy URL`
- `Copy ID`
- `Similar listings`

Layout rules:

- Mobile: primary action full-width, report form full-width, utilities grouped
  below.
- Tablet: primary action full-width or first row, report form below, utilities in
  a compact grid.
- Desktop: actions can sit in a compact grid, but primary action must remain
  visually distinct from copy utilities.

## Implementation Phases

### Phase 1: Sidecar And Action Read Model

Status: complete.

Goal: make result-page action data loadable server-side without adding side
effects yet.

Implementation:

- Write `{token}.json` sidecar when generating a result page.
- Include `action_nonce` and sanitized payload in the sidecar.
- Add non-route helpers to load and validate the sidecar by token.
- Update cleanup to remove expired sidecars and orphaned sidecars.
- Keep existing HTML serving behavior unchanged.

Acceptance:

- Existing result pages still render.
- New page generation writes both HTML and sidecar.
- Expired cleanup removes both files.
- Invalid token, missing sidecar, and expired sidecar are distinguishable in tests.

Verification:

```bash
python -m pytest tests/test_result_pages.py
python -m compileall app
```

Self-review before commit:

- Confirm sidecar contains no secrets or raw browser state.
- Confirm existing result page route behavior did not change.
- Confirm cleanup cannot delete unrelated files.

Commit after this phase before continuing.

### Phase 2: HTTP Action Routes Without UI Wiring

Status: complete.

Goal: add server-side POST routes and test them with mocked OpenWA/report flows.

Implementation:

- Add `POST /results/{token}/actions/openwa-draft`.
- Add `POST /results/{token}/actions/report`.
- Validate token, TTL, nonce, result id, and body fields.
- Build OpenWA draft payload from sidecar result fields and call the existing
  OpenWA handoff boundary.
- Record feedback through the existing database issue API.
- Add action-specific rate limiting by IP/token/action.
- Return only safe structured JSON.

Acceptance:

- OpenWA route returns success with mocked OpenWA client.
- Report route records a feedback issue and returns `issue_ref`.
- Invalid nonce returns a forbidden safe error.
- Invalid result id returns validation error.
- Expired/missing page returns safe not found/expired errors.
- OpenWA unavailable returns safe error without leaking config.
- Rate-limited requests return `429`.

Verification:

```bash
python -m pytest tests/test_mcp_server.py tests/test_result_pages.py tests/test_openwa_handoff.py tests/test_db.py
python -m compileall app
```

Self-review before commit:

- Confirm action routes do not expose secrets.
- Confirm OpenWA API key remains server-side only.
- Confirm all side-effect paths require nonce and unexpired token.
- Confirm errors are safe for public browser display.

Commit after this phase before continuing.

### Phase 3: Modal UI Wiring

Status: complete.

Goal: replace copy-helper actions in the modal with real browser actions.

Implementation:

- Embed action endpoint URLs and `action_nonce` into the result page payload or a
  safe script variable.
- Replace `Copy OpenWA` with `Create OpenWA draft` action.
- Replace `Copy Report` with the report mini form.
- Add fetch helpers for POST JSON, loading states, success states, and inline
  safe errors.
- Keep `Copy URL`, `Copy ID`, and similar toggle as utilities.
- Update responsive modal action layout for mobile, tablet, and desktop.

Acceptance:

- OpenWA button calls the action route and shows success/error states.
- Report form validates reason and submits notes.
- Successful report disables repeat submit for that result in the page session.
- Utility actions still work.
- Modal keyboard/focus behavior remains intact.
- No browser action requires Hermes to copy/paste prompts.

Verification:

```bash
python -m pytest tests/test_result_pages.py tests/test_mcp_server.py
python -m compileall app
```

Browser verification:

- Test modal at `320`, `390`, `768`, `1024`, and `1440` widths.
- Confirm no horizontal overflow.
- Confirm action buttons are reachable by keyboard.
- Confirm success/error states render without layout breakage.
- Confirm console has no CSP/connect-src errors.

Self-review before commit:

- Confirm the primary action is visually distinct.
- Confirm report form labels and accessible names are clear.
- Confirm failure states are visible and retryable.
- Confirm copy utilities are not confused with server-side operations.

Commit after this phase before continuing.

### Phase 4: Docs, Operations, And Production Verification

Status: complete.

Goal: finish operator-facing docs and prepare safe deployment.

Implementation:

- Update operations docs with result page action behavior, required OpenWA env,
  and troubleshooting steps.
- Update security docs with result-page public-action boundaries.
- Update technical spec with action route contracts.
- Document deployment verification and rollback.

Acceptance:

- Docs explain that result page actions are public-token actions protected by
  nonce, TTL, and rate limits.
- Docs explain OpenWA keys stay server-side.
- Docs explain report issues can be reviewed through MCP issue tools.
- Production verification checklist includes result page action smoke checks.

Verification:

```bash
git diff --check
python -m pytest tests/test_result_pages.py tests/test_mcp_server.py
```

Self-review before commit:

- Confirm docs match implemented routes and response shapes.
- Confirm no docs include real secrets, tokens, or production nonce values.
- Confirm rollback path is documented.

Commit after this phase.

## Rollout And Rollback

Rollout:

1. Complete and commit each phase separately.
2. Run full relevant tests before deploy.
3. Deploy with the normal MCP/server path.
4. Smoke test one generated result page with mocked or controlled OpenWA behavior.
5. Confirm report issue appears in `list_issues` / `get_issue`.
6. Confirm OpenWA draft appears in the OpenWA dashboard when enabled.

Rollback:

- If action routes fail but result pages still render, temporarily hide real
  action buttons in the template and keep utility copy actions.
- If sidecar generation fails, disable result pages with
  `RESULT_PAGE_PUBLIC_BASE_URL` until fixed.
- If OpenWA causes errors, set `ENABLE_OPENWA_CHAT_HANDOFF=false`; report action
  can still remain enabled.

## Open Questions For Future Versions

- Add authenticated operator-only result pages instead of public token pages.
- Add one-time action tokens for OpenWA draft creation.
- Add audit log rows for result page action attempts.
- Add per-action environment flags such as `RESULT_PAGE_ACTIONS_ENABLED` and
  `RESULT_PAGE_OPENWA_ACTIONS_ENABLED` if production needs staged rollout.
