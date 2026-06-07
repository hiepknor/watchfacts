from __future__ import annotations

import re
from typing import Iterable, Protocol, TypeVar

from app.matcher_normalization import (
    TOKEN_RE,
    compact_text as _compact_text,
    normalize_text,
    tokenize_query,
)
from app.matcher_rulebook import ExtractionTrace, QueryIntent
from app.matcher_token_classification import (
    CURRENCY_PREFIX_CHARS,
    looks_like_bare_reference_after_price as _looks_like_bare_reference_after_price,
    looks_like_caliber_token as _looks_like_caliber_token,
    looks_like_compact_currency_prefixed_price as _looks_like_compact_currency_prefixed_price,
    looks_like_date_or_condition_token as _looks_like_date_or_condition_token,
    looks_like_decimal_price_after_currency as _looks_like_decimal_price_after_currency,
    looks_like_decimal_price_before_currency as _looks_like_decimal_price_before_currency,
    looks_like_decimal_price_before_currency_symbol as _looks_like_decimal_price_before_currency_symbol,
    looks_like_decimal_size_before_unit as _looks_like_decimal_size_before_unit,
    looks_like_descriptor_number_token as _looks_like_descriptor_number_token,
    looks_like_hyphenated_descriptor_number as _looks_like_hyphenated_descriptor_number,
    looks_like_leetspeak_detail_token as _looks_like_leetspeak_detail_token,
    looks_like_link_count_token as _looks_like_link_count_token,
    looks_like_listing_stock_code as _looks_like_listing_stock_code,
    looks_like_model_or_price_token as _looks_like_model_or_price_token,
    looks_like_ordinal_detail_token as _looks_like_ordinal_detail_token,
    looks_like_plain_price_after_currency as _looks_like_plain_price_after_currency,
    looks_like_plain_price_after_date as _looks_like_plain_price_after_date,
    looks_like_plain_price_after_descriptor as _looks_like_plain_price_after_descriptor,
    looks_like_plain_price_before_currency as _looks_like_plain_price_before_currency,
    looks_like_plain_price_before_label_note as _looks_like_plain_price_before_label_note,
    looks_like_price_token as _looks_like_price_token,
    looks_like_query_descriptor_token as _looks_like_query_descriptor_token,
    looks_like_reference_detail_after_size as _looks_like_reference_detail_after_size,
    looks_like_repeat_reference_detail as _looks_like_repeat_reference_detail,
    looks_like_size_token as _looks_like_size_token,
    looks_like_split_thousands_price as _looks_like_split_thousands_price,
    looks_like_year_token as _looks_like_year_token,
    parse_query_terms as _parse_query_terms,
)


LOCAL_MATCH_WINDOW = 12
SEGMENT_MATCH_WINDOW = 45
PRODUCT_BRAND_TOKENS = {
    "a.lange",
    "audemars",
    "cartier",
    "lange",
    "p.p",
    "patek",
    "philippe",
    "richard",
    "rolex",
    "vacheron",
}
PRODUCT_STATUS_TOKENS = {"new", "used", "stock"}
PRODUCT_HEADER_TOKENS = {
    "air-king",
    "datejust",
    "day-date",
    "deepsea",
    "explorer",
    "gmt",
    "milgauss",
    "oyster",
    "sea-dweller",
    "sky-dweller",
    "submariner",
    "yacht",
}
ITEM_SEPARATOR_CHARS = "🍃🍓🍑🍯🍒🍄🍇🍧🥝🦋📍⏰🚗🧳🧰🪨💥♦‼🥥💠🚀"
LOCAL_PREFIX_WINDOW = 4
REF_PREFIX_WINDOW = 8
LOCAL_PREFIX_TOKENS = {
    "ap",
    "audemars",
    "brand",
    "like",
    "new",
    "nautilus",
    "naked",
    "p.p",
    "patek",
    "philippe",
    "piguet",
    "pp",
    "ref",
    "rolex",
    "used",
}
REF_PREFIX_TOKENS = {
    "audemars",
    "chronograph",
    "gold",
    "oak",
    "nautilus",
    "patek",
    "philippe",
    "piguet",
    "ref",
    "rose",
    "royal",
}


