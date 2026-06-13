from dataclasses import dataclass

import pytest

from app.matcher import (
    RULEBOOK,
    extract_relevant_listing_text,
    explain_extraction,
    filter_matching_listings,
    listing_matches,
    normalize_text,
    tokenize_query,
)
from app.matcher_aliases import canonicalize_descriptor_token
from app.matcher_token_classification import parse_query_terms


@dataclass(frozen=True)
class Listing:
    listing_text: str


def test_normalize_text_is_case_insensitive_and_punctuation_tolerant() -> None:
    assert normalize_text("  Patek 228253A, CHOCO!  ") == "patek 228253a choco"


def test_tokenize_query_extracts_model_reference_and_descriptor() -> None:
    assert tokenize_query("228253A choco") == ["228253a", "choco"]
    assert tokenize_query("7118/1200A 25.5k") == ["7118/1200a", "25.5k"]


def test_normalize_text_compacts_keycap_digit_prices() -> None:
    assert normalize_text("5164a Watch Only $8️⃣0️⃣k") == "5164a watch only 80k"


def test_listing_matches_requires_all_query_tokens() -> None:
    assert listing_matches("228253a choco", "Rolex 228253A dial CHOCO full set")
    assert not listing_matches("228253a choco", "Rolex 228253A silver dial full set")


def test_listing_matches_alias_query_cho_to_choco() -> None:
    assert listing_matches("228235a cho", "Rolex 228235A choco N2 467000hkd full set")
    assert listing_matches("228235a choco", "Rolex 228235A cho N2 467000hkd full set")


def test_tokenize_query_alias_query_meteorite_to_mete() -> None:
    assert tokenize_query("228349RBR meteorite") == ["228349rbr", "mete"]


def test_listing_matches_alias_query_meteorite_to_mete() -> None:
    assert listing_matches(
        "228349rbr meteorite",
        "AP 228349RBR mete 100k full set",
    )


@pytest.mark.parametrize(
    ("query", "expected_token", "listing_text"),
    [
        ("228235a cho", "cho", "Rolex 228235A choco N2 467000hkd full set"),
        ("228349RBR meteorite", "meteorite", "AP 228349RBR mete 100k full set"),
        ("116500 grey", "grey", "116500 grey 30.5k"),
    ],
)
def test_descriptor_aliases_apply_to_multiple_queries(
    query: str,
    expected_token: str,
    listing_text: str,
) -> None:
    expected_tokens = tokenize_query(query)
    assert canonicalize_descriptor_token(expected_token) in expected_tokens
    assert listing_matches(query, listing_text)


def test_descriptor_aliases_are_global_tokens() -> None:
    assert canonicalize_descriptor_token("cho") == "choco"
    assert canonicalize_descriptor_token("meteorite") == "mete"
    assert canonicalize_descriptor_token("grey") == "gray"
    assert canonicalize_descriptor_token("rosegold") == "rg"
    assert canonicalize_descriptor_token("rose-gold") == "rg"
    assert canonicalize_descriptor_token("whitegold") == "wg"
    assert canonicalize_descriptor_token("white-gold") == "wg"
    assert canonicalize_descriptor_token("mother-of-pearl") == "mop"


def test_parse_query_terms_canonicalizes_compound_descriptor_phrases() -> None:
    assert parse_query_terms("rm07-01 rose gold") == ([["rm07-01"]], ["rg"])
    assert parse_query_terms("rm07-01 white gold snow") == (
        [["rm07-01"]],
        ["wg", "snow"],
    )
    assert parse_query_terms("rm07-01 mother of pearl") == (
        [["rm07-01"]],
        ["mop"],
    )
    assert tokenize_query("rm07-01 rose gold") == ["rm07-01", "rg"]
    assert tokenize_query("rm07-01 mother of pearl") == ["rm07-01", "mop"]


def test_listing_matches_material_alias_phrases_near_reference() -> None:
    assert listing_matches(
        "rm07-01 rg snow",
        "RM07-01 Rose Gold Diamonds Snow Setting Red Jasper full set USD328000",
    )
    assert listing_matches(
        "rm07-01 rg snow",
        "RM07-01 Rosegold Snow Diamonds Red Lips Good Condition 260000US",
    )
    assert not listing_matches(
        "rm07-01 rg snow",
        "RM07-01 WG Snow Onyx N4-26 360000 USDT",
    )


def test_listing_matches_material_phrase_queries_against_abbreviated_listings() -> None:
    assert listing_matches(
        "rm07-01 rose gold snow",
        "RM07-01 RG Snow Diamonds Red Lips Good Condition 260000US",
    )
    assert not listing_matches(
        "rm07-01 rose gold snow",
        "RM07-01 WG Snow Onyx N4-26 360000 USDT",
    )
    assert listing_matches(
        "rm07-01 white gold snow",
        "RM07-01 WG Snow Diamond MOP N5/2026 usdt480",
    )
    assert not listing_matches(
        "rm07-01 white gold snow",
        "RM07-01 Rosegold Snow Diamonds Red Lips Good Condition 260000US",
    )
    assert listing_matches(
        "rm07-01 mother of pearl",
        "RM07-01 White Ceramic MOP N4/2026 usdt470k",
    )


def test_parse_query_terms_ignores_connector_tokens() -> None:
    assert parse_query_terms("15510 or blue") == ([["15510or"]], ["blue"])
    assert listing_matches(
        "15510 or blue",
        "15510OR Blue dial 2024 Fullset 94k",
    )
    assert not listing_matches("15510 or blue", "15510OR New 92k")


def test_parse_query_terms_keeps_compact_reference_and_descriptor() -> None:
    assert parse_query_terms("15510or blue") == ([["15510or"]], ["blue"])


def test_parse_query_terms_does_not_merge_reference_with_or_when_followed_by_year() -> None:
    assert parse_query_terms("15510 or 2026") == ([["15510"]], ["2026"])


def test_listing_matches_reference_tokens_across_punctuation() -> None:
    assert listing_matches("228253a choco", "Rolex 228-253A dial CHOCO full set")


