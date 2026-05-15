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


def test_detect_suspicious_result_ignores_price_before_currency_suffix() -> None:
    issues = detect_suspicious_result(
        listing_text="PP 5712G - 111k Usdt",
        raw_listing_text="PP 5712G - 111k Usdt",
    )

    assert issues == []


def test_detect_suspicious_result_flags_price_marker_suffix() -> None:
    issues = detect_suspicious_result(listing_text="5712R 2016 Price")

    assert [issue.reason for issue in issues] == ["ends_with_price_marker"]


def test_detect_suspicious_result_ignores_price_with_dollar_suffix() -> None:
    issues = detect_suspicious_result(
        listing_text="7118/1200R White Fullset 2023 /Good Price: 163.000$"
    )

    assert issues == []


def test_detect_suspicious_result_ignores_long_raw_when_segment_has_price() -> None:
    issues = detect_suspicious_result(
        listing_text="7118/1200R White 26/N2 HKD 1,445,000",
        raw_listing_text=(
            "Ready in HK 7118/1200R White 26/N2 HKD 1,445,000 "
            "7118/1200R Gold 26/N2 HKD 1,235,000"
        ),
    )

    assert issues == []


def test_detect_suspicious_result_flags_long_raw_without_price() -> None:
    issues = detect_suspicious_result(
        listing_text="7118/1200R white N2/2026",
        raw_listing_text=(
            "Ready in HK 7118/1200R white N2/2026 Hk1.45M "
            "7118/1200R champ N2/2026 Hk1.23M 5267/200A white HKD700k"
        ),
    )

    assert [issue.reason for issue in issues] == ["raw_much_longer"]
