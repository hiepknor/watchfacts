# System Design Review

Date: 2026-06-10 (latest review update)

Status: review snapshot, first follow-up fixed
Additional snapshot: current operational assessment completed.

## Current Assessment Snapshot (2026-06-10)

### Scope reviewed

- MCP and Telegram share the same search/parse/match pipeline through
  `WatchFactsSearchWorkflow`.
- Search flow with realistic fixtures and end-to-end tests (including cache and
  follow-up tools).
- Result presentation contract in MCP responses and legacy Telegram rendering.
- Deployment and cache behavior under repeated query patterns.

### Findings

- **Design maturity:** Solid-to-good for this product scope. The layer separation
  is clear and maintainable (scraper → parser → matcher → ranking → persistence
  → payload).
- **Determinism:** Matching and extraction remain deterministic; AI refinement is
  optional and guarded, and does not replace deterministic output.
- **Quality controls:** Suspicious detection, issue recording, `suspicious_summary`,
  and focused test coverage are present for the main runtime paths.
- **Follow-up:** `result_id` is explicitly treated as a short-lived handle for MCP
  follow-up in both docs and code, which is the safer approach for current safety
  boundaries.

### Remaining Gaps

- For color-specific queries such as `15510or blue`, recent runs still show a
  non-trivial number of results missing `image_url`, especially with multi-listing
  variant groupings. This remains a core data-quality gap.
- `result_id` is stable for query/rank/listing snapshot but is not a durable
  listing identity suitable for long-lived replay scenarios.
- In-memory `_RESULT_CACHE` provides fast follow-up lookup, but restart semantics
  still depend on process lifetime; after restart, follow-up flows may fallback to
  re-search.

### Quick Validation

- `python -m pytest -q`: **476 passed**.
- Color-specific flow for `15510or blue` (server-filtered + nested variants) is
  covered by regression tests, including non-colored variants under colored parent
  groups.
- No active critical security bypass findings in the current code path.

### Recommended Next Priorities (next 1-2 weeks)

1. Add a targeted regression test for variants that should inherit parent images
   when the visible variant text is not color-matching but belongs to a color-specific
   group.
2. Start tracking quality metrics: `image_missing_rate`, `server_filtered_hit_rate`,
   `search_cache_hit`, `playwright_fallback_rate`.
3. Consider an internal `stable_listing_id` derived from `source_url` for long-lived
   follow-up/issue workflows, while preserving MCP short-lived `result_id` contract.
4. Expand health/quality alerting so unusual `result image missing` spikes by query
   class trigger early investigation.

This review complements the product spec, technical spec, operations guide, and
ADRs. It documents the current system shape, the design choices that are working
well, and the system risks that should be handled next. It does not change the
runtime contract by itself.

## Current Architecture

The production path is Hermes through MCP:

```text
Hermes
  -> watchfacts-mcp /mcp tools
  -> app.mcp_server
  -> app.tool_runtime payload functions
  -> WatchFactsSearchWorkflow
  -> scraper, parser, matcher, dedupe, scoring, similarity grouping
  -> suspicious issue detection, optional guarded AI refinement, SQLite cache
  -> structured MCP response
```

The legacy Telegram bot remains a supported channel, but it calls the same
`WatchFactsSearchWorkflow` instead of owning a separate search implementation.
This is the most important architectural boundary in the project: Hermes,
Telegram, diagnostics, and tests should reuse the same deterministic runtime.

The runtime has two cache layers:

- SQLite `search_cache` stores serialized search results for repeated normalized
  queries.
- In-memory `_RESULT_CACHE` in `app.tool_runtime` stores result handles for
  follow-up MCP tools such as `create_chat_draft` and `report_issue`.

Authenticated WatchFacts access is handled only through a saved Playwright
browser state file. The scraper loads the existing state, navigates to the
configured WatchFacts page, posts the visible search form when possible, and
parses the returned JSON or HTML. The design does not bypass login, captcha,
Cloudflare, or anti-bot systems.

## Design Strengths

- Shared runtime keeps the production MCP path and legacy Telegram path from
  drifting apart.
- Core search remains deterministic: parser, matcher, dedupe, scoring, and
  grouping run locally and are covered by focused tests.
- MCP responses are structured enough for Hermes to answer without inventing
  result IDs, seller data, image URLs, source links, pagination state, or OpenWA
  links.
- SQLite is used for local query history, dedupe records, search cache, issue
  queues, and AI refinement suggestions without requiring an external database.
- The scraper respects the authenticated-browser boundary and fails clearly when
  browser state is missing or expired.
