# Search Engine Upgrade Plan

## Status

Planned.

## Date

2026-06-13

## Objective

Upgrade the deterministic WatchFacts search engine across three boundaries:

1. Query recognition: understand what the user means before searching.
2. Retrieval: fetch enough WatchFacts candidates without over-trusting the
   WatchFacts server query semantics.
3. Parsing and segmentation: convert noisy WatchFacts posts into item-level
   candidates that can be matched, ranked, and displayed safely.

The goal is better recall and faster repeated searches without reducing result
quality. Core matching, parsing, and ranking must remain deterministic. Do not
introduce LLM matching or semantic search into the core WatchFacts path.

## Current Evidence

Recent production audits showed that equivalent material queries can have very
different recall when the raw user wording is passed through too literally:

```text
rm07-01 rosegold          -> 29 results
rm07-01 rose gold         -> 3 results before canonical phrase folding
rm07-01 wg                -> 16 results
rm07-01 white gold        -> 1 result before canonical phrase folding
rm07-01 mop               -> 6 results
rm07-01 mother of pearl   -> 1 result before canonical phrase folding
```

The shipped compound-material fix canonicalizes:

```text
rose gold       -> rg
white gold      -> wg
mother of pearl -> mop
```

and bumps the search cache version so stale phrase-query results are not reused.
That patch fixes a narrow class of alias misses. This plan generalizes the same
principle across query recognition, retrieval planning, and parser scoping.

## Design Principles

- Keep core search deterministic and auditable.
- Prefer broad retrieval plus strict local filtering over narrow server search
  that can miss aliases.
- Keep the matcher responsible for eligibility, not ranking.
- Keep ranking responsible for ordering eligible results, not admitting weak
  matches.
- Treat stock-list parsing and image attribution as confidence-bearing
  extraction decisions.
- Back every behavior change with production audit evidence and regression
  tests.
- Avoid project bloat: add a module only when it replaces logic currently
  duplicated across parser, matcher, search, scoring, or diagnostics.

## Target Flow

```text
raw user query
  -> query recognition
       references
       canonical descriptors
       optional descriptors
       conflict groups
       query plan
  -> retrieval planning
       server query or query set
       canonical cache key
       local filter query
  -> WatchFacts authenticated fetch
  -> parser candidates
  -> stock-list item segments
  -> deterministic matcher eligibility
  -> dedupe
  -> feature-based ranking
  -> result page / Telegram / MCP payload
```

## Phase 1: Query Recognition V2

### Task 1.1: Introduce Descriptor Rulebook

Description: Move descriptor aliases, compound phrases, semantic groups, and
conflict rules into one deterministic rulebook.

Initial descriptor groups:

| Canonical | Aliases and phrases | Group | Conflicts |
| --- | --- | --- | --- |
| `rg` | `rg`, `rosegold`, `rose-gold`, `rose gold` | material | `wg` |
| `wg` | `wg`, `whitegold`, `white-gold`, `white gold` | material | `rg` |
| `mop` | `mop`, `motherofpearl`, `mother-of-pearl`, `mother of pearl` | dial/material detail | none initially |
| `gray` | `gray`, `grey` | color | other color group members |
| `choco` | `choco`, `chocolate`, `cho` | color/dial | other color group members |
| `mete` | `mete`, `meteorite` | dial/material detail | none initially |

Acceptance:

- [ ] `parse_query_terms()` and `tokenize_query()` use the same canonical rulebook.
- [ ] `score_fuzzy_match()` and `result_scoring` use the same canonical rulebook.
- [ ] `rg`, `rosegold`, and `rose gold` produce the same canonical descriptor.
- [ ] Conflicting material descriptors are explicit metadata, not ad-hoc string checks.
- [ ] Existing public import paths remain stable.

Verification:

```bash
python -m pytest tests/test_matcher.py tests/test_fuzzy_diagnostics.py tests/test_result_scoring.py
python -m compileall app scripts
```

Likely files:

- `app/searching/matcher_aliases.py`
- `app/searching/matcher_token_classification.py`
- `app/searching/matcher_normalization.py`
- `tests/test_matcher.py`
- `tests/test_fuzzy_diagnostics.py`
- `tests/test_result_scoring.py`

