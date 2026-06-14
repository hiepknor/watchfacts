# Search Engine Upgrade Plan

## Status

MCP deployed and production-validated through Phase 7.

As of 2026-06-15, commit `47bf74d` is pushed to `origin/master` and
deployed to `watchfacts-mcp`. The Phase 7 search changes are active on the
server with `SEARCH_CACHE_VERSION=search-v30`.

## Date

2026-06-13

Last reviewed: 2026-06-15

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

## Deployment Validation Evidence

Latest validated deployment:

- Commit: `47bf74d`
- Service: `watchfacts-mcp`
- Date: 2026-06-15
- Worktree state after deploy: clean and synced with `origin/master`

Phase 7 predeploy gate:

- Server `make deploy-mcp` passed.
- Container pytest passed with `690 passed`.
- `python -m compileall app scripts` passed.
- Default production quality audit passed during deploy.

Deploy and postdeploy gate:

- `make deploy-mcp` rebuilt and recreated `watchfacts-mcp`; the service became
  healthy.
- `make search-engine-postdeploy-check` passed.
- MCP smoke passed `4/4`.
- Default MCP benchmark passed `13/13` with hot-cache average `154ms`, median
  `99ms`, p95 `739ms`, max `739ms`, and cache hits `13/13`.
- Alias recall delta was zero for the canonical groups covering `rm07-01 mop`,
  `rm07-01 rg`, `rm07-01 rg snow`, and `rm07-01 wg`.

Remaining performance observation:

- Hot-cache search is within target for the representative benchmark set.
- Cold expanded retrieval remains the next speed bottleneck: observed cold
  first-pass times were roughly `7.7s` for `126500ln white`, `28s` for
  `daytona panda`, `29.9s` for `5711 blue`, and `13.5s` for `15500st blue`.
  These timings did not fail the quality gate, but they should drive the next
  optimization phase.

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

Implemented descriptor groups:

| Canonical | Aliases and phrases | Group | Conflicts |
| --- | --- | --- | --- |
| `rg` | `rg`, `rosegold`, `rose-gold`, `rose gold` | material | `wg` |
| `wg` | `wg`, `whitegold`, `white-gold`, `white gold` | material | `rg` |
| `mop` | `mop`, `motherofpearl`, `mother-of-pearl`, `mother of pearl` | dial/material detail | none initially |
| `gray` | `gray`, `grey` | color | other color group members |
| `choco` | `choco`, `chocolate`, `cho` | color/dial | other color group members |
| `mete` | `mete`, `meteorite` | dial/material detail | none initially |

Future descriptor candidates:

| Canonical | Aliases and phrases | Group | Conflicts |
| --- | --- | --- | --- |
| `fullset` | `fullset`, `full set`, `complete set` | set | none initially |
| `nos` | `nos`, `new old stock` | condition | none initially |

`fullset` and `nos` are recognized in several parser, matcher, scoring, and
similarity contexts today, but they are not yet centralized in the descriptor
rulebook. Do not mark them as descriptor-rulebook coverage until the canonical
rulebook, query parsing, listing matching, diagnostics, and tests all use the
same representation.

Acceptance:

- [x] `parse_query_terms()` and `tokenize_query()` use the same canonical rulebook.
- [x] `score_fuzzy_match()` and `result_scoring` use the same canonical rulebook.
- [x] `rg`, `rosegold`, and `rose gold` produce the same canonical descriptor.
- [x] Conflicting material descriptors are explicit metadata, not ad-hoc string checks.
- [x] Existing public import paths remain stable.

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

- [x] Parser emits item segments with bounded text snippets.
- [x] Segment records keep parent source, seller, posted date, and image metadata.
- [x] Segment text does not leak unrelated neighboring product references.
- [x] `scope.stock_list` remains visible when confidence is lower.

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

- [x] Price evidence belongs to the selected segment.
- [x] Ambiguous parent images are omitted rather than shown incorrectly.
- [x] Audit output explains segment/image/price decisions.
- [x] Result-page and MCP result schemas remain backward compatible.

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

