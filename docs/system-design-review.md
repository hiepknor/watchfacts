# System Design Review

Date: 2026-06-11 (latest review update)

Status: review snapshot updated after search-quality Phase 1-4 validation.

## Search Quality Phase Evidence (2026-06-11)

The search-quality improvement plan has shipped the first deterministic slices:

- Phase 1 audit visibility: quality audit rows now expose `has_image`,
  `image_reason`, `scope_reason`, `server_filtered`, bounded/redacted
  `raw_listing_preview`, and `stable_listing_id`.
- Phase 2 image attribution: image decisions use explicit reason codes such as
  `image.direct`, `image.inherited_parent_color`,
  `image.omitted_bundle_ambiguous`, and `image.missing_source`.
- Phase 3 stock-list QA: incomplete scoped stock-list segments and
  reference-only fragments are flagged/demoted instead of hidden.
- Phase 4 local verification: repository checks and authorized HTTPX smoke pass
  against the current runtime.

Latest local verification:

```text
make check
499 passed, 1 warning

make mcp-smoke
WARMUP ok=true elapsed_ms=4952 form_ms=4900 http_version=HTTP/1.1
HTTPX run=1 ok=true elapsed_ms=6033 status_elapsed_ms=6033 form_ms=0 post_ms=6032 http_version=HTTP/1.1 html_bytes=336595 server_filtered=True
SUMMARY httpx_passed=1/1
```

Latest quality-audit sample, `--limit 5`, uses live WatchFacts runtime data and
shows the new diagnostics are actionable:

| Query | Audited Results | Missing Images | Missing Rate | Scoped Stock List | Dominant Image Reasons |
| --- | ---: | ---: | ---: | ---: | --- |
| `5205r 2026` | 5 | 1 | 20% | 1 | `image.direct`, `image.omitted_bundle_ambiguous` |
| `126500ln white 2026` | 3 | 0 | 0% | 1 | `image.direct` |
| `7118/1200a grey` | 5 | 1 | 20% | 1 | `image.direct`, `image.omitted_bundle_ambiguous` |
| `Fpj Elegante Titanium` | 5 | 5 | 100% | 5 | `image.omitted_bundle_ambiguous` |
| `228235a choco` | 5 | 2 | 40% | 3 | `image.direct`, `image.missing_source`, `image.omitted_bundle_ambiguous` |
| `5712r` | 5 | 2 | 40% | 1 | `image.direct`, `image.missing_source`, `image.omitted_bundle_ambiguous` |
| `5205r green` | 5 | 0 | 0% | 1 | `image.direct` |
| `5726/1a` | 5 | 1 | 20% | 0 | `image.direct`, `image.missing_source` |
| `RM65-01 Lebron` | 5 | 4 | 80% | 4 | `image.direct`, `image.omitted_bundle_ambiguous` |
| `116500 panda` | 5 | 0 | 0% | 0 | `image.direct` |

Interpretation:

- The audit now distinguishes missing source images from intentionally omitted
  bundle images. This keeps the safety posture intact: ambiguous bundle images
  are omitted rather than attached to the wrong product.
- Stock-list image gaps remain concentrated in broad stock-list cards, notably
  `Fpj Elegante Titanium` and `RM65-01 Lebron`.
- Scoped stock-list segments that keep visible price evidence score as clean;
  incomplete scoped fragments are now routed to suspicious QA with specific
  reasons.
- `116500 panda` still needs future alias/descriptor refinement: the sample is
  image-complete, but top results include black Daytona listings because `panda`
  is currently treated as an alias/pass-through descriptor in some server-filtered
  flows.

## Current Operational Snapshot (2026-06-11)

- `15510or blue` returns `total_count=40`, `result_count=20`, `has_more=True` on
  the first page and the same 40 listings on page 2 with no overlap.
- `15510 or blue` returns the same result set as `15510or blue` (same `source_url`
  ordering and payload shape at both pages).
- `15510 or 2026` is a distinct constrained query: `total_count=4`,
  `result_count=4`, `has_more=False`.
- For `15510or blue` with `limit=40`, image completeness is currently
  `image_missing_count=13` (`32.5%` of first 40).
- Health metrics show quality counters are tracked and queryable:
  - `image_missing_rate: 0.0181`
  - `server_filtered_hit_rate: 0.0818`
  - `playwright_fallback_rate: 0.0`

This confirms parser behavior, pagination, and query normalization (`or` connector)
are consistent in deployed runtime.

## Current Assessment Snapshot (2026-06-10)

### Scope reviewed

- MCP and Telegram share the same search/parse/match pipeline through
  `WatchFactsSearchWorkflow`.
- Search flow with realistic fixtures and end-to-end tests (including cache and
  follow-up tools).