class HasListingText(Protocol):
    listing_text: str


T = TypeVar("T", bound=HasListingText)


# Query intent rules.
def is_non_sale_request(listing_text: str) -> bool:
    return _looks_like_non_sale_request(normalize_text(listing_text))


def listing_matches(query: str, listing_text: str) -> bool:
    reference_terms, descriptor_tokens = _parse_query_terms(query)
    if not reference_terms and not descriptor_tokens:
        return False

    normalized_listing = normalize_text(listing_text)
    if is_non_sale_request(listing_text):
        return False
    listing_token_list = normalized_listing.split()
    listing_tokens = set(listing_token_list)
    compact_listing = _compact_text(normalized_listing)

    if not all(
        _descriptor_exists_in_listing(token, listing_token_list)
        for token in descriptor_tokens
    ):
        return False

    if not all(
        _reference_term_exists(reference_term, listing_token_list, compact_listing)
        for reference_term in reference_terms
    ):
        return False

    if not reference_terms or not descriptor_tokens:
        return True

    return all(
        _reference_has_local_descriptors(
            reference_term,
            descriptor_tokens,
            listing_token_list,
        )
        for reference_term in reference_terms
    )


def filter_matching_listings(query: str, listings: Iterable[T]) -> list[T]:
    return [
        listing
        for listing in listings
        if listing_matches(query, listing.listing_text)
    ]


def extract_relevant_listing_text(query: str, listing_text: str) -> str:
    return explain_extraction(query, listing_text).output_text


def explain_extraction(query: str, listing_text: str) -> ExtractionTrace:
    reference_terms, descriptor_tokens = _parse_query_terms(query)
    intent = QueryIntent(
        reference_terms=tuple(tuple(term) for term in reference_terms),
        descriptor_tokens=tuple(descriptor_tokens),
    )

    token_matches = list(TOKEN_RE.finditer(listing_text))
    normalized_tokens = [normalize_text(match.group(0)) for match in token_matches]
    if not reference_terms:
        output_text, token_span, char_span = _extract_descriptor_only_listing_text_with_trace(
            listing_text,
            token_matches,
            normalized_tokens,
            descriptor_tokens,
        )
        return ExtractionTrace(
            query=query,
            intent=intent,
            selected_reference=None,
            matched_token_span=token_span,
            selected_char_span=char_span,
            rule_ids=(
                "query.parse_intent",
                "descriptor.require_local_match",
                "cleanup.display_text",
            ),
            output_text=output_text,
        )

    fallback: tuple[list[str], int, int] | None = None
    for reference_term in reference_terms:
        term_length = len(reference_term)
        exact_reference_exists = _find_exact_reference_term_index(
            reference_term,
            normalized_tokens,
        ) is not None
        for index in range(len(normalized_tokens) - term_length + 1):
            match_length = _reference_match_length_at(reference_term, normalized_tokens, index)
            if match_length is None:
                continue
            if exact_reference_exists and not _reference_term_exactly_matches_at(
                reference_term,
                normalized_tokens,
                index,
            ):
                continue

            reference_index = index + match_length - 1
            if fallback is None:
                fallback = (reference_term, index, reference_index)
            if descriptor_tokens and not _descriptors_match_local_tokens(
                descriptor_tokens,
                _local_descriptor_tokens(normalized_tokens, reference_index),
            ):
                continue

            start = _matching_segment_start(
                listing_text,
                token_matches,
                normalized_tokens,
                index,
            )
            end = _matching_segment_end(
                listing_text,
                token_matches,
                reference_index,
            )
            output_text = _clean_display_text(listing_text[start:end])
            return ExtractionTrace(
                query=query,
                intent=intent,
                selected_reference=tuple(reference_term),
                matched_token_span=(index, reference_index),
                selected_char_span=(start, end),
                rule_ids=(
                    "query.parse_intent",
                    "reference.match_exact_or_compact",
                    "descriptor.require_local_match",
                    "price.keep_prefix",
                    "boundary.stop_next_product",
                    "boundary.stop_metadata",
                    "date_condition.keep_detail",
                    "cleanup.display_text",
                ),
                output_text=output_text,
            )

    if fallback is not None:
        reference_term, index, reference_index = fallback
        start = _matching_segment_start(
            listing_text,
            token_matches,
            normalized_tokens,
            index,
        )
        end = _matching_segment_end(listing_text, token_matches, reference_index)
        output_text = _clean_display_text(listing_text[start:end])
        return ExtractionTrace(
            query=query,
            intent=intent,
            selected_reference=tuple(reference_term),
            matched_token_span=(index, reference_index),
            selected_char_span=(start, end),
            rule_ids=(
                "query.parse_intent",
                "reference.match_exact_or_compact",
                "price.keep_prefix",
                "boundary.stop_next_product",
                "boundary.stop_metadata",
                "date_condition.keep_detail",
                "cleanup.display_text",
            ),
            output_text=output_text,
        )

    return ExtractionTrace(
        query=query,
        intent=intent,
        selected_reference=None,
        matched_token_span=None,
        selected_char_span=None,
        rule_ids=("query.parse_intent",),
        output_text=listing_text,
    )


