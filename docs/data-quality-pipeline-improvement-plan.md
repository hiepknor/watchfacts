# Data Quality Pipeline Improvement Plan

## Objective

Improve WatchFacts search data quality without changing the core deterministic
matching contract.

This plan adds better visibility into the full data funnel, stronger data
contracts, and optional confidence signals so maintainers can see where results
are lost, merged, demoted, or promoted before changing matcher behavior.

Primary goals:

- Improve confidence that final results are complete and relevant.
- Detect false positives and false negatives earlier.
- Make raw crawl, parser, matcher, dedupe, ranking, and final output differences
  explainable from one audit artifact.
- Preserve deterministic search as the source of truth.

Non-goals:

- Do not replace matcher rules with an LLM, ML model, or probabilistic entity
  resolver.
- Do not use fuzzy matching as the primary accept gate in the first rollout.
- Do not store WatchFacts cookies, browser state, credentials, full page HTML, or
  sensitive raw response bodies in audit output.

## Current Baseline

Implementation status: phases 1 through 5 are implemented. RapidFuzz remains a
secondary signal and never includes candidates by itself. The only user-facing
guardrail behavior is conservative demotion for final results that have an
exact reference match but a clear required-descriptor conflict.

The current runtime now has:

- deterministic parsing and matching;
- dedupe by stable listing identity/text;
- result quality scoring and similarity grouping;
- suspicious-result detection;
- MCP payload diagnostics through `search_diagnostics`;
- `scripts/diagnostics/audit_quality.py` for bounded production/local audits,
  JSONL audit exports, DuckDB JSONL summaries, and before/after comparisons;
- lightweight search payload and diagnostics contract validation;
- RapidFuzz-backed fuzzy diagnostics with a standard-library fallback;
- weak/ambiguous candidate diagnostics in audit events;
- query intent metadata in diagnostics and audit artifacts;
- JSON and JSONL audit artifact conversion into draft regression fixtures;
- docs for production quality audit and result scoring.

## Design Principles

- Deterministic first: parser, matcher, dedupe, scoring, and grouping remain
  locally explainable.
- Observe before changing behavior: phase 1 through phase 3 should not change
  final result eligibility.
- Prefer demotion or review buckets over deletion when a result is relevant but
  uncertain.
- Keep production audit output safe, bounded, and redacted.
- Add dependencies only when they earn their operational cost.
- Do not make MCP clients reimplement WatchFacts search logic. Clients should
  consume MCP payloads and diagnostics from the runtime.

## Technology Choices

### DuckDB

Use DuckDB for offline/local audit analysis over JSONL, CSV, or Parquet exports.

Recommended use:

- query-level funnel summaries;
- stage-to-stage drop analysis;
- duplicate/drop inspection;
- regression comparison between two audit runs;
- aggregate reports across curated query sets.

DuckDB should not become the production cache or primary runtime database. SQLite
continues to own local cache, dedupe records, and query history.

### RapidFuzz

Use RapidFuzz as a secondary diagnostics signal.

Recommended use:

- compute fuzzy confidence between normalized query and candidate listing text;
- compare reference-like query tokens with listing reference segments;
- expose weak or near-match evidence in audit output;
- help maintainers decide which deterministic rules need improvement.

RapidFuzz must not be the primary accept gate in the first rollout.

### Pandera / Great Expectations

Do not add Pandera or Great Expectations in the first rollout.

Start with lightweight internal validators because current contracts are small
and Python-native. Revisit Pandera only if audit exports become dataframe-heavy
and schema validation grows beyond simple result/listing invariants.

### datasketch, dedupe, Splink

Do not add these to production runtime initially.

Use them only as future offline evaluation tools if audit data proves that
current dedupe is either too slow, too noisy, or over-merging distinct listings.

## Proposed Public Interface Changes

MCP search payload stays backward-compatible. Existing consumers can continue to
ignore unknown fields.

Extend `search_diagnostics` with additional optional fields:

| Field | Meaning |
| --- | --- |
| `raw_candidate_count` | Number of bounded raw candidate containers observed before parsing, when measurable |
| `parsed_count` | Number of parsed listing candidates |
| `matched_count` | Number of candidates accepted by matcher before conversion to final results |
| `search_result_count` | Number of converted `SearchResult` objects before dedupe |
| `unique_latest_count` | Count after latest-listing dedupe |
| `unique_text_count` | Count after text-level dedupe |
| `deduped_drop_count` | Number of candidate/result records removed by dedupe |
| `final_count` | Number of final user-facing results |
| `weak_match_count` | Number of deterministic matches with low/conflicting confidence signals |
| `ambiguous_candidate_count` | Number of non-final candidates with strong diagnostic-only fuzzy evidence |
| `fuzzy_score_min` | Lowest fuzzy diagnostics score among audited final results |
| `fuzzy_score_avg` | Average fuzzy diagnostics score among audited final results |
| `query_intent` | Classified query shape such as `reference_only`, `reference_with_descriptor`, or `brand_model_descriptor` |
| `required_descriptor_tokens` | Descriptor tokens the guardrail treats as required for the query intent |
| `optional_descriptor_tokens` | Descriptor tokens treated as optional metadata, such as soft year evidence |
| `intent_reason_codes` | Short reason codes explaining intent classification |
| `guardrail_action_counts` | Counts of emitted `warn` / `demote` guardrail actions |
| `rejection_reasons` | Stage-level reason code counts for rejected/dropped candidates |

Rules:

- Fields may be `null` on cache hits when stage-level data is unavailable.
- Unknown fields must not break MCP clients, Telegram, or result templates.
- Do not expose full raw HTML or sensitive browser/session data.

## Phase 1: Audit Funnel Export

### Goal

Make each query explainable across the data funnel:

```text
crawl/raw candidate metadata
  -> parser candidates
  -> matcher accepted candidates
  -> SearchResult conversion
  -> latest/text dedupe
  -> scoring/grouping
  -> final user-facing results
```

### Implementation

- Add a small audit event model for query-stage records.
- Extend the existing quality audit script to optionally emit JSONL.
- Keep default text output stable for humans.
- Add a DuckDB-backed summary mode or companion script that reads JSONL and
  reports stage counts, drop counts, and duplicate groups.
- Include bounded text snippets and reason codes only.
- Reuse the normal search workflow. Do not duplicate crawling, parsing,
  matching, or ranking logic in the audit script.

Suggested audit event fields:

| Field | Meaning |
| --- | --- |
| `query` | User query being audited |
| `run_id` | Audit run identifier |
| `stage` | `raw`, `parsed`, `matched`, `converted`, `dedupe_drop`, `final` |
| `candidate_id` | Stable per-run candidate identifier |
| `result_id` | Public result handle when available |
| `dedupe_key` | Dedupe identity when available |
| `rank` | Stage-local or final rank when available |
| `seller` | Seller text when available |
| `posted_date` | Posted date when available |
| `source_url` | Source URL when available |
| `has_image` | Whether image URL exists |
| `text_snippet` | Bounded sanitized text |
| `reason_codes` | Stage-specific reasons |

### Acceptance Criteria

- Maintainer can run one audit command and get a JSONL artifact.
- Maintainer can answer how many records existed at each stage for a query.
- Dedupe drops are visible with kept/dropped context.
- No audit output contains secrets, cookies, browser state, `.env` values, or
  full page HTML.
- Existing `make quality-audit` behavior remains usable.

### Verification

Recommended commands:

```bash
python -m compileall app scripts
python -m pytest tests/test_search.py tests/test_audit_quality.py
python scripts/diagnostics/audit_quality.py "5712g" --format jsonl --limit 1
python scripts/diagnostics/audit_quality.py --summarize-jsonl audit-report.jsonl
make quality-audit
```

If production/session credentials are unavailable, run only local fixture-backed
tests and report that live audit was not executed.

## Phase 2: Lightweight Data Contract Validation

### Goal

Catch malformed parser/search output before it becomes a user-facing quality
problem.

### Implementation

- Add internal validators for audit/test contexts.
- Validate final result payloads, parser candidates, and diagnostics summaries.
- Report validation errors in audit output.
- Avoid failing production search hard unless the error prevents safe output.

Suggested validations:

| Contract | Failure Handling |
| --- | --- |
| Final `listing_text` is non-empty | Audit error; production result should be skipped only if unsafe to display |
| Final `result_id` exists when rendering MCP/template payloads | Audit/test failure |
| `rank` is positive and stable | Audit/test failure |
| `source_url` is valid when present | Audit warning or failure depending on context |
| Duplicate public `result_id` in one payload | Audit/test failure |
| `final_count <= matched_count` when uncached stage metrics exist | Audit warning or failure depending on scenario |
| Diagnostics fields are JSON-serializable | Audit/test failure |

### Acceptance Criteria

- Contract failures appear as explicit audit warnings/errors.
- Unit tests cover empty listing text, duplicate IDs, invalid rank, invalid
  source URL, and cache-hit diagnostics with missing stage metrics.
- Production search remains resilient to non-critical diagnostics failures.

### Verification

Recommended commands:

```bash
python -m compileall app scripts
python -m pytest tests/test_audit_quality.py tests/test_mcp_smoke.py
```

## Phase 3: RapidFuzz Diagnostics

### Goal

Add fuzzy confidence signals without changing final result eligibility.

### Implementation

- Add `rapidfuzz` to runtime dependencies.
- Add a diagnostics helper for fuzzy scoring.
- Compute scores after deterministic matching and before/inside audit
  formatting.
- Store or emit score summaries through `search_diagnostics` and audit rows.
- Do not use fuzzy score to accept or reject final results in this phase.

Suggested score outputs:

| Field | Meaning |
| --- | --- |
| `query_text_score` | Similarity between normalized query and normalized listing text |
| `reference_score` | Similarity between query reference token and listing reference-like segment |
| `descriptor_overlap_score` | Query descriptor coverage in candidate text |
| `fuzzy_reason_codes` | Short reason codes explaining high/low confidence |

Suggested reason codes:

```text
exact_reference_match
near_reference_match
reference_score_low
descriptor_overlap_low
query_text_score_low
server_filtered_only
```

### Acceptance Criteria

- Audit output shows fuzzy scores for final results.
- RapidFuzz never accepts candidates by itself.
- Exact-reference results with required-descriptor conflict may be demoted by
  scoring when audit evidence and tests cover the behavior.
- Tests cover exact reference, near reference typo, descriptor mismatch, and
  unrelated listing cases.

### Verification

Recommended commands:

```bash
python -m compileall app scripts
python -m pytest tests/test_fuzzy_diagnostics.py tests/test_search.py tests/test_audit_quality.py
make quality-audit
```

## Phase 4: Weak And Ambiguous Candidate Diagnostics

### Goal

Expose candidates that may be useful for future matcher improvements without
mixing uncertain records into final user-facing results.

### Implementation

- Add diagnostics-only classification after deterministic matching:
  - `strong`: deterministic match and confidence signals agree.
  - `weak`: deterministic match exists but confidence signals are low or
    conflicting.
  - `ambiguous`: fuzzy/descriptor evidence is high but deterministic matcher did
    not accept the candidate.
- Keep final results limited to current deterministic output.
- Add weak/ambiguous counts and examples to audit output.
- Let MCP clients mention the existence of uncertain candidates only if the MCP
  payload explicitly provides that diagnostic data.

### Acceptance Criteria

- Weak and ambiguous records are visible in audit JSONL.
- Final result payload remains deterministic and conservative.
- No uncertain candidate becomes a public result without a later explicit
  behavior-change phase.

### Verification

Recommended commands:

```bash
python -m compileall app scripts
python -m pytest tests/test_audit_quality.py tests/test_search.py
make quality-audit
```

## Phase 5: Dedupe Similarity Evaluation

### Goal

Improve dedupe only after audit evidence shows current behavior is losing or
merging useful results.

### Implementation

- First expose dedupe keep/drop pairs in audit output.
- Add tests for known over-dedupe or under-dedupe cases.
- Evaluate `datasketch` offline only if volume or O(n^2) comparison becomes a
  real bottleneck.
- Keep deterministic dedupe keys as the production authority.

### Acceptance Criteria

- Audit output explains every dedupe drop.
- Dedupe changes are backed by fixture tests and before/after audit artifacts.
- Different seller/date/source records are not hard-dropped solely because text
  is similar.

### Verification

Recommended commands:

```bash
python -m compileall app scripts
python -m pytest tests/test_dedupe.py tests/test_search.py tests/test_audit_quality.py
make quality-audit
```

## Rollout Strategy

Implemented rollout order:

1. Phase 1 shipped audit events, JSONL export, and DuckDB summary support.
2. Phase 2 shipped shared search payload/diagnostics contract validators.
3. Phase 3 shipped RapidFuzz fuzzy diagnostics and conservative descriptor
   conflict demotion; fuzzy still cannot include a candidate.
4. Phase 4 shipped weak/ambiguous diagnostics as audit-only evidence.
5. Phase 5 shipped dedupe drop events with kept-result audit references.
6. DuckDB compare mode and JSONL fixture generation were added for before/after
   regression evidence.

Cache policy:

- Bump search cache version only when serialized final result shape, ranking,
  extraction, gating, or user-facing behavior changes.
- Do not bump cache version for audit-only JSONL output unless diagnostics are
  persisted in cached payloads.

Deployment policy:

- Run local checks before deploy.
- Deploy MCP/runtime.
- Run MCP health and a focused post-deploy audit query.
- Record any production finding as a regression fixture before changing matcher
  or dedupe behavior.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Audit output leaks sensitive runtime state | High | Use bounded snippets, never write full HTML/session/cookies, add no-secret tests |
