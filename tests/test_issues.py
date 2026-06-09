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


def test_detect_suspicious_result_ignores_plain_full_price() -> None:
    issues = detect_suspicious_result(
        listing_text="FPJ Elegante Titanium 48mm 2019 full set 780000",
        raw_listing_text=(
            "126500 black n2 $239000 "
            "FPJ Elegante Titanium 48mm 2019 full set 780000 "
            "G0A23172 10/2025 680000"
        ),
    )

    assert issues == []


def test_detect_suspicious_result_ignores_currency_with_colon() -> None:
    issues = detect_suspicious_result(
        listing_text="Used 07-01 wg snow red lip 23y full set HKD:2400000",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_euro_text() -> None:
    issues = detect_suspicious_result(
        listing_text="Audemars Royal Oak with 92 k euro",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_symbol_followed_by_text() -> None:
    issues = detect_suspicious_result(
        listing_text="2026 79360N New complete $9500shipped - NYC",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_numeric_nfc_price() -> None:
    issues = detect_suspicious_result(
        listing_text="228235a Choco New N2/2026 = 465 NFC",
        raw_listing_text=(
            "228235a Eisen y23 = 478 NFC "
            "228235a Choco New N2/2026 = 465 NFC "
            "228235a MOP New N2/2026 = 470 NFC"
        ),
    )

    assert issues == []


def test_detect_suspicious_result_ignores_bare_k_price() -> None:
    issues = detect_suspicious_result(
        listing_text="228235a Choco N3 470k",
        raw_listing_text="228235a Choco N3 470k",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_keycap_digit_currency_price() -> None:
    issues = detect_suspicious_result(
        listing_text="5726/1A Yours for 1️⃣2️⃣4️⃣,9️⃣8️⃣5️⃣ USD + insured shipping",
        raw_listing_text="5726/1A Yours for 1️⃣2️⃣4️⃣,9️⃣8️⃣5️⃣ USD + insured shipping",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_keycap_shorthand_price() -> None:
    issues = detect_suspicious_result(
        listing_text="Ref# 5205R-011 Comes box and papers 2023 Yours for 5️⃣3️⃣,5️⃣ shipped",
        raw_listing_text=(
            "PP Annual Calendar Green Ref# 5205R-011 Comes box and papers "
            "2023 Yours for 5️⃣3️⃣,5️⃣ shipped"
        ),
    )

    assert issues == []


def test_detect_suspicious_result_ignores_duplicated_currency_prefix() -> None:
    issues = detect_suspicious_result(
        listing_text="5726/1A N11/25 Like new HKDHKD1.06m",
        raw_listing_text="5726/1A N11/25 Like new HKDHKD1.06m",
    )

    assert issues == []


def test_detect_suspicious_result_flags_missing_price_evidence() -> None:
    issues = detect_suspicious_result(
        listing_text="26240BA new 2024",
        raw_listing_text="I have 26240BA new 2024",
    )

    assert [issue.reason for issue in issues] == ["missing_price_evidence"]


def test_detect_suspicious_result_does_not_treat_karat_gold_as_price() -> None:
    issues = detect_suspicious_result(
        listing_text=(
            "5712R Patek Philippe original movement customized "
            "18k rose gold case negotiable available for reservation"
        ),
        raw_listing_text=(
            "5712R Patek Philippe original movement customized "
            "18k rose gold case negotiable available for reservation"
        ),
    )

    assert [issue.reason for issue in issues] == ["missing_price_evidence"]


def test_detect_suspicious_result_does_not_treat_reference_as_plain_price() -> None:
    issues = detect_suspicious_result(
        listing_text="116500 panda Daytona 2017 full link retail ready",
        raw_listing_text="116500 panda Daytona 2017 full link retail ready",
    )

    assert [issue.reason for issue in issues] == ["missing_price_evidence"]


def test_detect_suspicious_result_ignores_comma_thousands_price() -> None:
    issues = detect_suspicious_result(
        listing_text="116500 panda watch only -3 links 27,500",
        raw_listing_text="116500 panda watch only -3 links 27,500",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_comma_price_with_label_suffix() -> None:
    issues = detect_suspicious_result(
        listing_text="116500 PANDA RETAIL READY FULL LINK 2022 30,450+lbl",
        raw_listing_text="116500 PANDA RETAIL READY FULL LINK 2022 30,450+lbl",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_tilde_joined_currency_prices() -> None:
    issues = detect_suspicious_result(
        listing_text="7118/1200R White 2021 1.210.000 HKD~155.200 USD"
    )

    assert issues == []


def test_detect_suspicious_result_ignores_decimal_price_with_lnl_suffix() -> None:
    issues = detect_suspicious_result(
        listing_text="116500 panda Daytona 2017 full link retail ready 28.9+lnl",
        raw_listing_text="116500 panda Daytona 2017 full link retail ready 28.9+lnl",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_euro_symbol_price() -> None:
    issues = detect_suspicious_result(
        listing_text="5990/1R 2026, 248 € inc shipment",
        raw_listing_text="Brand new w stickers 5990/1R 2026, 248 € inc shipment",
    )

    assert issues == []


def test_detect_suspicious_result_ignores_best_price_tail_with_prices() -> None:
    issues = detect_suspicious_result(
        listing_text="5712/1R fress date 265,000 hkd or 32,800 usd deal to best price",
        raw_listing_text=(
            "Patek Phillippe 5712/1R fress date 265,000 hkd or "
            "32,800 usd deal to best price"
        ),
    )

    assert issues == []


def test_detect_suspicious_result_ignores_condition_price_before_currency() -> None:
    issues = detect_suspicious_result(listing_text="7118/1r white N3-985k hkd")

    assert issues == []


def test_detect_suspicious_result_ignores_money_marker_price_before_currency() -> None:
    issues = detect_suspicious_result(
        listing_text="5168g New button 2020y full set 💰84000 USDT"
    )

    assert issues == []


def test_detect_suspicious_result_ignores_currency_symbol_after_abbrev_price() -> None:
    issues = detect_suspicious_result(
        listing_text="5168G. Blue 2023 Full Set 738,000HK$ 94,600US$"
    )

    assert issues == []


def test_detect_suspicious_result_ignores_other_raw_currency_prices_when_shown_has_price() -> None:
    issues = detect_suspicious_result(
        listing_text="5168G-010 2022 full set HKD 688000",
        raw_listing_text=(
            "5168G-010 2022 full set HKD 688000 "
            "5712G 2015 full set HkD 593000"
        ),
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