def _extract_descriptor_only_listing_text(
    listing_text: str,
    token_matches: list[re.Match[str]],
    normalized_tokens: list[str],
    descriptor_tokens: list[str],
) -> str:
    return _extract_descriptor_only_listing_text_with_trace(
        listing_text,
        token_matches,
        normalized_tokens,
        descriptor_tokens,
    )[0]


def _extract_descriptor_only_listing_text_with_trace(
    listing_text: str,
    token_matches: list[re.Match[str]],
    normalized_tokens: list[str],
    descriptor_tokens: list[str],
) -> tuple[str, tuple[int, int] | None, tuple[int, int] | None]:
    if not descriptor_tokens:
        return listing_text, None, None

    best_span: tuple[int, int] | None = None
    for index, token in enumerate(normalized_tokens):
        if token not in descriptor_tokens:
            continue

        end_index = min(len(normalized_tokens), index + LOCAL_MATCH_WINDOW)
        local_tokens = normalized_tokens[index:end_index]
        if not all(descriptor in local_tokens for descriptor in descriptor_tokens):
            continue
        last_descriptor_offset = max(
            local_tokens.index(descriptor) for descriptor in descriptor_tokens
        )
        span = (index, index + last_descriptor_offset)
        if best_span is None or (span[1] - span[0], span[0]) < (
            best_span[1] - best_span[0],
            best_span[0],
        ):
            best_span = span

    if best_span is not None:
        start_index, end_descriptor_index = best_span
        end = _descriptor_segment_end(listing_text, token_matches, end_descriptor_index)
        start = token_matches[start_index].start()
        return (
            _clean_display_text(listing_text[start:end]),
            (start_index, end_descriptor_index),
            (start, end),
        )

    return listing_text, None, None


def _descriptor_segment_end(
    listing_text: str,
    token_matches: list[re.Match[str]],
    start_index: int,
) -> int:
    end = token_matches[start_index].end()
    following_matches = token_matches[start_index + 1 :]
    for offset, match in enumerate(following_matches, start=1):
        normalized_token = normalize_text(match.group(0))
        next_match = following_matches[offset] if offset < len(following_matches) else None
        next_token = normalize_text(next_match.group(0)) if next_match else ""
        previous_token = (
            normalize_text(token_matches[start_index + offset - 1].group(0))
            if start_index + offset - 1 >= 0
            else ""
        )
        if offset > SEGMENT_MATCH_WINDOW:
            break
        previous_match = token_matches[start_index + offset - 1]
        if _has_section_separator_between(
            listing_text,
            previous_match.end(),
            match.start(),
        ):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if offset > 1 and _looks_like_product_reference_boundary(
            listing_text,
            match.start(),
            match.end(),
            normalized_token,
            next_token,
            previous_token,
        ):
            break
        end = _include_trailing_non_token_suffix(
            listing_text,
            _include_trailing_currency_symbol(listing_text, match.end()),
        )
    return end


