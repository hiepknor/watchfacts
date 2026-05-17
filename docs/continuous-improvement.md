# Continuous Improvement Spec

## Objective

Add an operator-driven feedback loop so the bot can collect bad or suspicious WatchFacts results over time, turn them into reviewable issue cases, and make future matcher/parser fixes faster and safer.

This feature must not let the bot rewrite production logic by itself. The bot should collect evidence, surface patterns, and produce regression fixtures for maintainers. Code changes still require tests, review, commit, and deploy.

## Problem Statement

Current matcher/parser quality improves only when the operator manually notices an incomplete or wrong result, explains it to a maintainer, and the maintainer creates a regression test. This is slow and easy to miss because:

- WatchFacts listings are inconsistent and often contain multiple products in one text block.
- Missing details can be subtle, such as a currency token without the following price.
- A bad extraction may only affect a subset of queries.
- Telegram result batches hide later examples unless the operator reviews many pages.

## Goals

- Let the operator report a bad result from Telegram with one tap.
- Preserve enough context to debug the issue later without exposing secrets.
- Automatically flag results that look likely to be incomplete.
- Provide owner commands to list, inspect, and export issue cases.
- Convert reviewed issue cases into deterministic tests or benchmark fixtures.
- Keep production behavior deterministic and auditable.
- Support a controlled OpenAI path where AI can suggest corrections for review or guarded refinement without becoming an uncontrolled source of truth.

## Non-Goals

- No autonomous code changes.
- No automatic production deployment from feedback.
- No storage of Telegram tokens, WatchFacts cookies, browser state, or passwords in issue records.
- No public feedback controls for unauthorized users.
- No LLM-only correction path as the first implementation.
- No AI-generated correction should bypass query/reference validation, confidence gates, or regression coverage.
- No local model runtime path in the supported production architecture.

## User Roles

- Telegram user: receives result batches and can report a visible issue.
- Owner/operator: receives alerts, reviews issue cases, exports data, and decides what needs fixing.
- Maintainer: converts high-quality issue cases into regression tests and code changes.

## Feature Categories

### 1. Result Feedback Buttons

Every sent result should optionally include inline feedback controls:

| Button | Meaning | Stored reason |
| --- | --- | --- |
| `✅ Đúng` | Result looks correct | `correct` |
| `⚠️ Thiếu thông tin` | Listing was relevant but extracted text missed details | `missing_info` |
| `❌ Sai kết quả` | Listing does not match the query | `wrong_result` |

Initial implementation can show only the negative buttons to reduce Telegram noise:

- `⚠️ Thiếu thông tin`
- `❌ Sai kết quả`

When a button is clicked:

- Only authorized users may submit feedback.
- Bot acknowledges with a short Vietnamese message.
- Feedback is stored locally in SQLite.
- The callback must not expose raw cookies, tokens, or browser state.
- Duplicate taps on the same result by the same user should increment or update the existing feedback rather than creating noisy duplicates.

Recommended acknowledgement messages:

```text
📝 Đã ghi nhận

Mình đã lưu case này để owner review sau.
```

```text
🔁 Case này đã được ghi nhận trước đó

Mình đã cập nhật lượt report.
```

### 2. Issue Store In SQLite

Add persistent tables for result feedback and suspicious result flags.

Recommended tables:

#### `result_feedback`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `created_at` | text | ISO timestamp |
| `updated_at` | text | ISO timestamp |
| `query_text` | text | Original query as typed |
| `normalized_query` | text | Matcher-normalized query |
| `result_rank` | integer | Rank within current search results |
| `reason` | text | `missing_info`, `wrong_result`, `correct`, or future reason |
| `report_count` | integer | Number of times this case was reported |
| `telegram_user_id` | integer nullable | Reporter ID when available |
| `listing_text` | text | Text shown to the user |
| `raw_listing_text` | text nullable | Original candidate text before scoping, when available |
| `seller` | text nullable | Seller display value |
| `posted_date` | text nullable | Posted date display value |
| `source_url` | text nullable | WatchFacts source URL or stable identifier |
| `issue_status` | text | `open`, `reviewed`, `fixed`, `ignored` |
| `review_notes` | text nullable | Maintainer notes |

#### `suspicious_results`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Local ID |
| `created_at` | text | ISO timestamp |
| `query_text` | text | Original query |
| `result_rank` | integer | Rank within current search results |
| `reason` | text | Suspicion code |
| `severity` | integer | 1 low, 2 medium, 3 high |
| `listing_text` | text | Text shown to the user |
| `raw_listing_text` | text nullable | Raw candidate text when available |
| `source_url` | text nullable | WatchFacts source URL |
| `reviewed_at` | text nullable | Set when owner reviews |

Retention:

- Keep issue data local by default.
- Do not auto-delete open issues.
- Future pruning may archive old reviewed/fixed issues after a configurable retention window.

### 3. Owner Review Commands

Add owner-only Telegram commands.

