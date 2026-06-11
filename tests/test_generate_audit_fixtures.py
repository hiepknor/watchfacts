from __future__ import annotations

from scripts.fixtures.generate_audit_fixtures import (
    audit_reports_to_cases,
    load_audit_reports,
    render_pytest_module,
)


def test_load_audit_reports_accepts_json_code_fence() -> None:
    reports = load_audit_reports(
        """
Audit report

```json
[
  {
    "query": "5712r",
    "result_count": 1,
    "rows": []
  }
]
```
"""
    )

    assert reports[0]["query"] == "5712r"


def test_load_audit_reports_accepts_jsonl_final_result_events() -> None:
    reports = load_audit_reports(
        """
{"type":"query_summary","query":"5205r green","result_count":1}
{"type":"final_result","query":"5205r green","stage":"final","text_snippet":"5205R black $428000","posted_date":"May 17, 2026","reason_codes":["guardrail.descriptor_conflict"],"fuzzy_score":72}
"""
    )

    assert reports == [
        {
            "query": "5205r green",
            "rows": [
                {
                    "listing_text": "5205R black $428000",
                    "posted_date": "May 17, 2026",
                    "quality_group": 1,
                    "price_evidence_score": 0,
                    "suspicious_reasons": [],
                    "score_reasons": ["guardrail.descriptor_conflict"],
                }
            ],
        }
    ]


def test_load_audit_reports_strips_jsonl_suspicious_reason_prefix() -> None:
    reports = load_audit_reports(
        """
{"type":"final_result","query":"5712r","stage":"final","text_snippet":"5712R 18k rose gold case reservation","posted_date":"May 17, 2026","reason_codes":["quality.missing_price","suspicious.missing_price_evidence","price.missing_visible"]}
"""
    )

    assert reports[0]["rows"][0]["suspicious_reasons"] == [
        "missing_price_evidence"
    ]


def test_audit_reports_to_cases_defaults_to_non_clean_rows() -> None:
    cases = audit_reports_to_cases(
        [
            {
                "query": "5712r",
                "rows": [
                    {
                        "rank": 1,
                        "quality_group": 0,
                        "price_evidence_score": 1,
                        "suspicious_reasons": [],
                        "listing_text": "5712R HKD 820000",
                    },
                    {
                        "rank": 2,
                        "quality_group": 1,
                        "price_evidence_score": 0,
                        "suspicious_reasons": ["missing_price_evidence"],
                        "listing_text": "5712R 18k rose gold case reservation",
                        "posted_date": "May 17, 2026",
                    },
                ],
            }
        ],
        case_prefix="audit",
        include_clean=False,
    )

    assert len(cases) == 1
    assert cases[0]["name"] == "audit_1_2_missing_price_evidence_5712r"
    assert cases[0]["expected_quality_group"] == 1
    assert cases[0]["expected_suspicious_reasons"] == ["missing_price_evidence"]
    assert cases[0]["expected_score_reasons"] == []


def test_audit_reports_to_cases_can_include_clean_rows() -> None:
    cases = audit_reports_to_cases(
        [
            {
                "query": "5205r green",
                "rows": [
                    {
                        "quality_group": 0,
                        "price_evidence_score": 1,
                        "suspicious_reasons": [],
                        "listing_text": "5205R green HKD428000",
                    }
                ],
            }
        ],
        case_prefix="audit",
        include_clean=True,
    )

    assert len(cases) == 1
    assert cases[0]["expected_quality_group"] == 0
    assert cases[0]["expected_price_evidence_score"] == 1
    assert cases[0]["expected_score_reasons"] == []


def test_render_pytest_module_outputs_quality_regression_test() -> None:
    module = render_pytest_module(
        [
            {
                "query": "5712r",
                "rows": [
                    {
                        "quality_group": 1,
                        "price_evidence_score": 0,
                        "suspicious_reasons": ["missing_price_evidence"],
                        "listing_text": "5712R 18k rose gold case reservation",
                    }
                ],
            }
        ],
        case_prefix="audit",
    )

    assert "def test_audit_quality_regression(case):" in module
    assert "score_result(result, original_rank=0, query=case[\"query\"])" in module
    assert "'expected_quality_group': 1" in module
    assert "'expected_suspicious_reasons': [" in module
    assert "for reason in case[\"expected_score_reasons\"]" in module