- Default `make deploy` encodes the intended production deployment path:
  pull, build, run checks, recreate legacy bot and `watchfacts-mcp`.
- `make deploy-hermes-mcp` is used for MCP schema/config changes when Hermes
  restart is required; it pulls/builds/recreates MCP, then restarts Hermes so it
  reloads tool schema/config.

## Risks And Follow-Ups

1. Resolved: fix stale rank follow-up resolution.

   `_resolve_result_by_rank()` looks for the first in-memory cache entry with a
   matching normalized query and rank. If the same query/rank has appeared in
   previous tests or previous production searches, a rank-based follow-up can
   target an older listing. Before the fix, full test suite verification exposed
   this through:
   `tests/test_tool_runtime.py::test_watchfacts_create_chat_draft_uses_cached_search_result`
   when global `_RESULT_CACHE` already contained an older rank 1 result for the
   same query.

   Implemented fix: rank lookup now prefers the newest stored result for the
   query/rank. Regression coverage stores two result sets for the same
   query/rank and verifies rank follow-up resolves to the latest search payload.

2. Resolved: clarify `result_id` stability.

   The MCP contract describes `result_id` as stable, but the current hash
   includes query, rank, listing text, raw listing text, and source URL. This is
   stable enough for short-lived follow-up handles, but it changes if ranking or
   extracted listing text changes. That is acceptable for an operational cache
   handle, but not for a durable listing identity.

   Current fix: document `result_id` as a short-lived follow-up handle. A
   separate durable listing identifier derived from source URL or WatchFacts
   listing number can be introduced later without changing the existing MCP
   field.

3. Resolved: add MCP search backpressure for distinct queries.

   `WatchFactsSearchWorkflow` coalesces identical in-flight searches, but the
   MCP path does not have a shared concurrency limit for different queries. A
   burst of Hermes searches can launch multiple Playwright browser sessions and
   WatchFacts requests at the same time. Telegram has
   `TELEGRAM_MAX_CONCURRENT_SEARCHES`; MCP needs an equivalent runtime guard.

   Implemented fix: search-mode runtime settings now include
   `SEARCH_MAX_CONCURRENT_SEARCHES`, defaulting to 1. The search workflow applies
   a semaphore only for `runtime_mode="search"`, so Hermes/MCP and diagnostics
   are bounded while Telegram keeps its existing queue behavior. Identical-query
   coalescing still happens before the concurrency gate.

4. Resolved: narrow `create_chat_draft` response exposure.

   `watchfacts_create_chat_draft_payload()` returns the full OpenWA draft payload
   to the MCP caller, including raw listing fields. The OpenWA API needs the
   payload, but Hermes generally should not receive raw listing text unless a
   diagnostic path explicitly asks for it.

   Implemented fix: normal `create_chat_draft` responses return only safe
   follow-up fields: status, result_id, rank, draft_id, chat_id, and
   dashboard_url. The full OpenWA payload remains internal to the OpenWA client
   call.

5. Resolved: add a Docker healthcheck for `watchfacts-mcp`.

   The MCP service exposes a `health` tool and deployment runs tests before
   recreation, but `docker-compose.yml` does not define a healthcheck for
   `watchfacts-mcp`. Operators can inspect logs, but Compose cannot mark the MCP
   container unhealthy when the runtime is not ready.

   Implemented fix: `watchfacts-mcp` now has a lightweight Docker healthcheck
   that uses Python stdlib socket connection to verify the local MCP port is
   listening. It does not call WatchFacts, load browser state, or expose secrets.
   The stronger WatchFacts browser-session check remains available through the
   MCP `health` tool for operator diagnostics.

## Interface Notes

No public API should change as part of this documentation review. Future fixes
should preserve the existing MCP tool names:

- `search`
- `health`
- `create_chat_draft`
- `report_issue`
- `list_issues`
- `get_issue`
- `update_issue`
- `suspicious_summary`

Any future change to `result_id`, rank follow-up semantics, or
`create_chat_draft` response shape should be treated as a compatibility-sensitive
MCP contract change and verified against Hermes behavior.

## Verification Snapshot

Review-time verification:

```text
python -m pytest -q
```

Result:

```text
478 passed
```

The stale rank follow-up issue described above is fixed by
`tests/test_tool_runtime.py::test_watchfacts_create_chat_draft_rank_uses_latest_cached_result`.
Search runtime backpressure is covered by
`tests/test_search.py::test_search_workflow_limits_search_runtime_concurrent_distinct_queries`.
The MCP Docker healthcheck is covered by
`tests/test_docker_compose.py::test_watchfacts_mcp_service_has_lightweight_healthcheck`.