- Result presentation contract in MCP responses and Telegram rendering.
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
- In-memory `_RESULT_CACHE` provides fast follow-up lookup. Restart resilience is
  improved by SQLite `result_reference_cache`, which can now resolve by
  `result_id`, `stable_listing_id`, or rank before falling back to re-search.

### Quick Validation

- `python -m pytest -q`: **476 passed**.
- Color-specific flow for `15510or blue` (server-filtered + nested variants) is
  covered by regression tests, including non-colored variants under colored parent
  groups.
- No active critical security bypass findings in the current code path.

### Production Evidence Notes (2026-06-11)

- `15510or blue` and `15510 or blue` produce identical sets in MCP output.
- `playwright_fallback_count` for this query class is `0` in observed runs.
- Search cache quality counters for these queries are persisted in
  `search_cache` and visible through query cache metadata.

### Recommended Next Priorities (next 1-2 weeks)

1. Add a targeted regression test for variants that should inherit parent images
   when the visible variant text is not color-matching but belongs to a color-specific
   group.
2. Reduce image-missing rate for color-specific result groups, especially where
   parent-level image context should propagate to sub-listing variants.
3. Continue monitoring `stable_listing_id` follow-up resolution in MCP/OpenWA
   flows and add regressions for any stale-reference edge case.
4. Expand health/quality alerting so unusual `result image missing` spikes by query
   class trigger early investigation.
5. Add cache-hit trend tracking by query class so regressions hidden by cache
   freshness are easier to detect.

This review complements the product spec, technical spec, operations guide, and
ADRs. It documents the current system shape, the design choices that are working
well, and the system risks that should be handled next. It does not change the
runtime contract by itself.

## Current Architecture

The production path is Telegram:

```text
Telegram user
  -> watchfacts-bot / app.telegram_bot
  -> WatchFactsSearchWorkflow
  -> scraper, parser, matcher, dedupe, scoring, similarity grouping
  -> suspicious issue detection, optional guarded AI refinement, SQLite cache
  -> generated result page when configured
  -> Telegram summary plus result-page link, with fallback listing batches
```

MCP remains a supporting integration path through `watchfacts-mcp` and
`app.mcp_server`, but it calls the same `WatchFactsSearchWorkflow` instead of
owning a separate search implementation. This is the most important
architectural boundary in the project: Telegram, MCP clients, diagnostics, and
tests should reuse the same deterministic runtime.

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

- Shared runtime keeps the production Telegram path and supporting MCP path from
  drifting apart.
- Core search remains deterministic: parser, matcher, dedupe, scoring, and
  grouping run locally and are covered by focused tests.
- MCP responses are structured enough for clients to answer without inventing
  result IDs, seller data, image URLs, source links, pagination state, or OpenWA
  links.
- SQLite is used for local query history, dedupe records, search cache, issue
  queues, and AI refinement suggestions without requiring an external database.
- The scraper respects the authenticated-browser boundary and fails clearly when
  browser state is missing or expired.
- Default `make deploy` encodes the intended production deployment path:
  pull, build, run checks, recreate `watchfacts-bot` and `watchfacts-mcp`.
- `make deploy-mcp` is used for MCP-only deploys; it pulls, builds, runs the
  MCP predeploy checks, and recreates the MCP service.

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
   burst of MCP searches can launch multiple Playwright browser sessions and
   WatchFacts requests at the same time. Telegram has
   `TELEGRAM_MAX_CONCURRENT_SEARCHES`; MCP needs an equivalent runtime guard.

   Implemented fix: search-mode runtime settings now include
   `SEARCH_MAX_CONCURRENT_SEARCHES`, defaulting to 1. The search workflow applies
   a semaphore only for `runtime_mode="search"`, so MCP and diagnostics
   are bounded while Telegram keeps its existing queue behavior. Identical-query
   coalescing still happens before the concurrency gate.

4. Resolved: narrow `create_chat_draft` response exposure.

   `watchfacts_create_chat_draft_payload()` returns the full OpenWA draft payload
   to the MCP caller, including raw listing fields. The OpenWA API needs the
   payload, but MCP clients generally should not receive raw listing text unless a
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
MCP contract change and verified against client behavior.

## Verification Snapshot

Review-time verification:

```text
make check
make mcp-smoke
```

Result:

```text
499 passed, 1 warning
SUMMARY httpx_passed=1/1
```

The stale rank follow-up issue described above is fixed by
`tests/test_tool_runtime.py::test_watchfacts_create_chat_draft_rank_uses_latest_cached_result`.
Search runtime backpressure is covered by
`tests/test_search.py::test_search_workflow_limits_search_runtime_concurrent_distinct_queries`.
The MCP Docker healthcheck is covered by
`tests/test_docker_compose.py::test_watchfacts_mcp_service_has_lightweight_healthcheck`.