- [x] Ranking features are visible in `ResultScore.reasons` or audit fields.
- [x] Missing price remains demoted below clean priced results.
- [x] Stock-list scoped results can rank below full listings when all else is equal.
- [x] Ranking never admits a listing that matcher rejected.

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

- [x] Benchmark reports total count, top result, cache hit, and stage timings.
- [x] Alias-equivalent queries have comparable recall after canonicalization,
  enforced by a benchmark gate on canonical groups.
- [x] Brand/model/nickname queries report recognized brand, collection,
  reference, descriptor, and retrieval expansion reason codes.
- [x] Benchmark output can be saved as JSONL for later comparison.

Initial implementation slice:

- [x] `benchmark_mcp_queries.py` default queries now cover the alias-pair and
  multi-brand set listed in this task.
- [x] Benchmark rows include `canonical_query`, brand candidates, references,
  collections, nicknames, required/optional/conflict descriptors, retrieval
  queries, and retrieval reason codes from safe search diagnostics.
- [x] Text, markdown, and JSONL renderers expose those fields so recall can be
  compared by canonical query and retrieval expansion behavior can be reviewed
  after a run.
- [x] Text and markdown reports include an `Alias Recall` comparison that groups
  rows by `canonical_query` and fails the benchmark when `total_count` differs
  by more than 10% within an alias group.

Verification:

```bash
python scripts/diagnostics/benchmark_mcp_queries.py \
  --query "rm07-01 rg" \
  --query "rm07-01 rose gold" \
  --query "rm07-01 wg" \
  --query "rm07-01 white gold" \
  --query "rm07-01 mop" \
  --query "rm07-01 mother of pearl" \
  --query "rm07-01 rg snow" \
  --query "rm07-01 rose gold snow" \
  --query "126500ln white" \
  --query "daytona panda" \
  --query "5711 blue" \
  --query "15500st blue" \
  --format markdown \
  --require-alias-recall \
  --allow-empty
```

Likely files:

- `scripts/diagnostics/benchmark_mcp_queries.py`
- `docs/operations.md`

### Task 5.2: Search Engine Deploy Checklist

Description: Add a focused deploy gate for search-engine changes.

Gate:

```bash
make search-engine-predeploy-check
make deploy-mcp
make search-engine-postdeploy-check
```

`search-engine-predeploy-check` runs `git diff --check`, the full test suite,
`compileall`, and the focused hard-case audit set. `search-engine-postdeploy-check`
runs MCP smoke plus the default MCP benchmark, including the alias-recall gate
from Task 5.1. Use `search-engine-deploy-check` only when the MCP service is
already deployed and you want to run both gates against the current checkout and
running service without recreating containers.

Acceptance:

- [x] Search engine changes include before/after evidence for affected query classes.
- [x] Deploy notes mention cache-version bumps when behavior changes cached output.
- [x] MCP smoke passes after deploy.
- [x] Bot deploy is blocked only by configuration issues such as missing
  `TELEGRAM_BOT_TOKEN`, not by code errors.

Initial implementation slice:

- [x] `Makefile` exposes `search-engine-predeploy-check`,
  `search-engine-postdeploy-check`, and `search-engine-deploy-check` targets.
- [x] The predeploy gate runs `git diff --check`, the full test suite,
  `compileall`, and a focused hard-case audit for alias/material query classes.
- [x] The postdeploy gate runs MCP smoke plus the default MCP benchmark, which
  includes the alias-recall gate from Task 5.1.
- [x] `docs/operations.md` documents before/after JSONL evidence, cache-version
  note requirements, postdeploy MCP verification, and the bot deploy blocker
  boundary.

Verification:

```bash
python -m pytest tests/test_makefile.py -q
git diff --check
```

Likely files:

- `Makefile`
- `tests/test_makefile.py`
- `docs/operations.md`
- `docs/search-engine-upgrade-plan.md`

## Phase 6: Cold-Path Retrieval Speed Optimization

