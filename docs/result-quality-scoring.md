# Result Quality Scoring And Matcher Diagnostics Spec

## Objective

Improve result quality after deterministic matching without making AI or
ad-hoc ordering the source of truth.

The next phase should keep the matcher focused on deciding whether a listing is
eligible for a query, then move output ordering and operator diagnostics into
explicit, testable layers.

## Current Baseline

Completed baseline behavior:

- `app.matcher` is a stable public matcher API.
- `app.matcher_rules` contains deterministic matching and extraction rules.
- `app.matcher_rulebook` contains rule groups, priorities, and extraction trace
  types.
- Search output is quality-first:
  - clean results first
  - missing-price-evidence results after clean results
  - other suspicious results last
- Results inside the same quality group are sorted by newest posted date first.
- Missing-price listings are demoted, not removed.
- OpenAI guarded refinement can improve scoped text only after local validation
  gates pass.

## Problem

The bot now has better deterministic extraction, but result quality still
depends on logic spread across matcher, suspicious-result detection, search
ordering, and OpenAI gates.

The main risks are:

- Ranking rules become harder to change safely as more cases are added.
- A result can match correctly but rank poorly because quality signals are
  implicit.
- Debugging a reported result still requires reading logs or reproducing a
  query locally.
- Future matcher refactors may change behavior without a clear trace contract.

## Design Principles

- Matching answers: "is this listing eligible for the query?"
- Scoring answers: "how good is this eligible listing for the user?"
- Diagnostics answer: "why did the bot keep, cut, rank, demote, or flag this
  result?"
- AI suggestions remain optional and guarded; they may add a signal but must not
  override local eligibility and safety gates.
- Production output must keep the summary-first Telegram flow. Users should see
  the summary first, then press "Xem ket qua" / "Xem them" for result batches.

## Functional Scope

### 1. Result Scoring Layer

Add a dedicated result scoring layer after matching, dedupe, and suspicious
detection.

Recommended module:

```text
app/result_scoring.py
```

The scorer should return a structured score object rather than a bare sort key.

Suggested fields:

| Field | Purpose |
| --- | --- |
| `quality_group` | Primary quality bucket: clean, missing price, suspicious |
| `posted_date_rank` | Newest date first inside the same quality group |
| `exact_reference_score` | Prefer exact or compact-reference match over weak text match |
| `descriptor_score` | Prefer listings where query descriptors are local to the reference |
| `price_evidence_score` | Prefer listings with visible price evidence when relevant |
| `seller_signal_score` | Optional future field for seller/dealer preferences |
| `original_rank` | Stable fallback preserving WatchFacts/source order |
| `reasons` | Short reason codes for diagnostics and tests |

Initial ordering contract:

```text
quality_group ASC
posted_date DESC
exact_reference_score DESC
descriptor_score DESC
price_evidence_score DESC
original_rank ASC
```

Quality group remains the first sort key. A newer result with missing price
evidence must not outrank a clean priced result.

### 2. Matcher Trace Diagnostics

Expose matcher trace in a maintainer/operator-friendly way.

Minimum local API:

```text
explain_extraction(query, listing_text) -> ExtractionTrace
score_result(query, result, original_rank) -> ResultScore
```

Optional owner command for a later implementation:

```text
/debug_match <query> | <listing text or issue id>
```

Owner-visible diagnostics should show:

- normalized query intent
- selected reference
- selected token/character span
- rule ids applied
- score reason codes
- suspicious reason codes, if any

Diagnostics must not include cookies, tokens, browser state, `.env`, full page
HTML, or raw secrets.

### 3. Regression Fixtures

Every production ranking or extraction bug should become a fixture with:

- query
- raw listing text
- shown listing text or expected selected text
- posted date
- seller, when available
- expected quality group
- expected relative order when ranking is the bug
- expected trace/rule ids when extraction is the bug

Tests should cover:

- clean results sort newest first
- missing-price results stay below clean priced results
- suspicious non-price truncation stays below missing-price-only cases
- exact reference beats weak/compact ambiguity inside the same quality/date
  group
- descriptor locality affects score without admitting unrelated listings

### 4. Optional OpenAI Signal

OpenAI can contribute only a bounded review/refinement signal:

- accepted guarded refinement can improve `shown_text`
- rejected AI suggestions can add diagnostic reasons
- OpenAI must not create a match for a listing that deterministic matching would
  reject
- OpenAI must not move suspicious or missing-price results ahead of clean
  deterministic results unless a future spec defines an explicit reviewed rule

## Non-Goals

- Replacing deterministic matcher with AI ranking.
- Letting OpenAI browse WatchFacts or query production data directly.
- Reordering results only by newest date while ignoring quality.
- Hiding missing-price or suspicious results completely.
- Refactoring all matcher helper functions in one large change.
- Changing Telegram summary-first pagination behavior.

## Implementation Phases

### Phase 9.1: Extract Scoring Module

Status: complete.

Move current quality/date ordering from `app/search.py` into
`app/result_scoring.py`.

Acceptance:

- Existing production order is preserved.
- Missing-price results remain demoted below clean results.
- Clean results sort by newest posted date descending.
- Unit tests cover score fields and final sort order.

### Phase 9.2: Add Structured Score Reasons

Status: complete.

Return reason codes alongside sort values.

Acceptance:

- Tests can assert reason codes without depending on private tuple order.
- Search logs can include safe score summaries for debugging.
- No Telegram user-facing output changes are required.

### Phase 9.3: Add Matcher/Score Debug Surface

Status: complete for the local debug script; Telegram exposure is deferred until
the production bot has a configured owner allowlist.

Add a local script or owner-only command for inspecting one query/listing or one
stored issue.

Acceptance:

- Debug output includes matcher trace and score reasons.
- Debug output is safe for Telegram length limits.
- Owner-only command requires configured `TELEGRAM_ALLOWED_USER_IDS` if exposed
  through Telegram.

### Phase 9.4: Split Matcher Helpers After Coverage

Status: complete for normalization/tokenization. Further helper splits are
deferred until a concrete production issue or test fixture justifies the move.

Only after phases 9.1-9.3 are covered, split `matcher_rules.py` into smaller
implementation files if it reduces maintenance risk.

Candidate files:

```text
app/matcher_normalization.py
app/matcher_intent.py
app/matcher_reference.py
app/matcher_descriptor.py
app/matcher_boundaries.py
app/matcher_extraction.py
```

Acceptance:

- `app.matcher` public API remains unchanged.
- All matcher, search, AI gate, and Telegram tests pass.
- No production behavior changes unless explicitly covered by fixtures.

### Phase 10.1: Query-Aware Relevance Signals

Status: complete.

Populate the relevance score fields introduced in Phase 9 while preserving the
primary quality/date order.

Acceptance:

- Search workflow passes the query into `rank_results_by_quality()`.
- `exact_reference_score` is populated from matcher trace reference selection.
- `descriptor_score` is populated when descriptors are local to the selected
  reference.
- `price_evidence_score` is populated from visible price evidence in shown text.
- Relevance signals only affect order after quality group and posted date are
  equal.
- Search cache version is bumped for the ranking tie-break change.

## Verification

Recommended commands for this phase:

```bash
.venv/bin/python -m pytest tests/test_matcher.py tests/test_search.py tests/test_telegram_bot.py
.venv/bin/python -m pytest
git diff --check
```

Before production deploy:

```bash
make predeploy-check
make deploy
```

## Success Criteria

- Ranking behavior is readable from one scoring module.
- Ranking tests assert quality-first and date-desc behavior explicitly.
- A reported ranking issue can be reproduced with one fixture.
- Matcher diagnostics explain why a listing was selected and where extraction
  stopped.
- Future matcher refactors can be done in smaller files without changing the
  public `app.matcher` API.