### Task 1.2: Add QueryPlan Metadata

Description: Add a small query planning structure that records the normalized
query intent without changing retrieval behavior yet.

Suggested fields:

| Field | Meaning |
| --- | --- |
| `original_query` | Raw user query |
| `canonical_query` | Query after phrase and alias folding |
| `references` | Parsed reference terms |
| `required_descriptors` | Descriptor tokens required for eligibility |
| `optional_descriptors` | Year/date/condition tokens treated as soft signals |
| `conflict_descriptors` | Canonical descriptors that should reject or demote results |
| `intent_kind` | Existing query-intent kind |
| `reason_codes` | Short explainable reason codes |

Acceptance:

- [ ] Search diagnostics expose safe query-plan fields.
- [ ] Audit JSONL emits query-plan fields for query summaries.
- [ ] No diagnostics expose secrets, cookies, CSRF tokens, full HTML, or raw
  WatchFacts response bodies.
- [ ] Existing MCP payload contract remains backward compatible.

Verification:

```bash
python -m pytest tests/test_search.py tests/test_audit_quality.py tests/test_mcp_smoke.py
python scripts/diagnostics/audit_quality.py "rm07-01 rose gold" --format jsonl --limit 1
```

Likely files:

- `app/searching/query_intent.py`
- `app/searching/search.py`
- `scripts/diagnostics/audit_quality.py`
- `tests/test_search.py`
- `tests/test_audit_quality.py`

## Phase 2: Retrieval Planner

### Task 2.1: Canonical Retrieval Cache Key

Description: Reuse cached retrieval for equivalent user phrasings while
preserving the original query in user-facing result ids and diagnostics.

Examples:

```text
rm07-01 rose gold       -> retrieval key: rm07-01 rg
rm07-01 rosegold        -> retrieval key: rm07-01 rg
rm07-01 rg              -> retrieval key: rm07-01 rg
rm07-01 mother of pearl -> retrieval key: rm07-01 mop
```

Acceptance:

- [ ] Equivalent descriptor aliases share the same retrieval cache key.
- [ ] Original query remains available for result ids, result pages, audit, and
  user-visible payloads.
- [ ] Cache invalidation is explicit through `SEARCH_CACHE_VERSION`.
- [ ] In-flight coalescing uses the retrieval key when safe.

Verification:

```bash
python -m pytest tests/test_search.py tests/test_db.py
python scripts/diagnostics/benchmark_mcp_queries.py \
  --query "rm07-01 rg" \
  --query "rm07-01 rose gold" \
  --repeat 2 \
  --format markdown \
  --allow-empty
```

Likely files:

- `app/searching/search.py`
- `app/infrastructure/search_cache_repository.py`
- `tests/test_search.py`
- `tests/test_db.py`

### Task 2.2: Retrieval Policy By Query Intent

Description: Generate a bounded retrieval plan based on query shape. Keep
WatchFacts fetching broad enough for recall, then rely on strict local filtering
for correctness.

Policy sketch:

| Intent | Retrieval strategy | Local filter |
| --- | --- | --- |
| reference only | Fetch reference query | Reference eligibility |
| reference + safe alias descriptor | Fetch canonical reference query | Required canonical descriptor |
| reference + multiple descriptors | Fetch reference or reduced canonical query | All required descriptors local to reference |
| reference + optional year | Fetch exact, optionally expand without year when under threshold | Year is soft/demote, not hard reject |
| brand/model text | Prefer raw query | Descriptor/model term eligibility |

Acceptance:

- [ ] `rm07-01 rg snow` returns only RG/Rosegold Snow, not WG Snow.
- [ ] `rm07-01 rose gold` and `rm07-01 rosegold` have equivalent recall after
  cache refresh.
- [ ] Retrieval query count is bounded and visible in diagnostics.
- [ ] Empty or low-result retrieval can expand only through documented rules.

Verification:

```bash
python -m pytest tests/test_search.py tests/test_mcp_smoke.py
python scripts/diagnostics/audit_quality.py \
  "rm07-01 rg snow" \
  "rm07-01 rose gold" \
  "rm07-01 white gold" \
  --limit 5
```

