from dataclasses import dataclass

from app.matcher import (
    extract_relevant_listing_text,
    filter_matching_listings,
    listing_matches,
    normalize_text,
    tokenize_query,
)


@dataclass(frozen=True)
class Listing:
    listing_text: str


def test_normalize_text_is_case_insensitive_and_punctuation_tolerant() -> None:
    assert normalize_text("  Patek 228253A, CHOCO!  ") == "patek 228253a choco"


def test_tokenize_query_extracts_model_reference_and_descriptor() -> None:
    assert tokenize_query("228253A choco") == ["228253a", "choco"]
    assert tokenize_query("7118/1200A 25.5k") == ["7118/1200a", "25.5k"]


def test_listing_matches_requires_all_query_tokens() -> None:
    assert listing_matches("228253a choco", "Rolex 228253A dial CHOCO full set")
    assert not listing_matches("228253a choco", "Rolex 228253A silver dial full set")


def test_listing_matches_reference_tokens_across_punctuation() -> None:
    assert listing_matches("228253a choco", "Rolex 228-253A dial CHOCO full set")


def test_listing_matches_decimal_price_as_single_token() -> None:
    assert listing_matches("25.5k", "116500 blk2023 glass scratch 25.5k")
    assert not listing_matches("25.5k", "116500 Black Used USD25,460")


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


def test_extract_relevant_listing_text_returns_exact_stock_list_segment() -> None:
    stock_list = (
        "134300 pistachio $78500 N3 "
        "228235A EIS $541000 N3 228235 green $433000 N12 "
        "278271g choco jub $162500 N10"
    )

    assert extract_relevant_listing_text("228235a eis", stock_list) == (
        "228235A EIS $541000 N3"
    )


def test_extract_relevant_listing_text_keeps_price_with_currency_suffix() -> None:
    listing_text = "Rolex 228253A choco N2 467000hkd full set"

    assert extract_relevant_listing_text("228253a choco", listing_text) == (
        "228253A choco N2 467000hkd full set"
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
        "7118/1A grey New 2/2026 PRICE 590.000 HKD 3 days to Hong Kong"
    )


def test_extract_relevant_listing_text_keeps_dot_date_and_price() -> None:
    listing_text = (
        "Brand new // Deal in HK "
        "Patek 7118/1200A Blue // New 03.2026 // Price 725K HKD "
        "Rolex 126518 Tiffany // New 02.2026 // Price 685K HKD"
    )

    assert extract_relevant_listing_text("7118/1200a blue", listing_text) == (
        "7118/1200A Blue // New 03.2026 // Price 725K HKD"
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
        "5990/1R Blue dial Mint condition Complete set $255 000.00 USD + shipping"
    )


def test_extract_relevant_listing_text_keeps_trailing_currency_symbol() -> None:
    listing_text = "Patek 5990/1r - 2022 - German Paper - 217,5€"

    assert extract_relevant_listing_text("5990/1r", listing_text) == (
        "5990/1r - 2022 - German Paper - 217,5€"
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


def test_filter_matching_listings_preserves_original_order() -> None:
    listings = [
        Listing("Rolex 228253A silver dial"),
        Listing("Rolex 228253A choco dial"),
        Listing("Patek 5712 blue dial"),
        Listing("Day-Date 228253A CHOCO"),
    ]

    matches = filter_matching_listings("228253a choco", listings)

    assert matches == [listings[1], listings[3]]