# Query descriptor and reference matching rules.
def _reference_term_exists(
    reference_term: list[str],
    listing_tokens: list[str],
    compact_listing: str,
) -> bool:
    if _find_reference_term_index(reference_term, listing_tokens) is not None:
        return True
    if len(reference_term) == 1:
        return _compact_text(reference_term[0]) in compact_listing
    return _compact_text("".join(reference_term)) in compact_listing


def _reference_has_local_descriptors(
    reference_term: list[str],
    descriptor_tokens: list[str],
    listing_tokens: list[str],
) -> bool:
    term_length = len(reference_term)
    found_reference = False
    for index in range(len(listing_tokens) - term_length + 1):
        match_length = _reference_match_length_at(reference_term, listing_tokens, index)
        if match_length is None:
            continue

        found_reference = True
        local_tokens = set(
            _local_descriptor_tokens(
                listing_tokens,
                index + match_length - 1,
            )
        )
        if _descriptors_match_local_tokens(descriptor_tokens, local_tokens):
            return True

    if found_reference:
        return False

    compact_reference = _compact_text("".join(reference_term))
    if compact_reference in _compact_text("".join(listing_tokens)):
        return all(
            _descriptor_exists_in_listing(descriptor, listing_tokens)
            for descriptor in descriptor_tokens
        )

    return False


def _descriptors_match_local_tokens(
    descriptor_tokens: list[str],
    local_tokens: Iterable[str],
) -> bool:
    local_token_list = list(local_tokens)
    return all(
        _descriptor_exists_in_listing(descriptor, local_token_list)
        for descriptor in descriptor_tokens
    )


def _descriptor_exists_in_listing(descriptor: str, listing_tokens: Iterable[str]) -> bool:
    return any(
        _descriptor_token_matches(descriptor, listing_token)
        for listing_token in listing_tokens
    )


def _descriptor_token_matches(descriptor: str, listing_token: str) -> bool:
    if descriptor == listing_token:
        return True
    if not _looks_like_year_token(descriptor):
        return False
    return _date_or_condition_token_contains_year(listing_token, descriptor)


def _date_or_condition_token_contains_year(token: str, year: str) -> bool:
    if not _looks_like_date_or_condition_token(token):
        return False
    return bool(
        token.startswith(year)
        or token.endswith(year)
        or re.search(rf"(?:[./-]|n){re.escape(year)}(?:$|[a-z./-])", token)
    )


def _find_reference_term_index(
    reference_term: list[str],
    listing_tokens: list[str],
) -> int | None:
    term_length = len(reference_term)
    for index in range(len(listing_tokens) - term_length + 1):
        if _reference_term_matches_at(reference_term, listing_tokens, index):
            return index
    return None


def _find_exact_reference_term_index(
    reference_term: list[str],
    listing_tokens: list[str],
) -> int | None:
    term_length = len(reference_term)
    for index in range(len(listing_tokens) - term_length + 1):
        if _reference_term_exactly_matches_at(reference_term, listing_tokens, index):
            return index
    return None


def _reference_term_exactly_matches_at(
    reference_term: list[str],
    listing_tokens: list[str],
    index: int,
) -> bool:
    return listing_tokens[index : index + len(reference_term)] == reference_term


def _reference_term_matches_at(
    reference_term: list[str],
    listing_tokens: list[str],
    index: int,
) -> bool:
    return _reference_match_length_at(reference_term, listing_tokens, index) is not None


def _reference_match_length_at(
    reference_term: list[str],
    listing_tokens: list[str],
    index: int,
) -> int | None:
    listing_slice = listing_tokens[index : index + len(reference_term)]
    if listing_slice == reference_term:
        return len(reference_term)
    if len(reference_term) != 1:
        return None
    if len(listing_slice) == 1 and _reference_token_matches(reference_term[0], listing_slice[0]):
        return 1

    compact_reference = _compact_text(reference_term[0])
    for span_length in range(2, 4):
        token_span = listing_tokens[index : index + span_length]
        if len(token_span) != span_length:
            break
        if _compact_text("".join(token_span)) == compact_reference:
            return span_length
    return None