Likely files:

- `app/integrations/watchfacts_http.py`
- `app/searching/search.py`
- `tests/test_search.py`
- `tests/test_scraper.py`

## Phase 3: Parser And Segmenter V2

### Task 3.1: Item-Level Stock-List Segmentation

Description: Split noisy WatchFacts stock-list posts into item-level candidates
before final matching and display.

Target patterns:

- emoji bullets
- numbered list items
- repeated reference tokens
- price/date/condition boundaries
- mixed brand/model stock cards
- parent post with one image and multiple product segments

Acceptance:

- [ ] Parser emits item segments with bounded text snippets.
- [ ] Segment records keep parent source, seller, posted date, and image metadata.
- [ ] Segment text does not leak unrelated neighboring product references.
- [ ] `scope.stock_list` remains visible when confidence is lower.

Verification:

```bash
python -m pytest tests/test_parser.py tests/test_search.py tests/test_audit_quality.py
python scripts/diagnostics/audit_quality.py \
  "rm07-01 white ceramic" \
  "rm07-01 mop" \
  "rm037 white ceramic" \
  --limit 8
```

Likely files:

- `app/searching/parser.py`
- `app/searching/matcher_rules.py`
- `app/searching/search.py`
- `tests/test_parser.py`
- `tests/test_search.py`

### Task 3.2: Attach Price, Date, Condition, And Image Confidence

Description: Keep item-local price/date/condition evidence attached to the
selected segment. Do not borrow evidence from adjacent products.

Suggested confidence fields:

| Field | Meaning |
| --- | --- |
| `scope_reason` | `scope.full_listing`, `scope.scoped`, `scope.stock_list` |
| `image_reason` | `image.direct`, `image.inherited_parent_reference`, `image.omitted_bundle_ambiguous`, `image.missing_source` |
| `price_reason` | `price.visible`, `price.missing_visible`, `price.ambiguous_neighbor` |
| `segment_reason_codes` | Why the segment boundaries were chosen |

Acceptance:

- [ ] Price evidence belongs to the selected segment.
- [ ] Ambiguous parent images are omitted rather than shown incorrectly.
- [ ] Audit output explains segment/image/price decisions.
- [ ] Result-page and MCP result schemas remain backward compatible.

Verification:

```bash
python -m pytest tests/test_parser.py tests/test_result_scoring.py tests/test_audit_quality.py
python scripts/diagnostics/audit_quality.py "rm07-01 mother of pearl" --limit 8
```

Likely files:

- `app/searching/parser.py`
- `app/searching/search.py`
- `app/searching/result_scoring.py`
- `scripts/diagnostics/audit_quality.py`

## Phase 4: Ranking And Guardrails

### Task 4.1: Feature-Based Ranking Reasons

Description: Make ranking use explicit deterministic features, not hidden tuple
ordering or incidental source order.

Feature set:

- exact reference
- descriptor locality
- alias confidence
- conflict penalty
- scope confidence
- image confidence
- price evidence
- posted date
- original WatchFacts order

Acceptance:

- [ ] Ranking features are visible in `ResultScore.reasons` or audit fields.
- [ ] Missing price remains demoted below clean priced results.
- [ ] Stock-list scoped results can rank below full listings when all else is equal.
- [ ] Ranking never admits a listing that matcher rejected.

Verification:

```bash
python -m pytest tests/test_result_scoring.py tests/test_search.py
python scripts/diagnostics/audit_quality.py --limit 5
```

Likely files:

- `app/searching/result_scoring.py`
- `app/searching/search.py`
- `tests/test_result_scoring.py`
- `tests/test_search.py`

### Task 4.2: Conflict Guardrails

Description: Use query-plan conflict groups to reject or demote conflicting
item-local descriptors.

Examples:

```text
query: rm07-01 rg snow
reject/demote item-local: wg snow

query: rm07-01 white ceramic
reject/demote item-local: black ceramic
```

Acceptance:

- [ ] Conflict checks apply only when the conflicting descriptor is local to the
  same reference/item segment.
- [ ] Reference-only queries do not inherit descriptor conflict filtering.
- [ ] Audit emits conflict reason codes.

