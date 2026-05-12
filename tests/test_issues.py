from app.issues import detect_suspicious_result


def test_detect_suspicious_result_flags_standalone_currency_suffix() -> None:
    issues = detect_suspicious_result(
        listing_text="5712R 2016/ HKD",
        raw_listing_text="5712R 2016/ HKD 830000",
    )

    assert {issue.reason for issue in issues} >= {
        "ends_with_currency",
        "missing_price_after_currency",
    }


def test_detect_suspicious_result_ignores_complete_currency_price() -> None:
    issues = detect_suspicious_result(
        listing_text="5712R 2016/ HKD 830000",
        raw_listing_text="5712R 2016/ HKD 830000",
    )

    assert issues == []


def test_detect_suspicious_result_flags_price_marker_suffix() -> None:
    issues = detect_suspicious_result(listing_text="5712R 2016 Price")

    assert [issue.reason for issue in issues] == ["ends_with_price_marker"]