Status: complete and deployed.

Goal: reduce first-pass latency for bounded multi-query retrieval expansions
without reducing recall, weakening local matcher eligibility, or adding broad
new abstractions.

Initial baseline from the 2026-06-14 MCP deploy:

| Query | Observed cold first-pass latency | Result count |
| --- | ---: | ---: |
| `126500ln white` | ~7.7s | 16 |
| `daytona panda` | ~28s | 211 |
| `5711 blue` | ~29.9s | 28 |
| `15500st blue` | ~13.5s | 6 |

Non-negotiables:

- Do not reduce result count or top-result quality for the Phase 5 benchmark
  set.
- Do not remove strict local eligibility checks to make retrieval faster.
- Do not fetch unbounded WatchFacts pages.
- Do not add another generic `engine` layer unless measurements show that an
  existing boundary is doing two unrelated jobs.
- Keep hot-cache latency at or below the current benchmark profile.

### Task 6.1: Per-Retrieval-Query Timing Trace

Description: Make cold benchmark output show timing per retrieval subquery and
which subqueries were cache hits, cache misses, empty, or dominant latency
contributors.

Acceptance:

- [x] Search diagnostics and benchmark output can explain which retrieval
  expansion consumed the most cold-path time.
- [x] A supported cold-run control exists for benchmarks, such as a documented
  cache reset command or benchmark flag; do not rely on ad-hoc environment
  variables that the Makefile or benchmark script does not read.
- [x] Diagnostics do not expose cookies, session state, full HTML, or raw
  WatchFacts response bodies.
- [x] Existing MCP payload schema remains backward compatible.

Initial implementation slice:

- [x] `SearchDiagnostics` emits `retrieval_timings` with query, cache status,
  fetch/parse/match/total timings, parsed/matched counts, empty status,
  server-filter/fallback flags, reason codes, and one dominant latency branch.
- [x] `benchmark_mcp_queries.py` extracts and renders retrieval timing summaries
  in text, markdown, and JSONL reports.
- [x] `benchmark_mcp_queries.py --clear-search-cache` clears local
  `search_cache` and `result_reference_cache` rows before each query run, and
  `make mcp-benchmark` can pass it through with `MCP_BENCHMARK_EXTRA_ARGS`.

Suggested verification:

```bash
python -m pytest tests/test_benchmark_mcp_queries.py tests/test_search.py
make mcp-benchmark
make mcp-benchmark MCP_BENCHMARK_EXTRA_ARGS=--clear-search-cache
```

### Task 6.2: Bounded Parallel Retrieval Evaluation

Description: Evaluate parallel fetching for independent retrieval expansions
with a small fixed concurrency cap. Preserve deterministic merge order after
fetching, and keep the local matcher as the only eligibility gate.

Acceptance:

- [x] Cold latency improves for at least two of the multi-brand baseline
  queries.
- [x] Result counts, top results, and alias recall remain equivalent to the
  Phase 5 deployed baseline.
- [x] Concurrency cap is documented and configurable only through a safe
  runtime setting.
- [x] Fetch failures are isolated to the affected retrieval branch and reported
  through existing diagnostics/error handling.

Initial implementation slice:

- [x] `SEARCH_RETRIEVAL_CONCURRENCY` defaults to `1` and is bounded to `1..4`
  by config validation.
- [x] Initial retrieval branches are fetched with a bounded semaphore when the
  setting is above `1`, while parse, match, audit, merge, and ranking still run
  in deterministic retrieval-plan order.
- [x] Partial branch fetch failures emit `retrieval_timings[].failed`,
  `error_type`, and `retrieval.fetch_error` diagnostics; all-branch fetch
  failures still raise through the existing query error path.
- [x] Partial branch fetch failures do not populate the final-result cache, so a
  later healthy run can refetch all retrieval branches.
- [x] Production cold benchmark has compared `SEARCH_RETRIEVAL_CONCURRENCY=1`
  versus `2` and confirmed no recall/top-result drift.