Verification:

```bash
python -m pytest tests/test_matcher.py tests/test_search.py tests/test_audit_quality.py
```

Likely files:

- `app/searching/matcher_rules.py`
- `app/searching/result_scoring.py`
- `app/searching/search.py`

## Phase 5: Evaluation And Deploy Gates

### Task 5.1: Alias-Pair Benchmark Set

Description: Keep a focused benchmark for equivalent query phrasings.

Initial query pairs:

```text
rm07-01 rg
rm07-01 rosegold
rm07-01 rose gold
rm07-01 wg
rm07-01 white gold
rm07-01 mop
rm07-01 mother of pearl
rm07-01 rg snow
rm07-01 rose gold snow
```

Acceptance:

- [ ] Benchmark reports total count, top result, cache hit, and stage timings.
- [ ] Alias-equivalent queries have comparable recall after canonicalization.
- [ ] Benchmark output can be saved as JSONL for later comparison.

Verification:

```bash
python scripts/diagnostics/benchmark_mcp_queries.py \
  --query "rm07-01 rg" \
  --query "rm07-01 rose gold" \
  --query "rm07-01 wg" \
  --query "rm07-01 white gold" \
  --query "rm07-01 mop" \
  --query "rm07-01 mother of pearl" \
  --format markdown \
  --allow-empty
```

Likely files:

- `scripts/diagnostics/benchmark_mcp_queries.py`
- `docs/operations.md`

### Task 5.2: Search Engine Deploy Checklist

Description: Add a focused deploy gate for search-engine changes.

Gate:

```bash
python -m pytest -q
python -m compileall app scripts
git diff --check
python scripts/diagnostics/audit_quality.py \
  "rm07-01 rg snow" \
  "rm07-01 rose gold" \
  "rm07-01 white gold" \
  "rm07-01 mother of pearl" \
  --limit 5
make mcp-smoke-set
```

Acceptance:

- [ ] Search engine changes include before/after evidence for affected query classes.
- [ ] Deploy notes mention cache-version bumps when behavior changes cached output.
- [ ] MCP smoke passes after deploy.
- [ ] Bot deploy is blocked only by configuration issues such as missing
  `TELEGRAM_BOT_TOKEN`, not by code errors.

Verification:

```bash
git diff --check
```

Likely files:

- `docs/operations.md`
- `docs/search-engine-upgrade-plan.md`

## Metrics

Track these before and after each phase:

| Metric | Target |
| --- | --- |
| alias-equivalent recall delta | Near zero for canonical equivalents |
| hot-cache MCP latency | Usually below 100 ms for cached searches |
| cold-path WatchFacts fetch latency | Recorded, not regressed without reason |
| weak match rate | Does not increase |
| ambiguous candidate rate | Decreases or becomes better explained |
| stock-list scoped rate | Decreases for queries improved by segmentation |
| missing image rate | Does not improve by showing wrong images |
| validation errors | Zero |

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Alias expansion admits false positives | High | Use conflict groups and local descriptor checks |
| Broad retrieval increases latency | Medium | Canonical cache key, in-flight coalescing, bounded retrieval plans |
| Parser segmentation drops valid neighboring text | Medium | Keep parent raw preview in audit and add regression fixtures |
| Ranking hides valid but incomplete results | Medium | Demote with reasons rather than delete unless matcher rejects |
| Project grows too many modules | Medium | Add modules only when replacing duplicated logic |
| Cached stale results mask improvements | Medium | Bump `SEARCH_CACHE_VERSION` for behavior-changing search patches |

## Non-Goals

- Replacing deterministic matching with LLM or semantic ranking.
- Letting MCP clients reimplement WatchFacts search in prompts.
- Fetching unbounded WatchFacts pages for one query.
- Showing ambiguous parent images just to reduce missing-image rate.
- Rewriting the whole parser, matcher, and ranking stack in one change.

## Recommended Next Step

Start with Phase 1 Task 1.1 and Task 1.2. Query recognition is the foundation
for retrieval planning and parser/ranking guardrails, and it has the smallest
runtime risk when introduced with diagnostics first.