def test_listing_matches_reference_needs_local_proximity_before_descriptor_match() -> None:
    assert not listing_matches(
        "228349rbr mete",
        "AP 228 349 r br 100k mete n2 / 2026",
    )


def test_listing_matches_decimal_price_as_single_token() -> None:
    assert listing_matches("25.5k", "116500 blk2023 glass scratch 25.5k")
    assert not listing_matches("25.5k", "116500 Black Used USD25,460")


def test_listing_matches_keycap_digit_price_descriptor() -> None:
    assert listing_matches("5164a 80k", "5164a Watch Only $8️⃣0️⃣k")


def test_listing_matches_treats_query_year_as_descriptor() -> None:
    listing_text = "🏷️ 26240BA new 2024 💎💫👑✨ 👤 member 932184 📅 25/03/2026"

    assert listing_matches("26240ba new 2024", listing_text)


def test_listing_matches_requires_descriptors_near_reference_token() -> None:
    stock_list = (
        "128348 Carnelian $561000 N10 134300 pistachio $78500 N3 "
        "228235A EIS $541000 N3 228235 green $433000 N12 "
        "278271g choco jub $162500 N10"
    )

    assert not listing_matches("228235a choco", stock_list)


def test_listing_matches_allows_descriptors_near_exact_reference_token() -> None:
    assert listing_matches(
        "228235a choco",
        "Rolex 228235A choco N2 467000hkd full set",
    )


def test_listing_matches_compound_reference_with_slash() -> None:
    assert listing_matches(
        "7118/1200a blue",
        "Patek 7118/1200A blue 02-26 $732k",
    )


def test_listing_matches_compound_reference_does_not_steal_later_descriptor() -> None:
    stock_list = (
        "7118/1200A gray 04-26 $787k "
        "7300/1200R blue 03-26 $366k"
    )

    assert not listing_matches("7118/1200a blue", stock_list)


def test_listing_matches_rejects_query_descriptor_in_next_reference_group() -> None:
    assert not listing_matches(
        "228235a blue",
        "228235A black full set 2025 fullset Used blue 228235B White 4/2026 New",
    )


def test_extract_relevant_listing_text_returns_exact_stock_list_segment() -> None:
    stock_list = (
        "134300 pistachio $78500 N3 "
        "228235A EIS $541000 N3 228235 green $433000 N12 "
        "278271g choco jub $162500 N10"
    )

    assert extract_relevant_listing_text("228235a eis", stock_list) == (
        "228235A EIS $541000 N3"
    )


def test_extract_relevant_listing_text_scopes_descriptor_only_query() -> None:
    stock_list = (
        "FPJ Elegante titanium ti 48mm G0A23172 10/2025 680000 "
        "642.OX.0118.Lr.0999 1/2026 410000 "
        "126618lb n2 335000"
    )

    assert extract_relevant_listing_text("Fpj Elegante Titanium", stock_list) == (
        "FPJ Elegante titanium ti 48mm G0A23172 10/2025 680000"
    )


def test_extract_relevant_listing_text_scopes_descriptor_only_multilist_separator() -> None:
    listing_text = (
        "FPJ quantieme perpetuel platinum 2022 used Fullset $298,500USD - [ ] "
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd - [ ] "
        "FPJ Rose Gold CS opendate watch"
    )

    assert extract_relevant_listing_text("Fpj Elegante Titanium", listing_text) == (
        "FPJ Elegante Titanium White 48mm 2022 Used Fullset 120,000usd"
    )


def test_extract_relevant_listing_text_keeps_fpj_elegante_price_and_drops_trailing_brand_alias() -> None:
    listing_text = "FPJ Elegante Titanium 48mm White Used 2022 Fullset HKD895,000 / USD116,300 PP ⚡"

    assert extract_relevant_listing_text("Fpj Elegante Titanium", listing_text) == (
        "FPJ Elegante Titanium 48mm White Used 2022 Fullset HKD895,000 / USD116,300"
    )


def test_extract_relevant_listing_text_keeps_plain_price_after_full_set() -> None:
    listing_text = (
        "FPJ Elegante Titanium 48mm 2019 full set 780000 "
        "G0A23172 10/2025 680000 642.OX.0118.Lr.0999 1/2026 420000"
    )

    assert extract_relevant_listing_text("Fpj Elegante Titanium", listing_text) == (
        "FPJ Elegante Titanium 48mm 2019 full set 780000"
    )


def test_extract_relevant_listing_text_keeps_compact_size_year_detail() -> None:
    listing_text = (
        "FPJ Elegante titanium ti 48mm2019 used 780000\n"
        "RM029 RG 2017 used Fullset \n"
        "HK$1090000\n\n"
        "G0A23172 10/2025 680000\n"
        "642.OX.0118.Lr.0999 1/2026 410000"
    )

    assert extract_relevant_listing_text("Fpj Elegante Titanium", listing_text) == (
        "FPJ Elegante titanium ti 48mm2019 used 780000"
    )