def _reference_token_matches(query_token: str, listing_token: str) -> bool:
    if listing_token == query_token:
        return True
    if listing_token == f"rm{query_token}":
        return True
    return (
        listing_token.startswith(f"{query_token}-")
        or listing_token.startswith(f"{query_token}.")
    )


def _local_descriptor_tokens(listing_tokens: list[str], reference_index: int) -> list[str]:
    local: list[str] = []
    for token in listing_tokens[reference_index + 1 :]:
        if len(local) >= LOCAL_MATCH_WINDOW:
            break
        if _looks_like_query_descriptor_token(token):
            local.append(token)
            continue
        if _looks_like_model_or_price_token(token):
            break
        local.append(token)
    return local


# Segment expansion rules.
def _matching_segment_start(
    listing_text: str,
    token_matches: list[re.Match[str]],
    normalized_tokens: list[str],
    reference_start_index: int,
) -> int:
    keycap_price_start = _keycap_price_prefix_start(normalized_tokens, reference_start_index)
    if keycap_price_start is not None:
        return token_matches[keycap_price_start].start()
    ref_prefix_start = _ref_prefixed_segment_start(
        listing_text,
        token_matches,
        normalized_tokens,
        reference_start_index,
    )
    if ref_prefix_start is not None:
        return ref_prefix_start

    start_index = reference_start_index
    for index in range(reference_start_index - 1, -1, -1):
        if reference_start_index - index > LOCAL_PREFIX_WINDOW:
            break

        token = normalized_tokens[index]
        separator_between = _has_item_separator_between(
            listing_text,
            token_matches[index].end(),
            token_matches[index + 1].start(),
        )
        if separator_between and not _separator_keeps_prefix_detail(token):
            break

        next_token = normalized_tokens[index + 1] if index + 1 < len(normalized_tokens) else ""
        previous_token = normalized_tokens[index - 1] if index > 0 else ""
        if _looks_like_previous_product_boundary(token, next_token, previous_token):
            break
        start_index = index
    return token_matches[start_index].start()


def _ref_prefixed_segment_start(
    listing_text: str,
    token_matches: list[re.Match[str]],
    normalized_tokens: list[str],
    reference_start_index: int,
) -> int | None:
    ref_index = reference_start_index - 1
    if ref_index < 0 or normalized_tokens[ref_index] != "ref":
        return None

    start_index = ref_index
    for index in range(ref_index - 1, -1, -1):
        if ref_index - index > REF_PREFIX_WINDOW:
            break
        if _has_item_separator_between(
            listing_text,
            token_matches[index].end(),
            token_matches[index + 1].start(),
        ):
            break

        token = normalized_tokens[index]
        if token not in REF_PREFIX_TOKENS:
            break
        start_index = index
    return token_matches[start_index].start()


def _keycap_price_prefix_start(
    normalized_tokens: list[str],
    reference_start_index: int,
) -> int | None:
    price_suffix_index = reference_start_index - 1
    if price_suffix_index < 1:
        return None
    if normalized_tokens[price_suffix_index] not in {"k", "m"}:
        return None

    start_index = price_suffix_index - 1
    while start_index >= 0 and normalized_tokens[start_index].isdigit():
        start_index -= 1
    start_index += 1

    if price_suffix_index - start_index < 1:
        return None
    if price_suffix_index - start_index > 3:
        return None
    return start_index


