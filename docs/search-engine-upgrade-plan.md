# Search Engine Upgrade Plan

## Status

In progress.

## Date

2026-06-13

## Objective

Upgrade the deterministic WatchFacts search engine across four pipeline
boundaries:

1. Query recognition: understand what the user means before searching.
2. Retrieval: fetch enough WatchFacts candidates without over-trusting the
   WatchFacts server query semantics.
3. Candidate processing: convert noisy WatchFacts posts into item-level
   candidates, then match, dedupe, and rank them deterministically.
4. Result delivery: format ranked results for Telegram, MCP, result pages, and
   follow-up actions without embedding search logic in presentation code.

The goal is better recall and faster repeated searches without reducing result
quality. Core matching, parsing, and ranking must remain deterministic. Do not
introduce LLM matching or semantic search into the core WatchFacts path.

The upgrade must work beyond one Richard Mille hard case. Brand-specific terms,
references, collections, and nicknames should be recognized through small
deterministic rulebooks layered on top of global descriptor rules, not through
one-off patches for each query.

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

The same failure mode can happen for other brands:

```text
daytona panda        -> Rolex Daytona white/black dial context
126500ln white       -> Rolex Daytona reference plus dial color
5711 blue            -> Patek Philippe Nautilus reference plus dial color
15500st blue         -> Audemars Piguet Royal Oak reference plus dial color
```

These cases should share the same recognition, retrieval, and filtering
machinery. The only brand-specific part should be compact taxonomy data and
reference grammar.

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
- Separate global descriptors from brand taxonomies. Material, color, condition,
  set, size, and year rules should be reusable across Rolex, Patek Philippe,
  Audemars Piguet, Richard Mille, and future brands.
- Keep brand-specific logic data-driven where practical: brand aliases,
  collection names, nickname expansions, and reference grammar should live in a
  small rulebook instead of scattered `if brand == ...` branches.
- Use recall-first retrieval and precision-first local matching. Retrieval may
  broaden through documented aliases or nicknames, but matcher eligibility must
  remain strict and explainable.
- Treat `WatchFacts Search Engine` as the system-level name. Inside the code and
  docs, prefer precise pipeline-stage names over naming every component an
  engine.
- Avoid project bloat: add a module only when it replaces logic currently
  duplicated across parser, matcher, search, scoring, or diagnostics.

## Pipeline Naming And Boundaries

Use these stage names in docs, diagnostics, and future refactors:

| Stage | Input | Output | Owns |
| --- | --- | --- | --- |
| Query Recognition | Raw user query | `QueryPlan` | Brand, reference, collection, nickname, descriptor, optional token, and conflict recognition |
| Retrieval | `QueryPlan` | Raw WatchFacts candidate batch | Retrieval policy, bounded expansion, authenticated fetch, cache key, and in-flight coalescing |
| Candidate Processing | Candidate batch plus `QueryPlan` | Ranked eligible results | Parse, segment, match eligibility, dedupe, score, rank, and quality reason codes |
| Result Delivery | Ranked eligible results | Telegram, MCP, result page, or action payload | Pagination, `result_id`, payload formatting, result-page rendering, OpenWA handoff, and report actions |

The preferred contract is:

```text
raw_query -> QueryPlan -> CandidateBatch -> RankedResults -> ResponsePayload
```

Naming guidance:

- Use `WatchFacts Search Engine` for the whole deterministic search system.
- Avoid naming every boundary `*Engine`; it makes ownership less clear and
  encourages broad modules.
- Prefer concrete component names when code is refactored:
  `QueryRecognizer`, `RetrievalPlanner`, `CandidateRetriever`,
  `ListingParser`, `CandidateMatcher`, `ResultRanker`, and `ResultPresenter`.
- Do not do a bulk rename before Phase 1. Introduce names as each phase creates
  or moves real behavior.
- Telegram handlers, MCP tools, and result pages belong to Result Delivery.
  They must call the shared runtime instead of reimplementing recognition,
  retrieval, parsing, matching, or ranking.

## Target Flow

```text
raw user query
  -> Query Recognition
       brand candidates
       references
       collections/models
       nicknames
       canonical descriptors
       optional descriptors
       conflict groups
       query plan
  -> Retrieval
       server query or query set
       canonical cache key
       local filter query
  -> WatchFacts authenticated fetch
  -> Candidate Processing
  -> parser candidates
  -> stock-list item segments
  -> deterministic matcher eligibility
  -> dedupe
  -> feature-based ranking
  -> Result Delivery
       result page / Telegram / MCP payload
```

