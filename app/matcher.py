from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Protocol, TypeVar


TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*", re.IGNORECASE)
QUERY_TERM_RE = TOKEN_RE
LOCAL_MATCH_WINDOW = 12
SEGMENT_MATCH_WINDOW = 20
PRODUCT_BRAND_TOKENS = {
    "audemars",
    "cartier",
    "patek",
    "richard",
    "rolex",
    "vacheron",
}


class HasListingText(Protocol):
    listing_text: str


T = TypeVar("T", bound=HasListingText)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = TOKEN_RE.findall(normalized)
    return " ".join(tokens)


def tokenize_query(query: str) -> list[str]:
    return normalize_text(query).split()


def listing_matches(query: str, listing_text: str) -> bool:
    reference_terms, descriptor_tokens = _parse_query_terms(query)
    if not reference_terms and not descriptor_tokens:
        return False

    normalized_listing = normalize_text(listing_text)
    listing_token_list = normalized_listing.split()
    listing_tokens = set(listing_token_list)
    compact_listing = _compact_text(normalized_listing)

    if not all(token in listing_tokens for token in descriptor_tokens):
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
    reference_terms, descriptor_tokens = _parse_query_terms(query)
    if not reference_terms:
        return listing_text

    token_matches = list(TOKEN_RE.finditer(listing_text))
    normalized_tokens = [normalize_text(match.group(0)) for match in token_matches]
    fallback: tuple[int, int] | None = None
    for reference_term in reference_terms:
        term_length = len(reference_term)
        for index in range(len(normalized_tokens) - term_length + 1):
            if normalized_tokens[index : index + term_length] != reference_term:
                continue

            reference_index = index + term_length - 1
            if fallback is None:
                fallback = (index, reference_index)
            if descriptor_tokens and not all(
                descriptor
                in _local_descriptor_tokens(normalized_tokens, reference_index)
                for descriptor in descriptor_tokens
            ):
                continue

            start = token_matches[index].start()
            end = _matching_segment_end(
                listing_text,
                token_matches,
                reference_index,
            )
            return _clean_display_text(listing_text[start:end])

    if fallback is not None:
        index, reference_index = fallback
        start = token_matches[index].start()
        end = _matching_segment_end(listing_text, token_matches, reference_index)
        return _clean_display_text(listing_text[start:end])

    return listing_text


def _parse_query_terms(query: str) -> tuple[list[list[str]], list[str]]:
    reference_terms: list[list[str]] = []
    descriptor_tokens: list[str] = []
    for match in QUERY_TERM_RE.finditer(query):
        parts = normalize_text(match.group(0)).split()
        if not parts:
            continue
        if any(_looks_like_reference_token(part) for part in parts):
            reference_terms.append(parts)
        else:
            descriptor_tokens.extend(parts)
    return reference_terms, descriptor_tokens


def _looks_like_reference_token(token: str) -> bool:
    return any(char.isdigit() for char in token)


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
    reference_index = _find_reference_term_index(reference_term, listing_tokens)
    if reference_index is None:
        compact_reference = _compact_text("".join(reference_term))
        if compact_reference in _compact_text("".join(listing_tokens)):
            return all(descriptor in listing_tokens for descriptor in descriptor_tokens)
        return False

    local_tokens = set(
        _local_descriptor_tokens(
            listing_tokens,
            reference_index + len(reference_term) - 1,
        )
    )
    if all(descriptor in local_tokens for descriptor in descriptor_tokens):
        return True

    return False


def _find_reference_term_index(
    reference_term: list[str],
    listing_tokens: list[str],
) -> int | None:
    term_length = len(reference_term)
    for index in range(len(listing_tokens) - term_length + 1):
        if listing_tokens[index : index + term_length] == reference_term:
            return index
    return None


def _local_descriptor_tokens(listing_tokens: list[str], reference_index: int) -> list[str]:
    local: list[str] = []
    for token in listing_tokens[reference_index + 1 :]:
        if len(local) >= LOCAL_MATCH_WINDOW:
            break
        if _looks_like_model_or_price_token(token):
            break
        local.append(token)
    return local


def _looks_like_model_or_price_token(token: str) -> bool:
    return any(char.isdigit() for char in token) and len(token) >= 4


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
        if _looks_like_next_product_brand(normalized_token, next_token):
            break
        if _looks_like_product_reference_boundary(
            listing_text,
            match.start(),
            normalized_token,
            next_token,
        ):
            break
        end = _include_trailing_currency_symbol(listing_text, match.end())
    return end


def _looks_like_product_reference_boundary(
    listing_text: str,
    token_start: int,
    normalized_token: str,
    next_token: str,
) -> bool:
    if not _looks_like_model_or_price_token(normalized_token):
        return False
    if token_start > 0 and listing_text[token_start - 1] == "$":
        return False
    if token_start > 0 and listing_text[token_start - 1] in {"/", "-"}:
        return False
    if _looks_like_year_token(normalized_token):
        return False
    if _looks_like_price_token(normalized_token):
        return False
    if _looks_like_plain_price_before_currency(normalized_token, next_token):
        return False
    if _looks_like_date_or_condition_token(normalized_token):
        return False
    if _looks_like_caliber_token(normalized_token):
        return False
    if _looks_like_size_token(normalized_token):
        return False
    return not any(
        currency in normalized_token for currency in ("hkd", "usd", "eur", "aed")
    )


def _looks_like_next_product_brand(token: str, next_token: str) -> bool:
    return token in PRODUCT_BRAND_TOKENS and _looks_like_model_or_price_token(next_token)


def _include_trailing_currency_symbol(listing_text: str, end: int) -> int:
    while end < len(listing_text) and listing_text[end] in {"$", "€", "£", "¥"}:
        end += 1
    return end


def _looks_like_price_token(token: str) -> bool:
    return bool(
        re.fullmatch(r"\d+(?:\.\d+)?(?:k|m)", token)
        or re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:\.\d+)?", token)
        or re.fullmatch(r"\d{3}\.\d{2}", token)
        or re.fullmatch(r"n?\d+[/-]\d+(?:\.\d+)?(?:k|m)", token)
    )


def _looks_like_date_or_condition_token(token: str) -> bool:
    return bool(
        re.fullmatch(r"n?\d{1,2}[/-]\d{1,4}y?", token)
        or re.fullmatch(r"\d{4}[/-]\d{1,2}", token)
        or re.fullmatch(r"\d{4}-\d{4}(?:-\d{4})?", token)
        or re.fullmatch(r"\d{1,2}\.\d{4}", token)
        or re.fullmatch(r"\d{4}(?:y|year|full)", token)
    )


def _looks_like_plain_price_before_currency(token: str, next_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{5,8}", token)
        and next_token in {"hkd", "usd", "usdt", "eur", "aed"}
    )


def _looks_like_caliber_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{3}[a-z]{1,3}", token))


def _looks_like_size_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{2,3}mm", token))


def _looks_like_year_token(token: str) -> bool:
    if not token.isdigit() or len(token) != 4:
        return False
    year = int(token)
    return 1900 <= year <= 2099


def _clean_display_text(value: str) -> str:
    cleaned = " ".join(value.split())
    return re.sub(r"(?:\s*[^\w\s$./-]*\s*new\s*)+$", "", cleaned, flags=re.IGNORECASE)


def _compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