def _matching_segment_end(
    listing_text: str,
    token_matches: list[re.Match[str]],
    reference_index: int,
) -> int:
    end = token_matches[reference_index].end()
    following_matches = token_matches[reference_index + 1 :]
    for offset, match in enumerate(following_matches, start=1):
        token = match.group(0)
        normalized_token = normalize_text(token)
        if offset > SEGMENT_MATCH_WINDOW:
            break
        next_match = following_matches[offset] if offset < len(following_matches) else None
        next_token = normalize_text(next_match.group(0)) if next_match else ""
        previous_token = (
            normalize_text(token_matches[reference_index + offset - 1].group(0))
            if reference_index + offset - 1 >= 0
            else ""
        )
        previous_previous_token = (
            normalize_text(token_matches[reference_index + offset - 2].group(0))
            if reference_index + offset - 2 >= 0
            else ""
        )
        previous_match = token_matches[reference_index + offset - 1]
        if _has_item_separator_between(
            listing_text,
            previous_match.end(),
            match.start(),
        ) and not _separator_keeps_continuation_detail(
            normalized_token,
            previous_token,
            next_token,
        ):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_next_product_brand(normalized_token, next_token):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_next_product_header(normalized_token, next_token):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_split_brand_header(normalized_token, next_token):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_metadata_boundary(normalized_token, next_token):
            if normalized_token in {"location", "loc"}:
                end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_post_price_service_tail(normalized_token, previous_token):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_next_item_after_price(
            normalized_token,
            next_token,
            previous_token,
            previous_previous_token,
        ):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_reference_after_complete_price(
            normalized_token,
            next_token,
            previous_token,
            previous_previous_token,
        ):
            end = _trim_trailing_section_marker(listing_text, end)
            break
        if _looks_like_product_reference_boundary(
            listing_text,
            match.start(),
            match.end(),
            normalized_token,
            next_token,
            previous_token,
        ):
            end = _trim_trailing_item_suffix(listing_text, end)
            break
        end = _include_trailing_non_token_suffix(
            listing_text,
            _include_trailing_currency_symbol(listing_text, match.end()),
        )
    return end


def _has_item_separator_between(listing_text: str, left_end: int, right_start: int) -> bool:
    return bool(re.search(f"[{ITEM_SEPARATOR_CHARS}]", listing_text[left_end:right_start]))


def _has_section_separator_between(listing_text: str, left_end: int, right_start: int) -> bool:
    between = listing_text[left_end:right_start]
    return bool(re.search(r"(?:-\s*\[\s*\]|[|•])", between))


# Boundary rules.
def _looks_like_metadata_boundary(token: str, next_token: str) -> bool:
    if token in {"location", "loc"}:
        return True
    return token in {"member", "seller", "dealer"} and bool(
        re.fullmatch(r"\d{3,}", next_token)
    )


def _looks_like_product_reference_boundary(
    listing_text: str,
    token_start: int,
    token_end: int,
    normalized_token: str,
    next_token: str,
    previous_token: str,
) -> bool:
    if not _looks_like_model_or_price_token(normalized_token):
        return False
    if _looks_like_ordinal_detail_token(normalized_token):
        return False
    if _looks_like_leetspeak_detail_token(normalized_token):
        return False
    if token_start > 0 and listing_text[token_start - 1] in CURRENCY_PREFIX_CHARS:
        return False
    if token_start > 0 and listing_text[token_start - 1] in {"/", "-"}:
        return False
    if _looks_like_year_token(normalized_token):
        return False
    if _looks_like_price_token(normalized_token):
        return False
    if _looks_like_decimal_price_before_currency_symbol(
        listing_text,
        token_end,
        normalized_token,
    ):
        return False
    if _looks_like_compact_currency_prefixed_price(
        listing_text[token_start:token_end],
    ):
        return False
    if _looks_like_decimal_price_before_currency(normalized_token, next_token):
        return False
    if _looks_like_plain_price_before_currency(normalized_token, next_token):
        return False
    if _looks_like_plain_price_before_label_note(normalized_token, next_token):
        return False
    if _looks_like_decimal_price_after_currency(normalized_token, previous_token):
        return False
    if _looks_like_plain_price_after_currency(normalized_token, previous_token):
        return False
    if _looks_like_plain_price_after_descriptor(normalized_token, previous_token):
        return False
    if _looks_like_plain_price_after_date(normalized_token, previous_token):
        return False
    if _looks_like_decimal_size_before_unit(normalized_token, next_token):
        return False
    if _looks_like_bare_reference_after_price(normalized_token, next_token, previous_token):
        return True
    if _looks_like_date_or_condition_token(normalized_token):
        return False
    if _looks_like_descriptor_number_token(normalized_token, next_token):
        return False
    if _looks_like_hyphenated_descriptor_number(normalized_token):
        return False
    if _looks_like_link_count_token(normalized_token):
        return False
    if _looks_like_repeat_reference_detail(normalized_token, next_token, previous_token):
        return False
    if _looks_like_caliber_token(normalized_token):
        return False
    if _looks_like_listing_stock_code(normalized_token):
        return False
    if _looks_like_size_token(normalized_token):
        return False
    if _looks_like_reference_detail_after_size(normalized_token, next_token, previous_token):
        return False
    return not any(
        currency in normalized_token for currency in ("hkd", "usd", "usdt", "eur", "aed", "chf")
    )