| Command | Purpose |
| --- | --- |
| `/issues` | Show open feedback and suspicious cases |
| `/issues missing` | Filter missing-info reports |
| `/issues wrong` | Filter wrong-result reports |
| `/issue <id>` | Show one issue in detail |
| `/issue_done <id>` | Mark issue as reviewed/fixed after maintainer action |
| `/issue_ignore <id>` | Ignore a false positive |
| `/issues_export` | Export open issue cases as JSON or text |

Implemented command set:

- `/issues`
- `/issue <id>`
- `/issue_done <id>`
- `/issue_ignore <id>`
- `/issues_export`

Owner command output should be Vietnamese and visual:

```text
🧾 Issue cần review

#42 ⚠️ Thiếu thông tin
🔎 Query: 5712r
🏷️ Bot gửi: 5712R 2016/ HKD
👤 Seller: AM.Timepiece TONY
🔗 Source: /flash-sales/9927122
📊 Report: 3 lượt
```

The export format should be deterministic and test-friendly:

```json
{
  "query": "5712r",
  "reason": "missing_info",
  "shown_text": "5712R 2016/ HKD",
  "raw_text": "5712R 2016/ HKD 830000",
  "seller": "AM.Timepiece TONY",
  "source_url": "/flash-sales/9927122"
}
```

### 4. Automatic Suspicious Result Detection

The bot should flag results that look likely to be incomplete even when the operator does not tap feedback.

Initial suspicious rules:

| Code | Trigger | Severity |
| --- | --- | --- |
| `ends_with_currency` | Extracted text ends with `HKD`, `USD`, `USDT`, `EUR`, `AED`, `CHF` | 3 |
| `ends_with_price_marker` | Extracted text ends with `Price`, `$`, `💰`, `💲`, or similar marker | 3 |
| `raw_much_longer` | Raw text is much longer than shown text and contains query/reference nearby | 2 |
| `trailing_separator` | Extracted text ends with dangling item marker or separator | 2 |
| `missing_price_after_currency` | Raw has currency followed by a long number but shown text omits the number | 3 |
| `too_short_reference_only` | Shown result is only a reference plus one or two tokens while raw has more local details | 1 |

Rules should run after parser/matcher scoping and before Telegram pagination storage.

Suspicious flags should:

- Store local issue records.
- Appear in `/issues`.
- Optionally add a subtle owner-only hint in result summaries, not in every user-facing result.
- Avoid blocking result delivery.

Example summary addition for owner chats:

```text
🧪 Bot đã tự đánh dấu 4 kết quả cần review.
Gõ /issues để xem.
```

### 5. Regression And Benchmark Loop

Feedback should become reusable test coverage.

Recommended workflow:

1. Operator reports or bot auto-flags a case.
2. Owner reviews with `/issue <id>`.
3. Owner exports with `/issues_export`.
4. Maintainer converts the case into:
   - a unit test in `tests/test_matcher.py`, or
   - a parser fixture, or
   - a benchmark hard case in `scripts/benchmark_hard_cases.py`.
5. Maintainer implements the smallest deterministic fix.
6. Full test suite runs before commit and deploy.
7. Issue is marked `fixed` or `ignored`.

Generate a draft matcher regression test from an exported JSON payload:

```bash
python scripts/generate_issue_fixtures.py issues.json > /tmp/test_exported_issues.py
```

The script also accepts the Telegram `/issues_export` message from stdin, including
the surrounding Markdown code fence.

The benchmark corpus should include:

- Query.
- Raw listing text.
- Expected extracted listing text.
- Required inclusion tokens.
- Forbidden truncation patterns.
- Source URL when available.

Example fixture shape:

```json
{
  "query": "5712r",
  "raw_text": "✅PP ❣️5711R Watch and Service paper, HKD 605000 ❣️5712R 2016/ HKD 830000 ❣️5134R Service paper, HKD 130000",
  "expected_text": "5712R 2016/ HKD 830000",
  "must_include": ["HKD", "830000"],
  "must_not_end_with": ["HKD"]
}
```

## Product Requirements

- Feedback controls must work inside Telegram pagination batches.
- Feedback must be tied to the exact result shown to the user.
- Feedback must remain useful after result page callbacks expire.
- Owner commands must be restricted by `TELEGRAM_ALLOWED_USER_IDS`.
- Owner commands must not reveal secrets.

## OpenAI Controlled Intelligence

The long-term improvement direction is a controlled OpenAI system: deterministic matcher/parser first, OpenAI only as a second-opinion/refiner for cases that are already suspicious, reported, or hard to scope.

This is not autonomous learning. The bot should not rewrite code, deploy changes, or blindly trust an AI response. AI suggestions are evidence that must pass guards and become tests.

Local model support is intentionally out of scope for the supported runtime. Keeping one AI provider reduces deployment complexity, avoids local model memory/CPU requirements, and makes validation, metrics, and operator documentation clearer.

### Operating Modes