def test_extract_relevant_listing_text_keeps_price_with_currency_suffix() -> None:
    listing_text = "Rolex 228253A choco N2 467000hkd full set"

    assert extract_relevant_listing_text("228253a choco", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_hk_prefixed_decimal_price() -> None:
    listing_text = "4-7 days arrive hk 7118/1200R white N2/2026 Hk1.45M"

    assert extract_relevant_listing_text("7118/1200r white", listing_text) == (
        "7118/1200R white N2/2026 Hk1.45M"
    )


def test_extract_relevant_listing_text_stops_before_fire_separator_brand() -> None:
    listing_text = (
        "💥PP 7118/1200r white 2026/2 new hkd1.47m "
        "💥PP 7118/1200r Champ 2026/2 new hkd1.235m "
        "💥Rolex 228348A Black 2026/3 new hkd650k"
    )

    assert extract_relevant_listing_text("7118/1200r white", listing_text) == (
        "PP 7118/1200r white 2026/2 new hkd1.47m"
    )


def test_extract_relevant_listing_text_keeps_price_after_fire_separator() -> None:
    listing_text = "📣*PP 7118/1200A grey* 💥$790k hkd 💥N1/2026"

    assert extract_relevant_listing_text("7118/1200a grey", listing_text) == (
        "PP 7118/1200A grey* 💥$790k hkd 💥N1/2026"
    )


def test_extract_relevant_listing_text_keeps_condition_and_split_price_after_fire_separator() -> None:
    listing_text = "*PP 7118/1200A grey* 💥N1/2026 💥790 000HKD"

    assert extract_relevant_listing_text("7118/1200a grey", listing_text) == (
        "PP 7118/1200A grey* 💥N1/2026 💥790 000HKD"
    )


def test_extract_relevant_listing_text_keeps_ap_50th_anniversary_details() -> None:
    listing_text = "AP 77451OR White 50th Used fullset 2022 🇭🇰HKD$ 660,000"

    assert extract_relevant_listing_text("77451or white", listing_text) == (
        "AP 77451OR White 50th Used fullset 2022 🇭🇰HKD$ 660,000"
    )


def test_extract_relevant_listing_text_keeps_ap_50th_likenew_price_suffix() -> None:
    listing_text = "AP 34mm 77451OR White 50th likenew 2022 665,000hkd"

    assert extract_relevant_listing_text("77451or white", listing_text) == (
        "AP 34mm 77451OR White 50th likenew 2022 665,000hkd"
    )


def test_extract_relevant_listing_text_handles_compound_reference() -> None:
    listing_text = (
        "7010/1G blue 03-26 $717k "
        "7118/1200A blue 02-26 $732k "
        "7118/1200A gray 04-26 $787k"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200A blue 02-26 $732k"
    )


def test_extract_relevant_listing_text_keeps_full_date_year_and_price() -> None:
    listing_text = (
        "7010/1G blue 12/2025 $527000 "
        "7118/1200A blue 2/2026 $732k N2 "
        "7300/1200R white 03/2026 $366k"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200A blue 2/2026 $732k N2"
    )


def test_extract_relevant_listing_text_keeps_shorthand_price_and_currency() -> None:
    listing_text = (
        "7010R purple N3/2026y 580k hkd "
        "7118/1200A blue N2/2026y 725k hkd "
        "5726/1A blue N9/2025y 1.065m hkd"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200A blue N2/2026y 725k hkd"
    )


def test_extract_relevant_listing_text_matches_reference_with_variant_suffix() -> None:
    listing_text = (
        "PP 7130G-016 Paper of 2022 USD31000 "
        "PP7010G-013, 2025 model, full set price: US$63,000 "
        "5726/1A-014 2021 Full Set: US$115,000"
    )

    assert extract_relevant_listing_text("5726/1a", listing_text) == (
        "5726/1A-014 2021 Full Set: US$115,000"
    )


def test_extract_relevant_listing_text_keeps_decimal_price() -> None:
    listing_text = (
        "116500 black 24.5k "
        "116500 panda 30.5k "
        "126334 blue jub $116500"
    )

    assert extract_relevant_listing_text("116500 panda", listing_text) == (
        "116500 panda 30.5k"
    )


def test_extract_relevant_listing_text_keeps_thousands_price_before_currency() -> None:
    listing_text = (
        "PP 7118/1A grey New 2/2026 PRICE 590.000 HKD "
        "3 days to Hong Kong"
    )

    assert extract_relevant_listing_text("7118/1a grey", listing_text) == (
        "PP 7118/1A grey New 2/2026 PRICE 590.000 HKD 3 days to Hong Kong"
    )


def test_extract_relevant_listing_text_keeps_dot_date_and_price() -> None:
    listing_text = (
        "Brand new // Deal in HK "
        "Patek 7118/1200A Blue // New 03.2026 // Price 725K HKD "
        "Rolex 126518 Tiffany // New 02.2026 // Price 685K HKD"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "Patek 7118/1200A Blue // New 03.2026 // Price 725K HKD"
    )


def test_extract_relevant_listing_text_strips_trailing_bullet_separator() -> None:
    listing_text = (
        "Patek 7118/1200A Blue // New 03.2026 // Price 725K HKD • "
        "Rolex 126518 Tiffany // New 02.2026 // Price 685K HKD"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "Patek 7118/1200A Blue // New 03.2026 // Price 725K HKD"
    )


def test_extract_relevant_listing_text_keeps_condition_prefixed_price() -> None:
    listing_text = (
        "5524G-cream-N2-405k hkd "
        "5226G-N2-280k hkd "
        "7118/1200a blue N3-735k hkd "
        "7118/1r white N3-985k hkd"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200a blue N3-735k hkd"
    )


def test_extract_relevant_listing_text_keeps_year_suffix_and_price() -> None:
    listing_text = "5990/1r 2021y full used hkd1.98k"

    assert extract_relevant_listing_text("5990/1r", listing_text) == (
        "5990/1r 2021y full used hkd1.98k"
    )


def test_extract_relevant_listing_text_keeps_split_price_and_currency() -> None:
    listing_text = (
        "2022 PP 5990/1R Blue dial Mint condition "
        "Complete set $255 000.00 USD + shipping"
    )

    assert extract_relevant_listing_text("5990/1r", listing_text) == (
        "2022 PP 5990/1R Blue dial Mint condition Complete set $255 000.00 USD + shipping"
    )


def test_extract_relevant_listing_text_keeps_trailing_currency_symbol() -> None:
    listing_text = "Patek 5990/1r - 2022 - German Paper - 217,5€"

    assert extract_relevant_listing_text("5990/1r", listing_text) == (
        "Patek 5990/1r - 2022 - German Paper - 217,5€"
    )


def test_extract_relevant_listing_text_keeps_decimal_price_before_currency_symbol() -> None:
    listing_text = "Brand new 5712G full set 2021 - 78.5 € inc shipment"

    assert extract_relevant_listing_text("5712g", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_one_digit_month_date_and_price() -> None:
    listing_text = (
        "5990/1r 25/11 new HKD2.23 "
        "5712/1r new 25/5 HKD1.98m "
        "126518ln yml N1/26 HKD490K"
    )

    assert extract_relevant_listing_text("5712/1r", listing_text) == (
        "5712/1r new 25/5 HKD1.98m"
    )


def test_extract_relevant_listing_text_keeps_year_month_date_and_price() -> None:
    listing_text = "126518 Tiffany 2026/3 new 678k hkd"

    assert extract_relevant_listing_text("126518 tiffany", listing_text) == (
        "126518 Tiffany 2026/3 new 678k hkd"
    )


def test_extract_relevant_listing_text_keeps_year_word_and_price() -> None:
    listing_text = (
        "7118/1200A Blue 2023Year Like New $670,000HKD "
        "5267/200A Green 2023Year Like New $460,000HKD"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200A Blue 2023Year Like New $670,000HKD"
    )


def test_extract_relevant_listing_text_keeps_compact_year_condition() -> None:
    listing_text = "New 5712/1r 2026full set HKD:2 2.05m"

    assert extract_relevant_listing_text("5712/1r", listing_text) == (
        "New 5712/1r 2026full set HKD:2 2.05m"
    )


def test_extract_relevant_listing_text_keeps_trailing_dollar_symbol() -> None:
    listing_text = "PP 5164A 2022 full set 100$"

    assert extract_relevant_listing_text("5164a", listing_text) == (
        "PP 5164A 2022 full set 100$"
    )


def test_extract_relevant_listing_text_keeps_size_before_price() -> None:
    listing_text = (
        "116500 PANDA LAST YR PROD. Daytona 40mm SS 2023 Card "
        "Retail ready Full Links $30,950+ship USDT OK NYC/LA"
    )

    assert extract_relevant_listing_text("116500 panda", listing_text) == (
        "116500 PANDA LAST YR PROD. Daytona 40mm SS 2023 Card "
        "Retail ready Full Links $30,950+ship USDT OK NYC/LA"
    )


def test_extract_relevant_listing_text_keeps_plain_price_after_year() -> None:
    listing_text = "116500 PANDA RETAIL READY FULL LINK 2023 31750"

    assert extract_relevant_listing_text("116500 panda", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_plain_price_before_label_note() -> None:
    listing_text = "Naked 116500 panda 26299 + lab"

    assert extract_relevant_listing_text("116500 panda", listing_text) == (
        "Naked 116500 panda 26299 + lab"
    )


def test_extract_relevant_listing_text_keeps_decimal_price_before_lnl_note() -> None:
    listing_text = "116500 panda Daytona 2017 full link retail ready 28.9+lnl"

    assert extract_relevant_listing_text("116500 panda", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_price_prefix_before_reference() -> None:
    listing_text = "4️⃣1️⃣k + 🚢 5205R blac d1al, 2015 pap3rs no b0x, reta1l r3ady"

    assert extract_relevant_listing_text("5205r", listing_text) == (
        "4️⃣1️⃣k + 🚢 5205R blac d1al, 2015 pap3rs no b0x, reta1l r3ady"
    )


def test_extract_relevant_listing_text_keeps_leading_price_after_metadata_noise() -> None:
    listing_text = "61000$ ( other groups ) ☄️☄️ 228349RBR - meteorite"

    assert extract_relevant_listing_text("228349rbr mete", listing_text) == (
        "61000$ ( other groups ) ☄️☄️ 228349RBR - meteorite"
    )


def test_explain_extraction_returns_rule_trace() -> None:
    listing_text = "4️⃣1️⃣k + 🚢 5205R blac d1al, 2015 pap3rs no b0x, reta1l r3ady"

    trace = explain_extraction("5205r", listing_text)

    assert trace.intent.reference_terms == (("5205r",),)
    assert trace.selected_reference == ("5205r",)
    assert trace.matched_token_span == (3, 3)
    assert trace.output_text == listing_text
    assert "reference.match_exact_or_compact" in trace.rule_ids
    assert "cleanup.display_text" in trace.rule_ids


def test_rulebook_is_priority_ordered() -> None:
    priorities = [rule.priority for rule in RULEBOOK]

    assert priorities == sorted(priorities)


@pytest.mark.parametrize(
    ("query", "listing_text", "expected_text"),
    [
        (
            "5205r",
            "4️⃣1️⃣k + 🚢 5205R blac d1al, 2015 pap3rs no b0x, reta1l r3ady",
            "4️⃣1️⃣k + 🚢 5205R blac d1al, 2015 pap3rs no b0x, reta1l r3ady",
        ),
        (
            "126500ln white 2026",
            "Rolex Daytona 126500LN white N3/2026 unworn 33.5k",
            "126500LN white N3/2026 unworn 33.5k",
        ),
        (
            "7118/1200a grey",
            "7118/1200A grey 🔥 2023 full set 27,500 + label",
            "7118/1200A grey 🔥 2023 full set 27,500 + label",
        ),
        (
            "116500 panda",
            "116500 panda Daytona 2017 full link retail ready 28.9+lnl",
            "116500 panda Daytona 2017 full link retail ready 28.9+lnl",
        ),
        (
            "77451or white",
            "AP 77451OR white 50th anniversary like new 2023 470k HKD",
            "AP 77451OR white 50th anniversary like new 2023 470k HKD",
        ),
    ],
)
def test_extract_relevant_listing_text_hard_pattern_table(
    query: str,
    listing_text: str,
    expected_text: str,
) -> None:
    assert extract_relevant_listing_text(query, listing_text) == expected_text


def test_listing_matches_ignores_looking_for_request() -> None:
    assert not listing_matches("228235a choco", "Lookingfor 228235A choco new 2026")


def test_extract_relevant_listing_text_keeps_year_range_and_price_range() -> None:
    listing_text = (
        "5712/1R 2022-2024 232$-246$ "
        "5712/1A 2016-2019-2020 "
        "5990/1A 2016 105,000$"
    )

    assert extract_relevant_listing_text("5712/1r", listing_text) == (
        "5712/1R 2022-2024 232$-246$"
    )


def test_extract_relevant_listing_text_keeps_caliber_after_reference() -> None:
    listing_text = "5164A 330SC 2022 complete $97,000 + label"

    assert extract_relevant_listing_text("5164a", listing_text) == (
        "5164A 330SC 2022 complete $97,000 + label"
    )


def test_extract_relevant_listing_text_keeps_plain_number_before_currency() -> None:
    listing_text = "5980/1R 2021 full set full links New buckle 1630000 HKD 208,000 Usdt"

    assert extract_relevant_listing_text("5980/1r", listing_text) == (
        "5980/1R 2021 full set full links New buckle 1630000 HKD 208,000 Usdt"
    )


def test_extract_relevant_listing_text_keeps_plain_number_after_currency() -> None:
    listing_text = (
        "✅PP ❣️5711R Watch and Service paper, HKD 605000 "
        "❣️5712R 2016/ HKD 830000 "
        "❣️5134R Service paper, HKD 130000"
    )

    assert extract_relevant_listing_text("5712r", listing_text) == (
        "5712R 2016/ HKD 830000"
    )


def test_extract_relevant_listing_text_keeps_decimal_price_before_currency() -> None:
    listing_text = "PP : 7118/1200A / White / Used full set 2023 - 105.5 Usdt"

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "PP : 7118/1200A / White / Used full set 2023 - 105.5 Usdt"
    )


def test_extract_relevant_listing_text_keeps_decimal_price_after_currency() -> None:
    listing_text = "7118/1200A white 2021 hkd (84.2)"

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "7118/1200A white 2021 hkd (84.2)"
    )


def test_extract_relevant_listing_text_matches_split_compound_reference() -> None:
    listing_text = (
        "Rm 056 white sapphire "
        "P.p 7118 1200a white naked 99k usdt "
        "P.p 5821/1AR 2026 new 755,000 HKD"
    )

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "P.p 7118 1200a white naked 99k usdt"
    )


def test_extract_relevant_listing_text_stops_before_split_brand_header() -> None:
    listing_text = (
        "7118/1200A WHITE $816k HKD 21Y USED "
        "A P 15416CD BLUE CERAMIC $3.32m HKD"
    )

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "7118/1200A WHITE $816k HKD 21Y USED"
    )