def _looks_like_next_product_brand(token: str, next_token: str) -> bool:
    if (token, next_token) in {("richard", "mille"), ("richard", "miller")}:
        return True
    if (token, next_token) in {("f.p", "journe")}:
        return True
    if (token, next_token) in {
        ("a", "lange"),
        ("a.lange", "s"),
        ("a.lange", "sohne"),
        ("lange", "sohne"),
    }:
        return True
    if token in {"f.p.journe", "fpjourne"}:
        return True
    if token in {"ap", "pp", "rm", "vc", "v.c"} and next_token in PRODUCT_STATUS_TOKENS:
        return True
    if token in {"ap", "pp", "rm", "vc", "v.c"}:
        return _looks_like_model_or_price_token(next_token)
    if token in PRODUCT_BRAND_TOKENS and next_token in PRODUCT_STATUS_TOKENS:
        return True
    return token in PRODUCT_BRAND_TOKENS and _looks_like_model_or_price_token(next_token)


def _looks_like_next_product_header(token: str, next_token: str) -> bool:
    return token in PRODUCT_HEADER_TOKENS and (
        next_token.isdigit() or _looks_like_model_or_price_token(next_token)
    )


def _looks_like_split_brand_header(token: str, next_token: str) -> bool:
    return token + next_token in {"ap", "pp", "rm", "vc"}


def _looks_like_post_price_service_tail(token: str, previous_token: str) -> bool:
    return token in {"welcome", "reconfirm"} and _looks_like_price_context_token(previous_token)


def _looks_like_next_item_after_price(
    token: str,
    next_token: str,
    previous_token: str,
    previous_previous_token: str = "",
) -> bool:
    if not (
        _looks_like_price_context_token(previous_token)
        or _looks_like_split_plain_thousands_price(
            previous_previous_token,
            previous_token,
        )
    ):
        return False
    if token in {"new", "used"}:
        return _looks_like_model_or_price_token(next_token) or next_token in {
            "ap",
            "cartier",
            "patek",
            "pp",
            "rm",
            "rolex",
        }
    return bool(re.fullmatch(r"\d{1,3}", token) and next_token in {"new", "used"})


def _looks_like_reference_after_complete_price(
    token: str,
    next_token: str,
    previous_token: str,
    previous_previous_token: str,
) -> bool:
    if not _looks_like_complete_price_before(previous_previous_token, previous_token):
        return False
    if _looks_like_year_token(token) or _looks_like_date_or_condition_token(token):
        return False
    if _looks_like_price_token(token):
        return False
    if _looks_like_plain_price_before_currency(token, next_token):
        return False
    if _looks_like_decimal_price_before_currency(token, next_token):
        return False
    return _looks_like_model_or_price_token(token) and bool(next_token)


def _looks_like_complete_price_before(first_token: str, second_token: str) -> bool:
    return bool(
        (
            _looks_like_price_context_token(first_token)
            and _looks_like_price_context_token(second_token)
        )
        or _looks_like_amount_currency_pair(first_token, second_token)
    )


def _looks_like_amount_currency_pair(amount_token: str, currency_token: str) -> bool:
    return bool(
        _looks_like_plain_price_before_currency(amount_token, currency_token)
        or _looks_like_decimal_price_before_currency(amount_token, currency_token)
    )