| Fuzzy score becomes hidden matching authority | High | Keep RapidFuzz diagnostics-only until a separate accepted behavior-change plan |
| Extra dependencies increase deploy risk | Medium | Add only DuckDB and RapidFuzz when their phase starts; keep Pandera/datasketch out initially |
| Cache-hit diagnostics lack stage metrics | Medium | Allow nullable stage fields and mark `cache_hit=true` clearly |
| Dedupe audit exposes too much raw text | Medium | Emit bounded snippets and reason codes only |
| Audit scripts drift from runtime behavior | High | Reuse `WatchFactsSearchWorkflow` and existing parser/matcher/scoring modules |

## Implementation Tasks

### Task 1: Add audit event model and JSONL formatter

Acceptance:

- Audit events serialize to deterministic JSONL.
- Bounded snippets are redacted and length-limited.
- Existing text audit output still works.

Suggested tests:

```bash
python -m pytest tests/test_audit_quality.py
```

### Task 2: Capture stage counts and dedupe drops

Acceptance:

- Uncached query diagnostics include parsed, matched, converted, dedupe, and
  final counts.
- Dedupe drop records include kept/dropped identifiers and reason codes.
- Cache-hit diagnostics remain safe with nullable stage data.

Suggested tests:

```bash
python -m pytest tests/test_search.py tests/test_audit_quality.py
```

### Task 3: Add DuckDB summary command

Acceptance:

- Maintainer can summarize a JSONL artifact with stage counts per query.
- Summary command works without WatchFacts credentials because it reads local
  audit artifacts.

Suggested tests:

```bash
python -m pytest tests/test_audit_quality.py
```

### Task 4: Add lightweight contract validator

Acceptance:

- Validator reports malformed result payloads with reason codes.
- Audit output includes validation warnings/errors.
- Production path does not crash on non-critical validation warnings.

Suggested tests:

```bash
python -m pytest tests/test_search.py tests/test_tool_runtime.py tests/test_audit_quality.py
```

### Task 5: Add RapidFuzz diagnostics and guardrail demotion

Acceptance:

- Fuzzy scores are emitted in audit output.
- MCP `search_diagnostics` includes aggregate fuzzy score fields when computed.
- Fuzzy does not include candidates.
- Descriptor conflict demotion is covered by regression tests and requires a
  cache version bump.

Suggested tests:

```bash
python -m pytest tests/test_fuzzy_diagnostics.py tests/test_search.py tests/test_audit_quality.py
```

### Task 6: Add weak/ambiguous diagnostics bucket

Acceptance:

- Audit JSONL includes examples of weak and ambiguous candidates.
- Final result payload remains conservative.
- MCP clients can only reference this data when MCP payload provides it.

Suggested tests:

```bash
python -m pytest tests/test_audit_quality.py tests/test_search.py
```

### Task 7: Add dedupe keep/drop audit references

Acceptance:

- Dedupe drop events include a redacted dedupe key hash.
- Dedupe drop events include the kept result audit id when available.
- Production dedupe behavior is unchanged.

Suggested tests:

```bash
python -m pytest tests/test_search.py
```

### Task 8: Add DuckDB compare and JSONL fixture loop

Acceptance:

- Maintainer can run `--compare-jsonl before.jsonl after.jsonl` to inspect
  changed stage counts.
- `scripts/fixtures/generate_audit_fixtures.py` accepts audit JSON and JSONL
  final-result events.
- Future matcher/scoring fixes can move from audit finding to fixture before
  implementation.

Suggested tests:

```bash
python -m pytest tests/test_audit_quality.py tests/test_generate_audit_fixtures.py
```

## Documentation Updates

When implementing phases, keep these docs synchronized:

- Update `docs/production-quality-audit.md` when audit commands or formats
  change.
- Update `docs/result-quality-scoring.md` when fuzzy diagnostics become part of
  scoring or operator diagnostics.
- Update `docs/technical-spec.md` when new diagnostics fields become stable MCP
  payload fields.
- Add an ADR before changing final result eligibility based on fuzzy or
  probabilistic scoring.

## Open Decisions

No implementation-blocking decision remains for diagnostics-only phases.

Before phase 4 becomes user-facing behavior, explicitly decide whether
`weak/ambiguous` candidates should:

- stay audit-only;
- appear in client responses as a separate "possible matches" count;
- become a guarded opt-in result bucket.

Before phase 5 adds `datasketch`, explicitly decide whether approximate
similarity belongs only in diagnostics or also in runtime dedupe.