## Phase 1: Query Recognition V2

### Task 1.1: Introduce Descriptor Rulebook

Description: Move descriptor aliases, compound phrases, semantic groups, and
conflict rules into one deterministic global rulebook. This layer is brand
agnostic.

Initial descriptor groups:

| Canonical | Aliases and phrases | Group | Conflicts |
| --- | --- | --- | --- |
| `rg` | `rg`, `rosegold`, `rose-gold`, `rose gold` | material | `wg` |
| `wg` | `wg`, `whitegold`, `white-gold`, `white gold` | material | `rg` |
| `mop` | `mop`, `motherofpearl`, `mother-of-pearl`, `mother of pearl` | dial/material detail | none initially |
| `gray` | `gray`, `grey` | color | other color group members |
| `choco` | `choco`, `chocolate`, `cho` | color/dial | other color group members |
| `mete` | `mete`, `meteorite` | dial/material detail | none initially |
| `fullset` | `fullset`, `full set`, `complete set` | set | none initially |
| `nos` | `nos`, `new old stock` | condition | none initially |

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
query intent without changing retrieval behavior yet. The structure should be
brand-aware, but not brand-coupled: missing brand data must not block reference
and descriptor matching.

Suggested fields:

| Field | Meaning |
| --- | --- |
| `original_query` | Raw user query |
| `canonical_query` | Query after phrase and alias folding |
| `brand_candidates` | Detected brand aliases with confidence and source terms |
| `references` | Parsed reference terms |
| `collections` | Collection/model names such as Daytona, Nautilus, Royal Oak |
| `nicknames` | Nickname expansions such as panda, pepsi, batman, sprite, hulk, root beer |
| `required_descriptors` | Descriptor tokens required for eligibility |
| `optional_descriptors` | Year/date/condition tokens treated as soft signals |
| `conflict_descriptors` | Canonical descriptors that should reject or demote results |
| `intent_kind` | Existing query-intent kind |
| `reason_codes` | Short explainable reason codes |

Acceptance:

- [x] Search diagnostics expose safe query-plan fields.
- [x] Audit JSONL emits query-plan fields for query summaries.
- [x] No diagnostics expose secrets, cookies, CSRF tokens, full HTML, or raw
  WatchFacts response bodies.
- [x] Existing MCP payload contract remains backward compatible.

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

### Task 1.3: Add Brand Taxonomy And Reference Grammar

Description: Add a compact deterministic brand taxonomy for the highest-value
WatchFacts brands. This should improve recognition without turning the codebase
into a large catalog project.

Initial taxonomy shape:

| Layer | Examples | Purpose |
| --- | --- | --- |
| brand aliases | `rolex`, `patek`, `patek philippe`, `ap`, `audemars piguet`, `rm`, `richard mille` | Identify likely brand context |
| collections | `daytona`, `submariner`, `gmt`, `nautilus`, `aquanaut`, `royal oak`, `offshore` | Add model/family context |
| nicknames | `panda`, `pepsi`, `batman`, `batgirl`, `sprite`, `hulk`, `starbucks`, `root beer` | Expand common market shorthand |
| reference grammar | `126500ln`, `116500ln`, `5711`, `5712`, `5167a`, `15500st`, `15510st`, `rm07-01` | Detect references consistently |

Rulebook organization:

```text
global descriptor rules
  material/color/dial/condition/set/year/size
brand taxonomy rules
  brand aliases
  collections
  nicknames
  reference grammar
query plan builder
  merges both layers into one deterministic plan
```

Initial brand examples:

```text
126500ln white
  brand_candidates: rolex
  references: 126500ln
  collections: daytona
  required_descriptors: white

daytona panda
  brand_candidates: rolex
  collections: daytona
  nicknames: panda
  required_descriptors: white, black-context

5711 blue
  brand_candidates: patek philippe
  references: 5711
  collections: nautilus
  required_descriptors: blue

15500st blue
  brand_candidates: audemars piguet
  references: 15500st
  collections: royal oak
  required_descriptors: blue
```

Acceptance:

- [x] Brand aliases, collections, nicknames, and reference grammar are defined in
  data structures, not scattered conditional branches.
- [x] Unknown brands still flow through the global descriptor/reference matcher.
- [x] QueryPlan diagnostics show which terms came from global rules versus
  brand taxonomy rules.