Production benchmark evidence from 2026-06-15 server run:

| Query | c1 cold | c2 cold | Delta | Total count drift | Top result drift |
| --- | ---: | ---: | ---: | --- | --- |
| `daytona panda` | `33361ms` | `22627ms` | `-32.2%` | none, `210` -> `210` | none |
| `5711 blue` | `28594ms` | `21628ms` | `-24.4%` | none, `28` -> `28` | none |
| `15500st blue` | `13492ms` | `8951ms` | `-33.7%` | none, `6` -> `6` | none |
| `126500ln white` | `3969ms` | `3690ms` | `-7.0%` | none, `16` -> `16` | none |

Artifacts:

- `logs/mcp-benchmark-c1-20260615-004231.jsonl`
- `logs/mcp-benchmark-c2-20260615-004552.jsonl`

The official postdeploy check with server runtime
`search_retrieval_concurrency=2` passed `mcp_smoke` `4/4`, MCP benchmark
`13/13`, and alias recall delta `0` for all canonical alias groups.

Suggested verification:

```bash
make mcp-benchmark MCP_BENCHMARK_EXTRA_ARGS=--clear-search-cache
make search-engine-postdeploy-check
```

### Task 6.3: Cache And Prewarm Policy Tightening

Description: Keep deploy/startup prewarm focused on proven common and benchmark
queries, and avoid using prewarm as a substitute for fixing cold-path waste.

Acceptance:

- [x] Prewarm remains best-effort and cannot mask a failing deploy gate.
- [x] Benchmark-default prewarm keeps alias groups hot and equivalent.
- [x] Cache-version changes are documented when retrieval behavior changes.
- [x] Operations docs explain when to use prewarm versus when to optimize
  retrieval itself.

Initial implementation slice:

- [x] `mcp-postdeploy-prewarm` keeps both common-query and benchmark-default
  prewarm failures best-effort through warning-only handling after hard
  predeploy and health gates.
- [x] `prewarm_mcp_cache.py --verify-hot` fails when the verify pass does not
  hit the search cache.
- [x] `prewarm_mcp_cache.py --use-benchmark-defaults` checks canonical alias
  group `total_count` equivalence with the benchmark drift threshold.
- [x] `docs/operations.md` documents prewarm as a latency helper, not a deploy
  gate or a substitute for cold-path retrieval optimization.
- [x] `docs/operations.md` documents when retrieval changes require
  `SEARCH_CACHE_VERSION` bumps and when runtime-only concurrency changes do not.

Suggested verification:

```bash
make mcp-postdeploy-prewarm
make search-engine-postdeploy-check
```

Phase 6 deployment evidence:

- Deployed server HEAD: `759b0fd`.
- Runtime setting: `SEARCH_RETRIEVAL_CONCURRENCY=2`.
- Production MCP deploy gate passed with container tests, compile checks,
  bounded quality audit, hot-cache prewarm, benchmark-default prewarm, and
  alias prewarm checks.
- Postdeploy search-engine check passed with `mcp_smoke` `4/4`, MCP benchmark
  `13/13`, hot-cache hits `13/13`, and alias recall delta `0`.

## Phase 7: Production Observation And Evidence-Driven Search Improvements

Status: deployed on 2026-06-15 at commit `47bf74d`.

Goal: improve matcher, parser, ranking, and retrieval only from concrete
production evidence, while keeping result quality and project size under
control.

Non-negotiables:

- Do not add brand taxonomy, nickname expansion, parser heuristics, or ranking
  rules without a failing audit case or benchmark evidence.
- Do not widen prewarm lists to hide cold-path problems.
- Do not add a new generic engine layer unless it removes duplicated behavior
  or clarifies an existing boundary that is doing unrelated jobs.
- Every behavior-changing search patch needs regression coverage and a cache
  version decision.

### Task 7.1: Update Production Baseline Snapshot

Description: Capture the current deployed search baseline as a small,
repeatable evidence artifact before adding more search rules.

Acceptance:

- [x] A server-side command sequence records runtime config, hot benchmark,
  cold benchmark, alias recall, cache hit rate, and top-result snippets.
- [x] The artifact names and command outputs are documented in deploy notes or
  this plan.
- [x] The snapshot does not include secrets, WatchFacts cookies, browser state,
  full HTML, or unbounded raw listings.

Baseline snapshot from 2026-06-15 production server:

- Artifact directory: `logs/search-engine-baseline/20260615-phase7-task71`.
- Files: `runtime-config.txt`, `hot-benchmark.md`, `cold-benchmark.md`.
- Runtime: `search_retrieval_concurrency=2`,
  `search_cache_ttl_seconds=1800`.
- Hot benchmark: `13/13` passed, average `146ms`, median `97ms`, p95 `706ms`,
  cache hits `13/13`.
- Cold benchmark: `13/13` passed, average `11944ms`, median `10706ms`, p95
  `22470ms`, cache misses `13/13`.
- Alias recall remained equivalent in the benchmark alias groups; top-result
  snippets are present in both benchmark artifacts.
- Snapshot artifacts are limited to safe runtime config and benchmark markdown;
  they do not include cookies, browser state, full HTML, or unbounded raw
  listings.

Implementation slice:

- [x] Added `make search-engine-baseline-snapshot` to write
  `runtime-config.txt`, `hot-benchmark.md`, and `cold-benchmark.md` under
  `logs/search-engine-baseline/<label>/`.
- [x] Added Makefile coverage for the snapshot target and artifact names.

Suggested verification:

```bash
make mcp-runtime-config
make mcp-benchmark
make mcp-benchmark MCP_BENCHMARK_EXTRA_ARGS=--clear-search-cache
```

### Task 7.2: Real Query Gap Audit

Description: Audit real or representative production queries and classify each
gap before changing matcher, parser, ranking, or retrieval behavior.

Acceptance:

- [x] Each finding is classified as query recognition, retrieval coverage,
  parser segmentation, matcher false negative, matcher false positive, ranking,
  image attribution, or cold-path latency.
- [x] Each accepted finding includes the query, expected behavior, current
  behavior, top-result evidence, and a proposed regression fixture.
- [x] Findings that do not affect result quality or speed are explicitly
  deferred instead of implemented.

Production gap audit from 2026-06-15:

- Artifact directory: `logs/search-engine-audit/20260615-phase7-task72`.
- Files: `audit.txt`, `audit.jsonl`, `audit-summary.csv`.
- Query set: `rm07-01 rg snow`, `rm07-01 wg`, `rm07-01 mop`,
  `daytona panda`, `126500ln white`, `5711 blue`, `15500st blue`,
  `FPJ Elegante Titanium`, and `RM65-01 Lebron`.
- Summary metrics: weak match rate `0.0000`, ambiguous candidate rate
  `0.0000`, dedupe drop rate `0.0000`, low-fuzzy included count `2`,
  missing image rate `0.3095`, and stock-list scoped rate `0.0000`.
- Validation errors: `0` for every audited query.

Accepted findings:

| Query | Classification | Expected behavior | Current evidence | Proposed regression fixture |
| --- | --- | --- | --- | --- |
| `126500ln white` | matcher false positive, ranking | A white-dial/product-color result should outrank listings where `white` only appears in accessory context such as `white tag` or `white tag & card`. | Top 5 included black-dial or black-Daytona listings because the required `white` descriptor was satisfied by `white tag`; examples include `126500LN Daytona black dial ... white tag` and `126500ln black daytona ... white tag`. | Result-scoring fixture with a true `126500LN White ...` listing and black listings containing only `white tag/card`, asserting accessory-only color evidence is demoted and ranked after product-color evidence. |
| `daytona panda` | ranking, nickname evidence | Exact `panda` listings or strong dial-color proxy evidence should outrank generic Daytona listings where `white` is material context rather than panda/dial context. | Count `210`; top result was `Rolex Daytona White Gold Baby Lemans 126519...` with fuzzy `40`, while exact `Daytona Panda 126500ln...` rows were ranked below it. | Result-scoring fixture with `Daytona Panda`, `Daytona White Dial`, and `Daytona White Gold Baby Lemans`, asserting exact/proxy panda evidence ranks first and the white-gold-only listing is demoted. |

