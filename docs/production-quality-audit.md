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
  press "Show results" / "Load more" before result messages are sent.
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
- Production issue review should go through MCP tools first; SSH and
  direct database reads are deploy/emergency paths, not the normal review path.
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
scripts/diagnostics/audit_quality.py
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
- Support `--format text`, `--format json`, and `--format jsonl`.
- Support `--summarize-jsonl <path>` for DuckDB-backed stage-count summaries
  over saved audit artifacts.
- Support `--compare-jsonl <before> <after>` for DuckDB-backed before/after
  stage-count comparison.
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

1. Use MCP `list_issues`, `get_issue`, `suspicious_summary`, and
   `update_issue` for triage.
2. Classify the finding as `bad_extraction`, `wrong_reference`,
   `wrong_descriptor`, `bad_rank`, `missing_price`, `stale_cache`, or
   `source_lacks_info`.
3. Capture query, shown text, bounded raw context when available, seller,
   posted date, and expected behavior.
4. Add the smallest test that fails for the current bug.
5. Fix matcher, scoring, extraction, or gate logic.
6. Run focused tests.
7. Run full test suite.
8. Bump search cache version if output ordering, extraction, gating, scoring,
   or serialized result shape changes.
9. Deploy and rerun the audit query.
10. Mark the issue `fixed` only after verified deploy, or `ignored` only with a
    clear note that explains why no code change is needed.

Fixture sources:

- Use `scripts/diagnostics/audit_quality.py --format json` for ranking, quality-group,
  missing-price, and suspicious-result fixtures.
- Use `scripts/diagnostics/audit_quality.py --format jsonl` when the review
  needs raw -> parsed -> matched -> dedupe -> final stage evidence.
- Use `scripts/diagnostics/audit_quality.py --summarize-jsonl <path>` to
  summarize saved JSONL artifacts without WatchFacts credentials.
- Use `scripts/diagnostics/audit_quality.py --compare-jsonl before.jsonl after.jsonl`
  to capture changed stage counts for a matcher/scoring PR.
- Use `scripts/diagnostics/ai_audit_triage.py audit-report.jsonl` to produce a
  deterministic operator summary from a saved artifact, and add `--use-openai`
  only for optional offline AI classification of recurring issue patterns.
- Use `/issues_export` plus `scripts/fixtures/generate_issue_fixtures.py` for extraction
  fixtures that require full raw listing text.
- Use [docs/templates/audit-issue-fixture.json](templates/audit-issue-fixture.json)
  when manually documenting an audit finding before turning it into a test.

Generate a draft quality/scoring regression module from audit JSON:

```bash
python scripts/diagnostics/audit_quality.py "5712r" --format json --limit 10 > audit-report.json
python scripts/fixtures/generate_audit_fixtures.py audit-report.json > tests/test_audit_regressions.py
```

The same generator also accepts JSONL audit artifacts and uses `final_result`
events as fixture input:

```bash
python scripts/diagnostics/audit_quality.py "5205r green" --format jsonl --limit 10 > audit-report.jsonl
python scripts/fixtures/generate_audit_fixtures.py audit-report.jsonl > tests/test_audit_regressions.py
```

By default, the generator emits only non-clean rows. Add `--include-clean` when
you need to lock an accepted clean shorthand or a known-good ranking example.

MCP client maintainer prompt examples:

```text
List open WatchFacts issues.
View issue F15.
Classify this issue.
Propose a regression test from this issue, no code change yet.
Mark issue F15 fixed with notes: commit/deploy/audit.
Mark issue S8 ignored with note raw source lacks info.
```

## Non-Goals

- Replacing deterministic ranking with OpenAI ranking.
- Making the bot delete all incomplete results.
- Printing secrets or raw browser state in reports.
- Building a dashboard before the CLI audit flow is stable.
- Changing Telegram summary-first pagination.

## Implementation Checklist

### Phase 10.1: Audit Script

- [x] Add `scripts/diagnostics/audit_quality.py`.
- [x] Support query args and default query set.
- [x] Support `--limit`.
- [x] Include score fields and reason codes.
- [x] Keep output safe and bounded.
- [x] Include query intent, candidate decision, fuzzy, guardrail, and dedupe
  keep/drop metadata in JSONL artifacts.
- [x] Support DuckDB `--compare-jsonl` before/after reports.
- [x] Add tests for report formatting and score inclusion where practical.

### Phase 10.2: Fixture Capture Path

- [x] Document how to turn audit output into tests.
- [x] Reuse existing `/issues_export` and `scripts/fixtures/generate_issue_fixtures.py`
  where possible.
- [x] Add a fixture template for ranking, extraction, and missing-price cases.
- [x] Require a failing fixture before broad matcher/scoring changes.
- [x] Generate draft fixture tests from audit JSON and JSONL artifacts.

### Phase 10.3: Ambiguous Price Policy

- [x] Centralize accepted and rejected shorthand examples in docs.
- [x] Add regression tests for accepted shorthand that must stay clean.
- [x] Add regression tests for rejected material/karat terms.
- [x] Avoid tightening ambiguous shorthand without production evidence.

### Phase 10.4: Production Verification Loop

- [x] `make quality-audit` runs the default bounded audit set.
- [x] `make predeploy-quality-check` runs local checks plus audit.
- [x] MCP predeploy checks run the bounded audit gate.
- [x] Standard `make deploy` performs the normal production release, while
  `make deploy-mcp` handles MCP-only releases.
- [ ] Rerun focused production audit after deploy for the changed query class.
- [ ] Capture unresolved findings into PMO or docs before stopping when requested.

## Acceptance Criteria

- A maintainer can run one command to audit a known query set.
- Audit output explains top result quality without reading application code.
- Confirmed production issues have regression tests before fixes are merged.
- Cache version is updated when scoring or gate changes affect cached output.
- MCP clients can list, inspect, classify, and update issue status through MCP
  without SSH or direct SQLite access.
- Documentation states which shorthand prices are accepted and which material
  terms are not prices.
- Production deploys remain gated by tests and health checks.