- [x] Nickname expansions are explainable and can be locally filtered to avoid
  broad false positives.
- [x] The taxonomy starts with only high-value brands observed in audits, then
  expands through benchmark evidence.

Verification:

```bash
python -m pytest tests/test_matcher.py tests/test_search.py tests/test_audit_quality.py
python scripts/diagnostics/audit_quality.py \
  "126500ln white" \
  "daytona panda" \
  "5711 blue" \
  "15500st blue" \
  --limit 5
```

Likely files:

- `app/searching/matcher_rulebook.py`
- `app/searching/query_intent.py`
- `app/searching/matcher_token_classification.py`
- `tests/test_matcher.py`
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

- [x] Equivalent descriptor aliases share the same retrieval cache key.
- [x] Original query remains available for result ids, result pages, audit, and
  user-visible payloads.
- [x] Cache invalidation is explicit through `SEARCH_CACHE_VERSION`.
- [x] In-flight coalescing uses the retrieval key when safe.

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
| brand/model text | Prefer raw query, optionally add known collection or nickname expansions | Descriptor/model term eligibility |
| brand nickname | Fetch bounded nickname/collection expansion | Nickname-expanded descriptor eligibility |
| reference + brand taxonomy descriptors | Fetch reference or canonical reference query | Brand/reference/descriptors local to item |

Acceptance:

- [x] `rm07-01 rg snow` returns only RG/Rosegold Snow, not WG Snow.
- [x] `rm07-01 rose gold` and `rm07-01 rosegold` have equivalent recall after
  cache refresh.
- [x] Retrieval query count is bounded and visible in diagnostics.
- [x] Empty or low-result retrieval can expand only through documented rules.

Implemented policy:

- Reference queries with required descriptors and no optional descriptors fetch
  the reference-only query, then use the original query for local eligibility.
- Reference queries with optional year/date descriptors keep the exact initial
  fetch and may expand through the existing documented no-year fallback.
- Diagnostics expose `retrieval_query_count`, `retrieval_queries`, and
  `retrieval_reason_codes`.

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

### Task 2.3: Multi-Brand Retrieval Expansion Rules

Description: Add bounded retrieval expansions for brand/model/nickname queries
after QueryPlan diagnostics are stable. The retrieval planner may add alternate
queries only when they are documented and locally filterable.

Examples:

```text
daytona panda
  retrieval candidates: "daytona panda", "daytona white", "126500ln white", "116500ln white"
  local filter: Daytona context plus white/black dial context or matching reference

5711 blue
  retrieval candidates: "5711 blue", "nautilus 5711 blue"
  local filter: 5711 reference plus blue descriptor

15500st blue
  retrieval candidates: "15500st blue", "royal oak 15500st blue"
  local filter: 15500st reference plus blue descriptor
```

Initial implementation slice:

- [x] `daytona panda` expands to at most four documented retrieval queries:
  raw query, `daytona white`, `126500ln white`, and `116500ln white`.
- [x] Multi-query nickname expansion uses strict local matcher eligibility, so
  black-dial Daytona listings fetched through broad server results do not pass.
- [x] Reference-only queries remain raw-query retrieval and do not inherit
  brand/model/nickname expansion behavior.
- [x] Reference-bearing nickname queries, such as `126500ln panda`, remain
  reference-scoped and do not expand to sibling references.
- [x] Patek Philippe `5711 blue` expands through raw query, `5711`, and
  `nautilus 5711 blue`, then uses strict local filtering so sibling references
  such as `5712 blue` do not pass.
- [x] Patek Philippe `5711 blue` expansion is skipped when extra query
  descriptors change intent, such as another brand or an unmodeled strap/detail
  term.
- [x] Audemars Piguet `15500st blue` expands through raw query, `15500st`,
  and `royal oak 15500st blue`, then uses strict local filtering so sibling
  references such as `15510st blue` do not pass.
- [x] Audemars Piguet `15500st blue` expansion is skipped when extra query
  descriptors change intent, such as another brand or an unmodeled strap/detail
  term.

Acceptance:

- [x] Retrieval expansions are bounded per query and visible in diagnostics.
- [x] Each expansion has a local eligibility rule before it can affect output.
- [x] Brand/model/nickname expansions do not change reference-only behavior.
- [x] Equivalent canonical queries share cache keys when safe; broad nickname
  expansions keep separate keys when they represent different retrieval intent.

Verification:

```bash
python -m pytest tests/test_search.py tests/test_audit_quality.py
python scripts/diagnostics/benchmark_mcp_queries.py \
  --query "126500ln white" \
  --query "daytona panda" \
  --query "5711 blue" \
  --query "15500st blue" \
  --format markdown \
  --allow-empty
```

Likely files:

- `app/searching/search.py`
- `app/searching/query_intent.py`
- `scripts/diagnostics/benchmark_mcp_queries.py`
- `tests/test_search.py`
- `tests/test_audit_quality.py`

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

Initial implementation slice:

- [x] JSON listing titles with explicit stock-list markers are split into
  item-level candidates using product-reference boundaries.
- [x] Stock-list item candidates keep parent seller, posted date, source URL,
  seller phone, and source image metadata.
- [x] Stock-list item candidates preserve the full parent title as raw context
  so audit and quality checks can still identify `scope.stock_list`.

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

Initial implementation slice:

- [x] `SearchResult` can carry optional `scope_reason`, `image_reason`,
  `price_reason`, and `segment_reason_codes` metadata without changing required
  MCP/result-page fields.
- [x] JSON stock-list item segments attach stock-list scope and segment boundary
  reason codes at parse time.
- [x] Price scoring distinguishes segment-local visible prices from parent-only
  neighboring prices via `price.ambiguous_neighbor`.
- [x] Search cache, result-reference cache, MCP payloads, result-page sidecars,
  and audit reports preserve evidence metadata when present.

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

Initial implementation slice:

- [x] `ResultScore` exposes explicit deterministic ranking fields for alias
  confidence, conflict penalty, scope confidence, and image confidence.
- [x] Scope and image confidence are late tie-breakers after quality, date,
  reference, descriptor, and price evidence.
- [x] Stock-list scoped results rank below full-listing results when all prior
  ranking features are tied.
- [x] Audit scope/image reasoning now reuses the same deterministic helpers as
  ranking.

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

- [x] Conflict checks apply only when the conflicting descriptor is local to the
  same reference/item segment.
- [x] Reference-only queries do not inherit descriptor conflict filtering.
- [x] Audit emits conflict reason codes.

Initial implementation slice:

- [x] Result scoring now uses `QueryPlan.conflict_descriptors` for material
  conflicts such as `rg` versus `wg`.
- [x] Conflict demotion scans the matcher-selected local output text rather
  than the raw parent listing context.
- [x] Legacy color conflict demotion remains available, but now emits explicit
  `conflict.local_descriptor:*` reason codes.
- [x] Reference-only queries bypass descriptor conflict demotion.
- [x] `SEARCH_CACHE_VERSION` is bumped for behavior-changing ranking output.

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

Description: Keep a focused benchmark for equivalent query phrasings and
multi-brand recognition coverage.

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

Initial multi-brand queries:

```text
126500ln white
daytona panda
5711 blue
15500st blue
```

Acceptance:

- [ ] Benchmark reports total count, top result, cache hit, and stage timings.
- [ ] Alias-equivalent queries have comparable recall after canonicalization.
- [ ] Brand/model/nickname queries report recognized brand, collection,
  reference, descriptor, and retrieval expansion reason codes.
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
  --query "126500ln white" \
  --query "daytona panda" \
  --query "5711 blue" \
  --query "15500st blue" \
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
| brand-recognition coverage | Increases for audited high-value brands |
| nickname false-positive rate | Does not increase beyond benchmark threshold |
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
| Brand taxonomy grows into a full product catalog | Medium | Start with observed high-value aliases only; require benchmark evidence before adding new terms |
| Nickname expansion over-fetches unrelated listings | Medium | Bound retrieval expansion count and require local eligibility checks |
| Cached stale results mask improvements | Medium | Bump `SEARCH_CACHE_VERSION` for behavior-changing search patches |

## Non-Goals

- Replacing deterministic matching with LLM or semantic ranking.
- Letting MCP clients reimplement WatchFacts search in prompts.
- Fetching unbounded WatchFacts pages for one query.
- Building a complete watch reference encyclopedia.
- Guaranteeing every nickname for every brand before there is audit evidence.
- Showing ambiguous parent images just to reduce missing-image rate.
- Rewriting the whole parser, matcher, and ranking stack in one change.

## Recommended Next Step

Start with Phase 1 Task 1.1 and Task 1.2, then add Task 1.3 only for the
highest-value audited brands. Query recognition is the foundation for retrieval
planning and parser/ranking guardrails, and it has the smallest runtime risk
when introduced with diagnostics first.