def test_extract_relevant_listing_text_drops_previous_item_before_emoji_separator() -> None:
    listing_text = (
        "7118/1R WHITE $996k HKD 3/2026 🍃 "
        "7118/1200A WHITE $816k HKD 21Y USED 🍃 "
        "7118/1450G $3.3m HKD 2/2026"
    )

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "7118/1200A WHITE $816k HKD 21Y USED"
    )


def test_extract_relevant_listing_text_drops_previous_condition_before_emoji_separator() -> None:
    listing_text = (
        "5235/50R $245k HKD 2021 LIKE NEW 🍃 "
        "7118/1200A WHITE $888k HKD 2024 LIKE NEW "
        "🍒🍒🍒A P🍒🍒🍒 15210ST GREY $135k HKD"
    )

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "7118/1200A WHITE $888k HKD 2024 LIKE NEW"
    )


def test_extract_relevant_listing_text_stops_before_richard_mille_header() -> None:
    listing_text = (
        "USED 7118/1200A white 2023Y $875K "
        "Richard miller RM61-01 green 2015y full set"
    )

    assert extract_relevant_listing_text("7118/1200a white", listing_text) == (
        "USED 7118/1200A white 2023Y $875K"
    )


def test_extract_relevant_listing_text_keeps_fullset_price_after_currency() -> None:
    listing_text = (
        "❶ 5167R Naked 71 series HKD 640000 "
        "❷ 5712R 2012 fullset HKD 777000"
    )

    assert extract_relevant_listing_text("5712r", listing_text) == (
        "5712R 2012 fullset HKD 777000"
    )


