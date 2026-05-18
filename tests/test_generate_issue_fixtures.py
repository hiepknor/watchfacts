from scripts.generate_issue_fixtures import load_exported_issues, render_pytest_module


def test_load_exported_issues_accepts_telegram_code_fence() -> None:
    issues = load_exported_issues(
        """
📤 Export issue regression

```json
[
  {
    "id": 1,
    "type": "suspicious",
    "query": "5712r",
    "reason": "ends_with_currency",
    "shown_text": "5712R 2016/ HKD",
    "raw_text": "5712R 2016/ HKD 830000",
    "source_url": "/flash-sales/9927122"
  }
]
```
"""
    )

    assert issues[0]["query"] == "5712r"
    assert issues[0]["raw_text"] == "5712R 2016/ HKD 830000"


def test_render_pytest_module_uses_raw_text_as_expected_for_missing_info() -> None:
    text = render_pytest_module(
        [
            {
                "id": 26,
                "type": "feedback",
                "query": "5712r",
                "reason": "missing_info",
                "shown_text": "5712R 2016/ HKD",
                "raw_text": "5712R 2016/ HKD 830000",
                "source_url": "/flash-sales/9927122",
            }
        ]
    )

    assert "extract_relevant_listing_text" in text
    assert "'name': 'exported_issue_feedback_26_missing_info_5712r'" in text
    assert "'expected_text': '5712R 2016/ HKD 830000'" in text


def test_render_pytest_module_uses_reviewed_ai_suggestion_as_expected_text() -> None:
    text = render_pytest_module(
        [
            {
                "id": 7,
                "type": "ai_suggestion",
                "query": "Fpj Elegante Titanium",
                "reason": "ai_reviewed_refinement",
                "shown_text": "FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
                "raw_text": "FPJ quantieme - [ ] FPJ Elegante Titanium 120k",
                "suggested_text": "FPJ Elegante Titanium 120k",
            }
        ],
        case_prefix="reviewed_ai",
    )

    assert "'name': 'reviewed_ai_ai_suggestion_7_ai_reviewed_refinement_fpj_elegante_titanium'" in text
    assert "'expected_text': 'FPJ Elegante Titanium 120k'" in text