Deferred findings:

| Query | Classification | Evidence | Decision |
| --- | --- | --- | --- |
| `rm07-01 rg snow`, `rm07-01 mop`, `15500st blue`, `FPJ Elegante Titanium`, `RM65-01 Lebron` | image attribution | Missing images were caused by `image.omitted_bundle_ambiguous` on scoped bundle/list posts; top result text and seller/source remained present. | Deferred. This is an intentional guardrail; do not show ambiguous parent images unless a future parser can prove item-to-image ownership. |
| `FPJ Elegante Titanium` | query recognition | The query plan had no brand candidate, but all audited top rows still matched FPJ/Elegante/Titanium text and no validation or suspicious-result errors appeared. | Deferred until a future audit shows brand-recognition absence causing false positives, missed retrieval, or ranking drift. |
| Full audited set | cold-path latency | Task 7.1 cold benchmark remained the dominant speed cost: average `11944ms`, p95 `22470ms`, cache misses `13/13`. | Deferred to the next performance phase because Task 7.3 should stay focused on confirmed quality/ranking fixes, not another retrieval architecture change. |
| `rm07-01 wg`, `rm07-01 mop`, `5711 blue`, `15500st blue`, `FPJ Elegante Titanium`, `RM65-01 Lebron` | retrieval coverage, parser segmentation, matcher false negative | Representative top rows were relevant; no weak/ambiguous/dedupe drift and no validation errors. | Deferred because no current evidence showed missed result coverage or parser segmentation quality loss. |

Suggested verification:

```bash
python scripts/diagnostics/audit_quality.py --limit 5
make mcp-benchmark MCP_BENCHMARK_EXTRA_ARGS=--clear-search-cache
```

### Task 7.3: Evidence-Based Fix Batch

Description: Implement the smallest set of deterministic fixes for confirmed
audit findings.

Acceptance:

- [x] Each fix has a regression test or fixture tied to a specific finding.
- [x] The default MCP benchmark still passes with alias recall delta within the
  configured threshold.
- [x] Result count and top-result drift are documented for affected queries.
- [x] `SEARCH_CACHE_VERSION` is bumped or explicitly documented as unchanged.

Implemented fix batch:

- Added a ranking guardrail that demotes `white` descriptor matches when the
  only local evidence is accessory context such as `white tag`, `white card`,
  or `white label`, and the user query did not ask for that accessory context.
- Added a `panda` nickname-evidence guardrail for `daytona panda`: exact
  `panda` text or a `white dial` / `white face` proxy stays clean; generic
  white-material Daytona rows are demoted. The guardrail is scoped to `panda`
  only; unaudited nicknames such as `pepsi` are not changed.
- Added a scoped raw-evidence exception so a segment that only says
  `WHITE TAG` is not demoted when its non-stock-list raw parent contains
  product context such as `MODEL: PANDA DAYTONA`.
- Bumped `SEARCH_CACHE_VERSION` from `search-v29` to `search-v30` because ranked
  output changed.

Regression coverage:

- Accessory-only `white tag` no longer outranks a product-color `126500LN White`
  listing for `126500ln white`.
- `Daytona Panda` and `Daytona White Dial` outrank a generic
  `Daytona White Gold Baby Lemans` row for `daytona panda`.
- Raw scoped `MODEL: PANDA DAYTONA` evidence prevents a true panda `WHITE TAG`
  segment from being demoted.
- Unaudited nickname proxies such as `gmt pepsi` are not affected by the new
  `panda` guardrail.

Production verification from 2026-06-15:

- Deployed commit: `47bf74d`.
- Container deploy tests: `690 passed`.
- Local pre-push tests for the final hotfix: `688 passed, 2 skipped`.
- `make search-engine-postdeploy-check` passed: MCP smoke `4/4`, benchmark
  `13/13`, cache hits `13/13`, alias recall delta `0`.
- Final baseline artifacts:
  `logs/search-engine-baseline/20260615-phase7-final/runtime-config.txt`,
  `hot-benchmark.md`, and `cold-benchmark.md`.
- Runtime: `search_retrieval_concurrency=2`,
  `search_cache_ttl_seconds=1800`.
- Hot benchmark: `13/13` passed, average `178ms`, median `100ms`, p95
  `1016ms`, cache hits `13/13`.
- Cold benchmark: `13/13` passed, average `12014ms`, median `10565ms`, p95
  `26597ms`, cache misses `13/13`.
- Focused audit artifacts:
  `logs/search-engine-audit/20260615-phase7-final/audit.txt`,
  `audit.jsonl`, and `audit-summary.csv`.

Affected-query drift:

| Query | Before Phase 7 fix | After Phase 7 fix |
| --- | --- | --- |
| `daytona panda` | Top result was `Rolex Daytona White Gold Baby Lemans 126519...`; exact `Daytona Panda 126500ln...` rows were below it. | Result count stayed `210`; top three rows are exact `Daytona Panda 126500ln...`; `White Dial` proxy remains eligible below exact panda evidence. |
| `126500ln white` | Top 5 included black-dial or black-Daytona rows where `white` only came from `white tag/card`. | Result count stayed `16`; top 5 all have product white or scoped raw panda evidence, and no `guardrail.descriptor_context` reason appears in the top 5. |
| `126500ln white 2026` | The first deploy attempt exposed an edge case where raw `MODEL: PANDA DAYTONA` was demoted because the segment only said `WHITE TAG`. | Result count stayed `3`; top quality groups are `[0, 0, 0]`, and the raw panda-context segment remains clean. |

Suggested verification:

```bash
python -m pytest
make search-engine-postdeploy-check
```

## Phase 8: Context Evidence And Cold-Path Budgeting

Status: in progress from Phase 7 postdeploy evidence.

Goal: turn the narrow Phase 7 guardrails into a small, reusable evidence model
only where production audit proves the need, while reducing cold-path latency
without hiding cost through broader prewarm.

Design stance:

- Do not create new `engine` packages for Phase 8. Keep the project smaller by
  improving the existing query recognition, matcher, parser, scoring, and
  diagnostics boundaries.
- Move repeated context checks into compact helpers or rulebook data only after
  a second audited case needs the same behavior.
- Demote questionable results with explicit reason codes before considering
  hard filtering.
- Keep raw-parent evidence disabled for stock-list scope unless item ownership
  is proven.

### Task 8.1: Descriptor Evidence Model

Description: Define a minimal descriptor-evidence helper for product context,
accessory context, raw scoped context, and excluded stock-list context. Start
with the Phase 7 `white tag` / `panda` evidence and add no new descriptor groups
without audit cases.

Acceptance:

- [x] Existing Phase 7 behavior remains unchanged for `126500ln white`,
  `126500ln white 2026`, and `daytona panda`.
- [x] Evidence reasons distinguish product, accessory, raw scoped, and
  stock-list-excluded context.
- [x] Tests prove unaudited nicknames and colors are not silently affected.
- [x] No new module is added unless it replaces duplicated logic in at least two
  current files.

Implementation notes:

- Added a small internal `DescriptorContextEvidence` model inside
  `app/searching/result_scoring.py`; no new module or package was introduced.
- Existing Phase 7 ranking behavior is unchanged. The model only centralizes the
  context decision and emits auditable reason codes:
  `evidence.product_color:white`, `evidence.accessory_color:white`,
  `evidence.raw_scoped_product:panda`, and
  `evidence.stock_list_excluded:panda`.
- Raw scoped evidence is only considered when the local segment has accessory
  color evidence and no local product-color evidence. It is still excluded for
  stock-list scope.