def test_extract_relevant_listing_text_keeps_local_prefix_before_reference() -> None:
    listing_text = "New 2026 AP 26240BA Ombre Yellow Fullset $119"

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "New 2026 AP 26240BA Ombre Yellow Fullset $119"
    )


def test_extract_relevant_listing_text_does_not_pull_previous_stock_item() -> None:
    listing_text = (
        "26239OR full gold blue used 2021 full set HKD 748k "
        "26240BA Frost gold 2022 Full Set 1,344,000HKD"
    )

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "26240BA Frost gold 2022 Full Set 1,344,000HKD"
    )


def test_extract_relevant_listing_text_keeps_month_year_after_price() -> None:
    listing_text = (
        "7118/1R-010 Champ HK$898,000 Feb-2026 "
        "💎7130R-014 Green HK$305,000 Jun-2024 "
        "💎7300/1200A-011 Green HK$169,000 Feb-2026"
    )

    assert extract_relevant_listing_text("7130r-014", listing_text) == (
        "7130R-014 Green HK$305,000 Jun-2024"
    )


def test_extract_relevant_listing_text_keeps_brand_family_prefix() -> None:
    listing_text = (
        "320560 Audemars Piguet Royal Oak Chronograph Rose Gold "
        "Ref: 26331OR.OO.D315CR.01 Full Set $56,000"
    )

    assert extract_relevant_listing_text("26331OR.OO.D315CR.01", listing_text) == (
        "Audemars Piguet Royal Oak Chronograph Rose Gold "
        "Ref: 26331OR.OO.D315CR.01 Full Set $56,000"
    )


def test_extract_relevant_listing_text_stops_at_reference_after_complete_price() -> None:
    listing_text = (
        "126200 Mint Green Oys N4 -> 85k hkd "
        "126231 GREEN ROMA JUB N5 -> 159k hkd "
        "126231 vi grey jub n5 -> 155k hkd "
        "126233 ng white jub n5 -> 162k hkd"
    )

    assert extract_relevant_listing_text("126231 green roma", listing_text) == (
        "126231 GREEN ROMA JUB N5 -> 159k hkd"
    )


def test_extract_relevant_listing_text_keeps_stock_list_segment_price() -> None:
    listing_text = (
        "HK STOCK LIST 116505 rainbow 284k "
        "5712g new 2024 -> 115k "
        "5726/1A used 2022 68k"
    )

    assert extract_relevant_listing_text("5712g", listing_text) == (
        "5712g new 2024 -> 115k"
    )


def test_extract_relevant_listing_text_keeps_unicode_currency_prefix() -> None:
    listing_text = "26240BA ALYX 2022💲128.5 + label"

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "26240BA ALYX 2022💲128.5 + label"
    )