def _looks_like_split_plain_thousands_price(
    first_token: str,
    second_token: str,
) -> bool:
    return bool(
        re.fullmatch(r"\d{1,3}", first_token)
        and re.fullmatch(r"\d{3}", second_token)
    )


def _looks_like_named_month_date_token(token: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[-/]\d{2,4}",
            token,
        )
        or re.fullmatch(
            r"\d{2,4}[-/](?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)",
            token,
        )
    )


def _looks_like_price_context_token(token: str) -> bool:
    return (
        token in {"hkd", "usd", "usdt", "eur", "aed", "chf"}
        or _looks_like_price_token(token)
        or _looks_like_compact_currency_prefixed_price(token)
    )


def _looks_like_previous_product_boundary(
    token: str,
    next_token: str,
    previous_token: str,
) -> bool:
    if token in LOCAL_PREFIX_TOKENS:
        return False
    if _looks_like_year_token(token) or _looks_like_date_or_condition_token(token):
        if _looks_like_named_month_date_token(token) and _looks_like_model_or_price_token(
            next_token
        ):
            return True
        return False
    if _looks_like_size_token(token):
        return False
    if token in {"full", "set", "only", "watch"}:
        return False
    if _looks_like_model_or_price_token(token):
        if _looks_like_plain_price_before_currency(token, next_token):
            return True
        if _looks_like_price_token(token) or any(
            currency in token for currency in ("hkd", "usd", "usdt", "eur", "aed")
        ):
            return True
        if previous_token in {"hkd", "usd", "usdt", "eur", "aed"}:
            return True
        return True
    return True


def _include_trailing_currency_symbol(listing_text: str, end: int) -> int:
    while end < len(listing_text) and listing_text[end] in CURRENCY_PREFIX_CHARS:
        end += 1
    return end


def _include_trailing_non_token_suffix(listing_text: str, end: int) -> int:
    while end < len(listing_text) and not listing_text[end].isalnum():
        end += 1
    return end


def _trim_trailing_section_marker(listing_text: str, end: int) -> int:
    while end > 0 and not listing_text[end - 1].isalnum():
        end -= 1
    return end


def _trim_trailing_item_suffix(listing_text: str, end: int) -> int:
    while (
        end > 0
        and not listing_text[end - 1].isalnum()
        and listing_text[end - 1] not in CURRENCY_PREFIX_CHARS
    ):
        end -= 1
    return end


def _separator_keeps_prefix_detail(token: str) -> bool:
    return _looks_like_price_context_token(token)


def _separator_keeps_continuation_detail(
    token: str,
    previous_token: str,
    next_token: str,
) -> bool:
    if _looks_like_price_context_token(token):
        return True
    if _looks_like_split_thousands_price(token, next_token):
        return True
    return _looks_like_date_or_condition_token(token)


def _looks_like_non_sale_request(normalized_listing: str) -> bool:
    tokens = normalized_listing.split()
    if not tokens:
        return False
    head = " ".join(tokens[:4])
    return bool(
        tokens[0] in {"lookingfor", "wtb"}
        or head.startswith("looking for")
        or head.startswith("want to buy")
    )


# Display cleanup rules.
def _clean_display_text(value: str) -> str:
    cleaned = " ".join(value.split())
    cleaned = re.sub(f"\\s*[{ITEM_SEPARATOR_CHARS}]+\\s*$", "", cleaned)
    cleaned = re.sub(r"\s*[•|]+\s*$", "", cleaned)
    cleaned = re.sub(r"\s*(?:👤|📅|🗓️)\s*$", "", cleaned)
    cleaned = re.sub(r"\s+(?:P\.p|PP|AP|Patek Philippe|Audemars Piguet)\s*[^\w\s$./-]*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[❣❶-❿➊-➓]\ufe0f?\s*$", "", cleaned)
    if not re.search(r"\blike\s+new\s*$", cleaned, flags=re.IGNORECASE):
        cleaned = re.sub(r"(?:\s*[^\w\s$./-]*\s*new\s*)+$", "", cleaned, flags=re.IGNORECASE)
    return cleaned
