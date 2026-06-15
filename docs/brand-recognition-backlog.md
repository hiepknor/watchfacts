# Brand Recognition Backlog

This backlog tracks brand-recognition gaps that are visible in audits but are
not yet proven to require taxonomy changes.

Core rule: do not add a brand alias just because a query contains a brand-like
token. Add taxonomy only when an audit shows that missing recognition causes at
least one of these outcomes:

- false positives that ranking or matching cannot otherwise explain;
- missed retrieval or result-count drift;
- top-result ranking drift;
- repeated user-visible issue reports for the same brand family.

## Addition Gate

Before adding or changing brand recognition:

1. Capture before evidence with `audit_quality.py`, including `query_plan`,
   `brand_candidates`, top result snippets, result count, and reason codes.
2. State the failure mode: false positive, missed retrieval, ranking drift, or
   recurring user issue.
3. Add taxonomy only in the existing rulebook data:
   `BRAND_ALIAS_RULES`, `COLLECTION_RULES`, `NICKNAME_RULES`, or
   `REFERENCE_GRAMMAR_RULES` in `app/searching/matcher_rulebook.py`.
4. Do not add scattered `if brand == ...` branches in parser, matcher, ranking,
   retrieval, MCP, or Telegram code.
5. Add focused tests for query intent and matching behavior.
6. Verify with the default benchmark and a focused audit for the affected query
   family.

Suggested verification:

```bash
python -m pytest tests/test_query_intent.py tests/test_matcher.py tests/test_search.py
python scripts/diagnostics/audit_quality.py "Fpj Elegante Titanium" --limit 5
make mcp-benchmark
```

If a brand change touches retrieval expansion, also run:

```bash
make mcp-cold-budget
```

## Backlog

| Candidate | Observed query | Current evidence | Status |
| --- | --- | --- | --- |
| FP Journe / FPJ | `FPJ Elegante Titanium` | Query plan now has `fp_journe` brand candidate and focused audits remain clean; no confirmed missed-retrieval or ranking regression. | Deferred until repeated issues show user-visible impact. |
| F.P. / RP Journe | `RP Journe Elegante Titanium` | Server query `RP Journe Elegante Titanium` returned no rows while `FPJ Elegante Titanium` was populated. | Brand alias + retrieval fallback added (`query plan -> fp_journe`, fallback query `fpj elegante titanium`), with targeted unit tests + focused search workflow assertion. |

## Accepted Change Template

Use this template in the search engine plan or commit notes before accepting a
brand taxonomy change:

```text
Brand:
Observed query family:
Before audit artifact:
Failure mode:
Rulebook data changed:
Tests added:
Default benchmark result:
Focused audit result:
Recall/result-count drift:
Decision:
```