def test_extract_relevant_listing_text_keeps_numeric_collaboration_descriptor() -> None:
    listing_text = (
        "26239OR full gold blue used 2021 full set HKD 748k "
        "26240BA 1017 ALYX 9SM used 2024 full set HKD 1.05m "
        "26401RO brown camo used 2019 full set HKD 278k"
    )

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "26240BA 1017 ALYX 9SM used 2024 full set HKD 1.05m"
    )


def test_extract_relevant_listing_text_keeps_compact_new_year_and_price() -> None:
    listing_text = "AP 26240Ba New2024 $830k Hkd"

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "AP 26240Ba New2024 $830k Hkd"
    )


def test_extract_relevant_listing_text_keeps_compact_date_new_suffix() -> None:
    listing_text = "26240ba 11/25new fullset Adjusted/sized HKD875k USD112.2k"

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "26240ba 11/25new fullset Adjusted/sized HKD875k USD112.2k"
    )


def test_extract_relevant_listing_text_keeps_n_marker_date_and_price() -> None:
    listing_text = "5990/1R 25/N11 HKD2.23m"

    assert extract_relevant_listing_text("5990/1r", listing_text) == (
        "5990/1R 25/N11 HKD2.23m"
    )


def test_extract_relevant_listing_text_keeps_chf_price_after_typo_date() -> None:
    listing_text = "AP 26240BA Alix 1/202P 125k chf new fs"

    assert extract_relevant_listing_text("26240ba", listing_text) == (
        "AP 26240BA Alix 1/202P 125k chf new fs"
    )


def test_extract_relevant_listing_text_stops_before_member_metadata() -> None:
    listing_text = "🏷️ 26240BA new 2024 💎💫👑✨ 👤 member 932184 📅 25/03/2026 11:14"

    assert extract_relevant_listing_text("26240ba new 2024", listing_text) == (
        "26240BA new 2024 💎💫👑✨"
    )


def test_extract_relevant_listing_text_keeps_compact_new_year_suffix() -> None:
    listing_text = "5990/1r new2026y hkd2.31m"

    assert extract_relevant_listing_text("5990/1r", listing_text) == (
        "5990/1r new2026y hkd2.31m"
    )


def test_extract_relevant_listing_text_keeps_hyphenated_descriptor_number() -> None:
    listing_text = "Patek Philippe 5164A, single watch, 56-character dial, 76,000 USDT"

    assert extract_relevant_listing_text("5164a", listing_text) == (
        "Patek Philippe 5164A, single watch, 56-character dial, 76,000 USDT"
    )


def test_extract_relevant_listing_text_keeps_repeat_reference_detail() -> None:
    listing_text = "5712/1R NAUTILUS 5712 2022 FULL SET RETAIL READY $233K + LABEL"

    assert extract_relevant_listing_text("5712/1r", listing_text) == (
        "5712/1R NAUTILUS 5712 2022 FULL SET RETAIL READY $233K + LABEL"
    )


def test_extract_relevant_listing_text_drops_next_item_marker() -> None:
    listing_text = (
        "7010R purple N3/2026y 580k hkd "
        "7118/1200A blue N2/2026y 725k hkd 🎃New "
        "5726/1A blue N9/2025y 1.065m hkd"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200A blue N2/2026y 725k hkd"
    )


def test_extract_relevant_listing_text_keeps_dot_thousand_price_with_hk_suffix() -> None:
    listing_text = (
        "127386 2026 1,37m "
        "127336 2026 1,22m "
        "127335 2025 453.000hk "
        "128236a rainbow good 590.000hk "
        "228235a choco 2026 468.000hk "
        "7010r champ nos 455.000hk"
    )

    assert extract_relevant_listing_text("228235a choco", listing_text) == (
        "228235a choco 2026 468.000hk"
    )


def test_extract_relevant_listing_text_keeps_compact_year_condition_after_reference() -> None:
    listing_text = (
        "Datejust 36/41mm 2️⃣ 126331 Choco JUB 2024N10 $139,500 "
        "Day-Date ⏰ 228235A Choco 2018N9 Used $380,000 "
        "Sky-Dweller 9️⃣ 326238 White 2021N1 $295,500"
    )

    assert extract_relevant_listing_text("228235a choco", listing_text) == (
        "228235A Choco 2018N9 Used $380,000"
    )


def test_extract_relevant_listing_text_stops_before_luggage_separator() -> None:
    listing_text = (
        "🧳*126621 Choco - N4/2026* $149,000 HKD "
        "*🧳228235A Choco - N5/2026* $465,000 HKD "
        "*🧳228238 Champ Roman - N5/2026* $425,000 HKD 🧳*"
    )

    assert extract_relevant_listing_text("228235a choco", listing_text) == (
        "228235A Choco - N5/2026* $465,000 HKD"
    )


def test_extract_relevant_listing_text_stops_before_reference_after_amount_currency_pair() -> None:
    listing_text = (
        "228235A Choco Dial | 5/2026 | 463000 HKD "
        "228236 Ice Blue Roman | 4/2026 Both Tag | 588000 HKD"
    )

    assert extract_relevant_listing_text("228235a choco", listing_text) == (
        "228235A Choco Dial | 5/2026 | 463000 HKD"
    )


def test_extract_relevant_listing_text_stops_before_lange_brand_after_price() -> None:
    listing_text = (
        "116598SACO only watch 707k HKD ⚡️A.Lange &Söhne ⚡️720.032 "
        "Accompanied by a post-matching certificate 1.31m HKD "
        "⚡️109.021 only watch 195k HKD"
    )

    assert extract_relevant_listing_text("116598saco", listing_text) == (
        "116598SACO only watch 707k HKD"
    )


def test_extract_relevant_listing_text_stops_before_lange_brand_without_separator() -> None:
    listing_text = (
        "116598SACO 2015y Full Set HKD905k "
        "A.Lange &Söhne 405.035 2023y Full set HKD590k "
        "414.026 only watch HKD336k"
    )

    assert extract_relevant_listing_text("116598saco", listing_text) == (
        "116598SACO 2015y Full Set HKD905k"
    )


