# Production Quality Audit Loop Spec

## Objective

Make production result quality measurable and repeatable.

The bot already has deterministic matching, guarded OpenAI refinement, result
quality scoring, and matcher diagnostics. The next phase should turn real
production query output into a standard audit report, then convert confirmed
issues into fixtures and regression tests before changing matcher or scoring
behavior.

## Current Baseline

- Production runs with `HYBRID_AI_MODE=guarded`.
- Telegram keeps summary-first behavior: users receive a result count first and
  press "Xem ket qua" / "Xem them" before result messages are sent.
- Ranking is quality-first:
  - clean results first
  - missing-price results after clean results
  - stronger suspicious results last
- Within the same quality group, newer posted dates rank before older dates.
- Query-aware relevance is a tie-break after quality and posted date.
- Search cache version must be bumped when ranking, scoring, or quality-gate
  behavior changes.

## Problem

Production issues still arrive as individual observations:

- A real query returns a suspicious result near the top.
- A dealer shorthand price is ambiguous.
- A multi-list card extracts the wrong segment.
- A result matches the query but ranks below a lower-quality result.
- A fix works locally but cached production results hide the behavior change.

Without a standard audit loop, these issues can be fixed inconsistently and may
regress later.

## Design Principles

- Production evidence first: do not tune broad rules from one vague report.
- Every confirmed issue should become a fixture or test before behavior changes.
- Audit output must be safe: no `.env`, API keys, Telegram tokens, WatchFacts
  cookies, browser state, or full page HTML.
- Quality gates should be conservative. Prefer demotion over deletion when a
  result is relevant but incomplete.
- Ambiguous dealer shorthand should be documented before tightening rules.
- OpenAI can help classify hard snippets, but deterministic gates remain the
  authority for user-facing output.

## Functional Scope

### 1. Production Audit Script

Add a script for running a curated query set against either local or production
runtime.

Recommended file:

```text
scripts/audit_quality.py
```

Minimum capabilities:

- Accept queries from CLI arguments or a text/JSON file.
- Run the normal search workflow with current settings.
- Print or write a bounded report for each query:
  - result count
  - top result rank
  - listing text snippet
  - posted date
  - quality group
  - quality severity
  - reference score
  - descriptor score
  - price evidence score
  - score reason codes
  - suspicious reason codes
- Support `--limit` for top-N results.
- Support `--format text` and optionally `--format json`.
- Return non-zero only on runtime failures, not on quality warnings.

The script should use the existing app modules instead of duplicating scraping,
parsing, scoring, or OpenAI code.

### 2. Curated Query Sets

Keep a default audit set focused on diverse matcher risks:

```text
5205r 2026
126500ln white 2026
7118/1200a grey
Fpj Elegante Titanium
228235a choco
5712r
5205r green
5726/1a
RM65-01 Lebron
116500 panda
```

Future query sets may be grouped by risk:

- reference-only queries
- reference plus color/dial descriptor
- year/date descriptor queries
- RM/FPJ/AP/Patek/Rolex edge cases
- multi-list card extraction
- ambiguous price shorthand
- known user-reported issues

### 3. Issue Classification

Audit findings should be classified before implementation:

| Class | Meaning | Preferred action |
| --- | --- | --- |
| `wrong_reference` | Result references a different model | matcher/gate fixture |
| `wrong_descriptor` | Color, material, dial, year, or edition conflicts | descriptor conflict fixture |
| `bad_extraction` | Relevant card exists but shown segment is wrong | extraction fixture |
| `bad_rank` | Correct results exist but quality order is wrong | scoring fixture |
| `missing_price` | Result has no visible price evidence | suspicious-result fixture |
| `ambiguous_price` | Dealer shorthand could be valid or invalid | policy doc first |
| `cache_stale` | Production cache hides a ranking/gate change | cache version bump |

### 4. Ambiguous Price Policy

Do not immediately reject common dealer shorthand. Document it first.

Currently accepted examples:

- `465k`
- `HKD785K`
- `$36k`
- `30+lbl`
- `26299 + lab`
- `USDT 485`
- `110k€`
- `248 €`

Known non-price/material examples:

- `18k rose gold`
- `22k gold`
- `24k yellow gold`

Policy:

- Material karat terms must not count as price evidence.
- Currency or label shorthand may count as price evidence when it appears in a
  dealer sale context.
- Ambiguous shorthand should be demoted only after at least one production issue
  proves it creates bad ranking.

### 5. Regression Fixture Workflow

For every confirmed issue:

1. Capture query, shown text, raw text when available, seller, posted date, and
   expected behavior.
2. Add the smallest test that fails for the current bug.
3. Fix matcher, scoring, extraction, or gate logic.
4. Run focused tests.
5. Run full test suite.
6. Bump search cache version if output ordering or gating changes.
7. Deploy and rerun the audit query.

## Non-Goals

- Replacing deterministic ranking with OpenAI ranking.
- Making the bot delete all incomplete results.
- Printing secrets or raw browser state in reports.
- Building a dashboard before the CLI audit flow is stable.
- Changing Telegram summary-first pagination.

## Implementation Checklist

### Phase 10.1: Audit Script

- [ ] Add `scripts/audit_quality.py`.
- [ ] Support query args and default query set.
- [ ] Support `--limit`.
- [ ] Include score fields and reason codes.
- [ ] Keep output safe and bounded.
- [ ] Add tests for report formatting and score inclusion where practical.

### Phase 10.2: Fixture Capture Path

- [ ] Document how to turn audit output into tests.
- [ ] Reuse existing `/issues_export` and `scripts/generate_issue_fixtures.py`
  where possible.
- [ ] Add a fixture template for ranking, extraction, and missing-price cases.
- [ ] Require a failing fixture before broad matcher/scoring changes.

### Phase 10.3: Ambiguous Price Policy

- [ ] Centralize accepted and rejected shorthand examples in docs.
- [ ] Add regression tests for accepted shorthand that must stay clean.
- [ ] Add regression tests for rejected material/karat terms.
- [ ] Avoid tightening ambiguous shorthand without production evidence.

### Phase 10.4: Production Verification Loop

- [ ] Run the 10-query audit before deploy when matcher/scoring changes.
- [ ] Run full local tests before commit.
- [ ] Deploy with `make deploy`.
- [ ] Verify container health and production HEAD.
- [ ] Rerun focused production audit after deploy.
- [ ] Capture unresolved findings into PMO or docs before stopping.

## Acceptance Criteria

- A maintainer can run one command to audit a known query set.
- Audit output explains top result quality without reading application code.
- Confirmed production issues have regression tests before fixes are merged.
- Cache version is updated when scoring or gate changes affect cached output.
- Documentation states which shorthand prices are accepted and which material
  terms are not prices.
- Production deploys remain gated by tests and health checks.