- `SEARCH_CACHE_VERSION` is unchanged because sort keys, result counts, result
  payloads, and cache semantics did not change.

Verification from 2026-06-15:

- `python -m pytest tests/test_result_scoring.py -q` passed with `38 passed`.
- `python -m pytest tests/test_search.py tests/test_audit_quality.py tests/test_result_scoring.py -q`
  passed with `128 passed, 2 skipped`.
- `python -m pytest -q` passed with `692 passed, 2 skipped`.
- `python -m compileall app scripts` passed.
- `python scripts/diagnostics/audit_quality.py "126500ln white" "daytona panda" --limit 5`
  kept the same top result shape: `126500ln white` count `16`, top quality
  groups `[0, 0, 0, 0, 0]`; `daytona panda` count `210`, top quality groups
  `[0, 0, 0, 0, 0]`. The audit now exposes product, accessory, and raw scoped
  evidence reasons for the relevant `126500ln white` rows.

Suggested verification:

```bash
python -m pytest tests/test_result_scoring.py tests/test_search.py tests/test_audit_quality.py
python scripts/diagnostics/audit_quality.py "126500ln white" "daytona panda" --limit 5
```

### Task 8.2: Parser Evidence For Scoped Raw Parents

Description: Make parser/scoping output expose why raw parent evidence is safe
or unsafe, instead of leaving scoring to infer it from `scope_reason` alone.

Acceptance:

- [ ] Scoped result payloads can explain whether raw parent evidence was used,
  ignored, or excluded.
- [ ] Stock-list raw evidence stays excluded unless item-to-image/text ownership
  is deterministic.
- [ ] Audit output includes the new reason codes without exposing full raw HTML
  or sensitive session data.

### Task 8.3: Cold-Path Retrieval Budget Audit

Description: Instrument and compare cold retrieval branches so the next speed
change is based on branch-level cost and recall contribution, not guesswork.

Acceptance:

- [ ] Cold benchmark artifacts include per-query and per-branch timing for the
  slow representatives: `daytona panda`, `5711 blue`, `15500st blue`, and
  `rm07-01 rg snow`.
- [ ] Each candidate optimization states expected recall risk before code
  changes.
- [ ] No prewarm list is expanded as a substitute for reducing cold-path cost.
- [ ] Any retrieval-plan change is checked against alias recall delta and
  result-count drift.

### Task 8.4: Image Attribution Decision Gate

Description: Revisit high missing-image cases only if an audit can prove safe
item-to-image ownership. This is a decision gate, not an automatic parser
rewrite.

Acceptance:

- [ ] `image.omitted_bundle_ambiguous` cases are grouped by raw layout pattern.
- [ ] A proposed image fix must show deterministic ownership evidence and a
  regression fixture.
- [ ] If ownership is still ambiguous, the finding remains deferred.

### Task 8.5: Brand Recognition Backlog

Description: Track brand-recognition gaps such as `FPJ Elegante Titanium`
without adding taxonomy until absence of brand recognition causes false
positives, missed retrieval, or ranking drift.

Acceptance:

- [ ] Brand additions require before/after audit evidence.
- [ ] Brand aliases live in the existing rulebook data, not scattered branches.
- [ ] The default benchmark and focused audit remain stable after each accepted
  brand addition.

## Metrics

Track these before and after each phase:

| Metric | Target |
| --- | --- |
| alias-equivalent recall delta | Near zero for canonical equivalents |
| hot-cache MCP latency | Usually below 100 ms for cached searches |
| cold-path WatchFacts fetch latency | Recorded for every deploy; after Phase 6, reduced for multi-query retrieval without recall loss |
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

Phase 7 is implemented, pushed, and deployed at `47bf74d`. The next useful work
is Phase 8 Task 8.1: extract the Phase 7 context checks into a minimal
descriptor evidence model without changing behavior. Do not start another broad
matcher/parser/ranking expansion until fresh audit data shows a real quality or
latency gap and the benchmark can prove no false-positive or recall regression.