| Mode | Behavior | User-facing impact |
| --- | --- | --- |
| `off` | No AI refinement or suggestions | Deterministic behavior only |
| `shadow` | OpenAI proposes alternative extraction for suspicious cases; bot records diff | No Telegram output changes |
| `review` | OpenAI suggestions appear in owner issue review/digest | Owner can approve/ignore |
| `guarded` | OpenAI correction can be used only when strict confidence gates pass | Limited user-facing correction |

Initial rollout should use `shadow`, then `review`. `guarded` requires enough reviewed fixtures and production evidence.

### Confidence Gates

An AI-suggested listing text can only be considered if all of these are true:

- It contains the query reference or a normalized equivalent.
- It preserves required query descriptors when present.
- It includes local evidence from the raw listing text, not invented text.
- It does not cross a known item separator or product-header boundary.
- It improves a concrete issue signal such as missing price, truncated date, or dangling currency.
- It does not include secrets, cookies, full page HTML, or unrelated user data.
- It is bounded by length and Telegram formatting limits.

If any gate fails, the suggestion is stored only as review evidence or discarded.

### AI Review Artifacts

For each AI suggestion, store only safe, minimal data:

- Query.
- Shown deterministic text.
- Raw listing text snippet already available in the issue record.
- Suggested corrected text.
- Reason codes and confidence score.
- Gate results.
- Source issue id or query history id.

Do not store prompts containing secrets or full browser state. Do not send `.env`, cookies, tokens, or full HTML pages to any model.

### Owner Workflow

Recommended workflow:

1. Deterministic search runs as usual.
2. Suspicious detector flags risky results.
3. OpenAI refiner runs only on flagged snippets when enabled.
4. Bot records `deterministic_text` vs `suggested_text`.
5. Owner reviews grouped suggestions in a digest or issue command.
6. Maintainer converts approved suggestions into regression tests.
7. Deterministic matcher/parser is updated where possible.
8. Guarded AI use is considered only for patterns that cannot be handled cleanly by deterministic rules.

### Success Metrics

- Fewer open feedback issues per week.
- Lower false-positive rate in `suspicious_results`.
- More regression tests generated per owner review session.
- Fewer repeated reports for the same extraction pattern.
- OpenAI suggestions accepted by owner at a high enough rate to justify the added complexity.

### Safety Requirements

- OpenAI must be optional and disabled by default.
- Search must still work when OpenAI is unavailable or times out.
- OpenAI timeout must be short enough not to block normal Telegram UX.
- Every AI-assisted user-facing correction must be explainable from raw listing text.
- Reviewed AI suggestions should strengthen deterministic tests rather than replace them.
- The bot must continue sending search results even if feedback storage fails; storage failures should be logged safely.
- Suspicious detection must be deterministic and unit-tested.

## Technical Requirements

- Store stable issue records in SQLite.
- Add schema migrations in `app/db.py` without breaking existing `bot.db`.
- Use parameterized SQL.
- Generate short callback tokens and store result context in bot memory and/or SQLite.
- Ensure Telegram callback data stays within platform limits.
- Do not store raw WatchFacts browser state in issue tables.
- Do not store full HTML response unless explicitly approved later.
- Prefer raw listing text over full page HTML for debugging.
- Keep user-facing messages in Vietnamese.

## Suggested Implementation Phases

### Phase A: Feedback Persistence

- Add feedback tables.
- Add database methods to record feedback and list open issues.
- Add feedback buttons to result messages.
- Add tests for callback authorization and duplicate reporting.

### Phase B: Owner Issue Review

- Add `/issues`, `/issue <id>`, and `/issues_export`.
- Add pagination for issue lists.
- Add tests for owner-only access and safe formatting.

### Phase C: Suspicious Detection

- Add deterministic suspicious rules.
- Persist auto-flags.
- Surface a summary hint to owner chats.
- Add matcher/search tests for known truncation patterns.

### Phase D: Regression Export

- Add export format for test fixtures.
- Add docs for converting issue exports to tests.
- Optionally add a script to generate draft test cases from exported JSON.

### Phase E: OpenAI Controlled Refinement

- Remove the local model runtime surface.
- Add OpenAI configuration and a stub-testable refiner provider.
- Use structured output and local validation gates for every suggestion.
- Record shadow suggestions and review artifacts in SQLite.
- Enable guarded output only for validated, well-tested cases.

## Acceptance Criteria

- A user can report a bad result with one tap.
- Owner can list and inspect reported cases in Telegram.
- Suspicious truncation patterns are stored without user input.
- Exported issue data is enough to create a regression test.
- No secrets appear in feedback tables, logs, Telegram messages, or exports.
- Full test suite and `make check` pass.

## Open Questions

- Should feedback buttons be visible to every authorized user or owner-only?
- Should “correct” feedback be stored initially, or only negative feedback?
- Should issue records include a small raw snippet only, or full raw listing text?
- Should issue export be sent as Telegram text or document attachment?
- Should suspicious flags be shown in result summaries only for owners?
