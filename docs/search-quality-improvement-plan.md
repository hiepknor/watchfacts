# Search Quality Improvement Plan

## Status

Planned.

## Date

2026-06-11

## Objective

Improve user-visible WatchFacts search quality over one month without changing
the core architecture. The priority is result quality, especially missing
images, noisy stock-list cards, and incomplete scoped listing text.

Core constraints:

- Keep search deterministic by default.
- Do not make OpenAI or any LLM part of core extraction, matching, or ranking.
- Preserve the existing MCP tool names and payload contract.
- Prefer omitting ambiguous images over showing a wrong product image.
- Keep all fixes backed by focused regression tests and production audit output.

## Success Criteria

- Representative audit queries show lower or better-explained
  `image_missing_count`, especially for color/reference queries and stock-list
  cards.
- Stock-list and multi-listing extraction has regression coverage for observed
  production patterns.
- Quality audit output explains image and scoping decisions clearly enough for a
  maintainer to create a targeted test.
- MCP smoke continues to validate `result_id`, `stable_listing_id`, pagination,
  and safe result fields.
- Server deploy passes `pytest`, `compileall`, quality audit, MCP health, Hermes
  restart, and MCP smoke set.

## Baseline Query Set

Use this bounded query set for before/after comparisons:

```text
5712g
15510or blue
15510 or blue
5205r green
5726/1a
116500 panda
7118/1200a grey
```

Additional default audit queries from `scripts/diagnostics/audit_quality.py`
remain useful for deploy gates, but this set is the quality-improvement
baseline.

## Work Plan

### Week 1: Audit Visibility

Extend the production quality audit so result-quality failures are actionable,
not just visible.

Required output additions:

- `has_image`
- `image_reason`
- `scope_reason`
- `server_filtered`
- `raw_listing_preview`
- `stable_listing_id`

Required audit summary:

- image missing rate by query
- count of server-filtered results
- count of scoped stock-list results
- top suspicious reasons
- top image omission reasons

Rules:

- Never print cookies, browser state, `.env`, full page HTML, or unbounded raw
  listings.
- Keep raw snippets bounded and redacted through existing diagnostic safety
  patterns.
- Add tests for audit formatting and no-secret guarantees.

### Week 2: Image Attribution

Move image attribution into one deterministic boundary with explicit reason
codes.

Required reason codes:

```text
image.direct
image.inherited_parent_color
image.inherited_parent_reference
image.omitted_bundle_ambiguous
image.missing_source
```

Decision rules:

- Use a nested listing's own image when present.
- Inherit a parent image only when parent metadata strongly matches the scoped
  item by color or reference.
- Omit a parent image when the parent appears to be a stock-list or bundle cover
  and the scoped item cannot be tied to that image.
- Preserve the current safety posture: wrong image is worse than missing image.

Required tests:

- direct nested image is used
- parent color image is inherited only for matching-color variants
- parent reference image is inherited only for matching-reference variants
- ambiguous bundle image is omitted
- missing source image produces `image.missing_source`

### Week 3: Stock-List Scoping

Improve deterministic scoping for stock-list and multi-listing cards.

Target patterns:

- one card with many references and one `frontImage`
- nested JSON variants with `dialColor`
- parent title that contains multiple product segments
- server-filtered JSON where local scoping extracts one product segment

Expected behavior:

- Preserve the selected reference segment.
- Keep nearby price, year, condition, and color text when available.
- Do not leak unrelated product segments into the shown `listing_text`.
- Flag incomplete scoped segments rather than hiding them.

Suspicious patterns to cover:

- currency without amount
- reference-only fragment
- segment truncated at seller/member metadata boundary
- scoped stock-list result missing visible price evidence

### Week 4: Verification And Deploy

Run full verification and record before/after evidence.

Local verification:

```bash
python -m pytest -q
python -m compileall app scripts
make mcp-smoke
make quality-audit
```

Server deploy verification:

```bash
make deploy-hermes-mcp
```

Deployment must confirm:

- `watchfacts-mcp` is healthy
- Hermes restarts successfully
- representative MCP smoke set passes
- audit output does not expose secrets

Update `docs/system-design-review.md` after deploy with:

- before/after image missing rates for the baseline query set
- stock-list scoping improvements shipped
- known remaining gaps
- next recommended priority

## Interface And Compatibility Rules

Do not rename or remove MCP tools:

```text
search
health
create_chat_draft
report_issue
list_issues
get_issue
update_issue
suspicious_summary
```

Do not remove existing search result fields:

```text
result_id
stable_listing_id
rank
listing_text
seller
posted_date
source_url
image_url
offset
next_offset
has_more
```

Follow-up tools must continue accepting:

- short-lived `result_id`
- returned `stable_listing_id`
- absolute `rank`

## Test Matrix

Required automated coverage:

- audit output includes image/scope reason fields
- audit output bounds and redacts raw context
- image attribution reason codes cover direct, inherited, omitted, and missing
  cases
- parser/search preserve scoped stock-list segments
- scoring demotes ambiguous or incomplete scoped stock-list results without
  hiding them
- MCP smoke requires `stable_listing_id`
- OpenWA handoff still resolves by `result_id`, `stable_listing_id`, and rank

Required production checks:

- one live query analysis for `5712g`
- one color-specific query analysis for `15510or blue`
- one MCP smoke set after Hermes restart

## Non-Goals

- Replacing deterministic matching with an LLM.
- Adding uncontrolled AI ranking or summarization.
- Rewriting Hermes prompts to duplicate WatchFacts search logic.
- Changing WatchFacts authentication boundaries.
- Moving away from SQLite or single-server deployment in this phase.
- Showing a product image when attribution is ambiguous.