def test_extract_relevant_listing_text_keeps_compact_used_year_and_price() -> None:
    listing_text = "15410OR 2024used 460K usdt"

    assert extract_relevant_listing_text("15410or", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_decimal_year_and_split_price() -> None:
    listing_text = "127335 land dweller 🤍 New 2026.3 no box $59,000 + label 🫶🇺🇸"

    assert extract_relevant_listing_text("127335", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_link_count_before_compact_prices() -> None:
    listing_text = "15451OR white only watch 18links💰501000Hkd/64500USDT"

    assert extract_relevant_listing_text("15451or white", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_slash_usdt_shorthand_price() -> None:
    listing_text = "Good condition 2021 fullset 15451or white 585000 hkd / 75700u"

    assert extract_relevant_listing_text("15451or white", listing_text) == (
        "15451or white 585000 hkd / 75700u"
    )


def test_extract_relevant_listing_text_keeps_full_slash_date_and_emoji_price() -> None:
    listing_text = (
        "Philippe Nautilus Ref. 5726/1A-014 ✨ Blue Dial • Annual Calendar • "
        "Moonphase • 09/10/2019 Stunning blue dial configuration, full set dated "
        "09/10/2019. Mint condition example, very well preserved. Includes serial "
        "check for added transparency and confidence. Yours for 1️⃣2️⃣4️⃣,9️⃣8️⃣5️⃣ "
        "USD + insured shipping 📦 Location: Miami, FL • @ThrowinSalt 📸 DM for pics"
    )

    assert extract_relevant_listing_text("5726/1a", listing_text) == (
        "Philippe Nautilus Ref. 5726/1A-014 ✨ Blue Dial • Annual Calendar • "
        "Moonphase • 09/10/2019 Stunning blue dial configuration, full set dated "
        "09/10/2019. Mint condition example, very well preserved. Includes serial "
        "check for added transparency and confidence. Yours for 1️⃣2️⃣4️⃣,9️⃣8️⃣5️⃣ "
        "USD + insured shipping"
    )


def test_extract_relevant_listing_text_keeps_compact_new_and_currency_price() -> None:
    listing_text = "❶ 67650ST black 2026new fullset HKD25000 ❷ 77450SR grey 3/2026new fullset HKD348000"

    assert extract_relevant_listing_text("67650st", listing_text) == (
        "67650ST black 2026new fullset HKD25000"
    )


def test_extract_relevant_listing_text_keeps_fullset_and_compact_hkd_price() -> None:
    listing_text = "77350sr, 2021fullset, 278000hkd"

    assert extract_relevant_listing_text("77350sr", listing_text) == (
        "77350sr, 2021fullset, 278000hkd"
    )


def test_extract_relevant_listing_text_keeps_compact_prefixed_date_and_multi_currency() -> None:
    listing_text = (
        "127235 new3/26 With white tag 🏷️/No box "
        "HKD 361k |USD 46.3k USD 46.8k|AED 173.9k"
    )

    assert extract_relevant_listing_text("127235", listing_text) == listing_text


def test_extract_relevant_listing_text_keeps_listing_stock_code_after_reference() -> None:
    listing_text = (
        "$39,999.99 + label Audemars Piguet Royal Oak 34MM "
        "(77350SR.OO.1261SR.01) - Steel & Rose Gold - White Dial - "
        "2021 Card - (SW649) Amazing Condition, Retail Ready, Full Links"
    )

    assert extract_relevant_listing_text("77350sr", listing_text) == (
        "34MM (77350SR.OO.1261SR.01) - Steel & Rose Gold - White Dial - "
        "2021 Card - (SW649) Amazing Condition, Retail Ready, Full Links"
    )


def test_extract_relevant_listing_text_prefers_exact_reference_over_compact_split() -> None:
    listing_text = (
        "126595 rbow black 2025y 3.4m hkd "
        "126595 white n9 1.45m hkd "
        "126679 SABR 2025y 1.28m hkd "
        "126595rbow rainbow 2026y 490000usdt "
        "126598 pave 2024y 3.58m hkd"
    )

    assert extract_relevant_listing_text("126595rbow", listing_text) == (
        "126595rbow rainbow 2026y 490000usdt"
    )


def test_extract_relevant_listing_text_stops_before_split_rm_header() -> None:
    listing_text = (
        "AP 77350sr 2020y fullset 259kHKD "
        "RM 35-03 white 2023y 545kusdt"
    )

    assert extract_relevant_listing_text("77350sr", listing_text) == (
        "AP 77350sr 2020y fullset 259kHKD"
    )


def test_listing_matches_rm_reference_without_brand_prefix() -> None:
    assert listing_matches(
        "65-01 lebron james",
        "RM65-01 Lebron James New 12/2025 - 485k usdt",
    )
    assert not listing_matches(
        "65-01 lebron james",
        "RM65-01 Mclaren New 12/2025 - 475k usdt",
    )


def test_extract_relevant_listing_text_scopes_rm_reference_without_brand_prefix() -> None:
    listing_text = (
        "RM65-01 Mclaren New 12/2025 - usdt 475k "
        "RM65-01 Lebron James New 12/2025 - usdt 485k "
        "RM30-01 White Ceramic New 2/2026 - usdt 380k"
    )

    assert extract_relevant_listing_text("65-01 lebron james", listing_text) == (
        "RM65-01 Lebron James New 12/2025 - usdt 485k"
    )


def test_extract_relevant_listing_text_stops_before_bare_rm_reference_after_price() -> None:
    listing_text = (
        "RM65-01 Lebron Jamew 4/2026 Usdt485k "
        "65-01 McLaren 2026/4 New Usdt476k"
    )

    assert extract_relevant_listing_text("RM65-01 Lebron", listing_text) == (
        "RM65-01 Lebron Jamew 4/2026 Usdt485k"
    )


def test_extract_relevant_listing_text_stops_before_fp_journe_brand_header() -> None:
    listing_text = (
        "RM65-01 Lebron James N12/2025 USDT 485 "
        "F.P. Journe Tourbillon Souverain Vertical Fullset 2024 1.10m usdt"
    )

    assert extract_relevant_listing_text("RM65-01 Lebron", listing_text) == (
        "RM65-01 Lebron James N12/2025 USDT 485"
    )


def test_extract_relevant_listing_text_stops_before_compact_fp_journe_brand_header() -> None:
    listing_text = (
        "RM65-01 Lebron James N12/2025 USDT 485 "
        "F.P.Journe Octa Auto Lune salmon RG 38mm only watch HKD2.12m "
        "V.C 4300v/220r -H144 26/4 HKD895k"
    )

    assert extract_relevant_listing_text("RM65-01 Lebron", listing_text) == (
        "RM65-01 Lebron James N12/2025 USDT 485"
    )


def test_extract_relevant_listing_text_stops_before_rm_section_header_after_price() -> None:
    listing_text = (
        "RM65-01 Lebron James N12/2025 USDT 485 "
        "RM USED RM72-10WG Full T Diamond 2024 fullset 4.70m HKD"
    )

    assert extract_relevant_listing_text("RM65-01 Lebron", listing_text) == (
        "RM65-01 Lebron James N12/2025 USDT 485"
    )


def test_extract_relevant_listing_text_stops_before_post_price_service_tail() -> None:
    listing_text = (
        "Brand new // Deal in HK • 5226G // New 02/2026 // Price 37k USDT "
        "• 7130R // New 02/2026 // Price 43k USDT "
        "• 7200R // New 02/2026 // Price 23K USDT "
        "👉 Welcome to my office check and pay // All"
    )

    assert extract_relevant_listing_text("7200r", listing_text) == (
        "7200R // New 02/2026 // Price 23K USDT"
    )


def test_extract_relevant_listing_text_keeps_decimal_size_and_comma_price() -> None:
    listing_text = "Patek Philippe 7200R, 34.6 mm case diameter, 2019, no box,152000HKD"

    assert extract_relevant_listing_text("7200r", listing_text) == listing_text


def test_extract_relevant_listing_text_stops_before_numbered_used_item_after_price() -> None:
    listing_text = (
        "*🎊✨🎊Used Cartier🎊✨🎊* ✨w20018d6 with card 39300hkd "
        "✨w20073x8 16y 28500hkd ✨w2sa0011 24y fullset 38500hkd "
        "✨wssa0009 18y fullset 44400hkd ✨w2sa0012 20y fullset 33000hkd "
        "✨wssa0022 24y fullset 28500hkd ✨wssa0046 26y fullset 56000hkd "
        "✨wssa0082 25y fullset 43000hkd ✨Used 7200R-001 White 2017y 146000hkd "
        "4⃣Used"
    )

    assert extract_relevant_listing_text("7200r", listing_text) == (
        "Used 7200R-001 White 2017y 146000hkd"
    )


def test_extract_relevant_listing_text_stops_before_next_used_same_reference_item() -> None:
    listing_text = (
        "🩵Used 5160R-001 naked 1.1m hkd "
        "🩵Used 5160R-001 2014y 1.245m hkd "
        "🩵Used 7200R-001 2019y 156k hkd "
        "🩵Used 7200R-001 2022y 160k hkd "
        "🩵Used 5980/1A-001 blue naked 780k hkd"
    )

    assert extract_relevant_listing_text("7200r", listing_text) == (
        "Used 7200R-001 2019y 156k hkd"
    )


def test_extract_relevant_listing_text_stops_before_numbered_next_item_after_hkd_price() -> None:
    listing_text = (
        "🔥🔥CL WATCH🔥🔥 HKD/USD/EUR wire 🆗+ ✈️ label, 4-7days arrive HK "
        "No box for watches, Welcome DM 1. Used Cartier ‎W2BB0023 / watch only / "
        "11 links HKD43500 2. Used PAM01287 green 2022 / card and watch / "
        "not original strap HKD55500 3. Used Cartier WSSA0009 2018 / card and watch / "
        "not original strap HKD44400 11. Used PP 7200R 2019 / paper and watch / "
        "not original strap HKD156500 11. Used 114300 black / watch only /"
    )

    assert extract_relevant_listing_text("7200r", listing_text) == (
        "Used PP 7200R 2019 / paper and watch / not original strap HKD156500"
    )


def test_extract_relevant_listing_text_stops_before_numbered_item_after_comma_price() -> None:
    listing_text = (
        "Used 116598saco / watch only $716,000 "
        "95 Used 116600 2016 / white card / 9.5 links $88,500"
    )

    assert extract_relevant_listing_text("116598saco", listing_text) == (
        "Used 116598saco / watch only $716,000"
    )


def test_extract_relevant_listing_text_stops_before_brand_status_header_after_price() -> None:
    listing_text = (
        "Used 77451ST ice blue 2023y full set 461k hkd "
        "Rolex⚡⚡ ⚡️New 126000 purple N7/2025y 69k HKD"
    )

    assert extract_relevant_listing_text("77451st ice blue", listing_text) == (
        "Used 77451ST ice blue 2023y full set 461k hkd"
    )


def test_year_descriptor_matches_compact_month_year_condition_tokens() -> None:
    assert listing_matches(
        "126500ln white 2026",
        "126500LN White N3/2026 ⭐️( HK$279,000 / USD35,950 ) without box",
    )
    assert listing_matches(
        "126500ln white 2026",
        "126500ln white N3-2026 HK$ 274,000 without box Ready In HK",
    )
    assert listing_matches(
        "126500ln white 2026",
        "126500LN White 2026y HKD 279000",
    )


def test_extract_relevant_listing_text_uses_compact_month_year_as_year_descriptor() -> None:
    listing_text = (
        "126500LN Black N2/2026 ⭐️( HK$235,000 / USD30,280 ) "
        "126500LN White N3/2026 ⭐️( HK$279,000 / USD35,950 ) without box"
    )

    assert extract_relevant_listing_text("126500ln white 2026", listing_text) == (
        "126500LN White N3/2026 ⭐️( HK$279,000 / USD35,950 ) without box"
    )


def test_filter_matching_listings_preserves_original_order() -> None:
    listings = [
        Listing("Rolex 228253A silver dial"),
        Listing("Rolex 228253A choco dial"),
        Listing("Patek 5712 blue dial"),
        Listing("Day-Date 228253A CHOCO"),
    ]

    matches = filter_matching_listings("228253a choco", listings)

    assert matches == [listings[1], listings[3]]
