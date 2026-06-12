from __future__ import annotations

import re

from app.searching.matcher_normalization import TOKEN_RE
from app.searching.matcher_aliases import canonicalize_descriptor_token


CURRENCY_PREFIX_CHARS = {"$", "€", "£", "¥", "💲"}
QUERY_TERM_RE = TOKEN_RE
MONTH_NAME_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
QUERY_CONNECTOR_TOKENS = {"and", "or"}


def parse_query_terms(query: str) -> tuple[list[list[str]], list[str]]:
    reference_terms: list[list[str]] = []
    descriptor_tokens: list[str] = []
    tokens = [match.group(0).casefold() for match in QUERY_TERM_RE.finditer(query)]

    index = 0
    while index < len(tokens):
        raw_token = canonicalize_descriptor_token(tokens[index])
        if not raw_token:
            index += 1
            continue
        next_token = (
            canonicalize_descriptor_token(tokens[index + 1])
            if index + 1 < len(tokens)
            else ""
        )
        token_after_next = (
            canonicalize_descriptor_token(tokens[index + 2])
            if index + 2 < len(tokens)
            else ""
        )

        parts = [raw_token]
        if (
            raw_token not in QUERY_CONNECTOR_TOKENS
            and next_token == "or"
            and raw_token.isdigit()
            and token_after_next.isalpha()
        ):
            parts = [f"{raw_token}or"]
            index += 2
        else:
            index += 1

        parts = [part for part in parts if part not in QUERY_CONNECTOR_TOKENS]
        if not parts:
            continue
        if any(looks_like_reference_token(part) for part in parts) and not all(
            looks_like_query_descriptor_token(part) for part in parts
        ):
            reference_terms.append(parts)
        else:
            descriptor_tokens.extend(parts)

    return reference_terms, descriptor_tokens


def looks_like_query_descriptor_token(token: str) -> bool:
    if re.fullmatch(r"\d{2}-\d{2}", token):
        return False
    return (
        looks_like_year_token(token)
        or looks_like_price_token(token)
        or looks_like_date_or_condition_token(token)
        or looks_like_plain_price_before_currency(token, "")
    )


def looks_like_reference_token(token: str) -> bool:
    return any(char.isdigit() for char in token)


def looks_like_model_or_price_token(token: str) -> bool:
    return any(char.isdigit() for char in token) and len(token) >= 4


def looks_like_price_token(token: str) -> bool:
    return bool(
        re.fullmatch(r"\d+(?:\.\d+)?(?:k|m)", token)
        or re.fullmatch(r"\d+(?:\.\d+)?(?:k|m)(?:hk|hkd|usd|usdt|eur|aed|chf)?", token)
        or re.fullmatch(r"\d{4,7}u", token)
        or re.fullmatch(r"\d{5,8}(?:hk|hkd|usd|usdt|eur|aed|chf)", token)
        or re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:hk|hkd)?", token)
        or re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:\.\d+)?", token)
        or re.fullmatch(r"\d{3}\.\d{2}", token)
        or re.fullmatch(r"n?\d+[/-]\d+(?:\.\d+)?(?:k|m)", token)
    )


def looks_like_decimal_price_before_currency_symbol(
    listing_text: str,
    token_end: int,
    token: str,
) -> bool:
    if not re.fullmatch(r"\d{1,4}\.\d{1,3}", token):
        return False
    index = token_end
    while index < len(listing_text) and listing_text[index].isspace():
        index += 1
    return index < len(listing_text) and listing_text[index] in CURRENCY_PREFIX_CHARS


def looks_like_compact_currency_prefixed_price(token: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?i)(?:hk|hkd|usd|usdt|eur|aed|chf)\s*\d+(?:[,.]\d+)*(?:k|m)?",
            token.strip(),
        )
    )


def looks_like_date_or_condition_token(token: str) -> bool:
    return bool(
        re.fullmatch(r"n?\d{1,2}[/-]\d{1,4}(?:y|new)?", token)
        or re.fullmatch(r"\d{1,2}n[/-]\d{1,4}y?", token)
        or re.fullmatch(r"\d{1,2}[/-]n\d{1,2}", token)
        or re.fullmatch(r"\d{1,2}[/-]\d{3,4}p?", token)
        or re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", token)
        or re.fullmatch(r"\d{4}[/-]\d{1,2}", token)
        or re.fullmatch(r"\d{4}[/-]n\d{1,2}", token)
        or re.fullmatch(r"\d{4}\.\d{1,2}", token)
        or re.fullmatch(r"\d{1,2}-\d{4}new", token)
        or re.fullmatch(r"\d{4}-\d{4}(?:-\d{4})?", token)
        or re.fullmatch(r"\d{1,2}\.\d{4}", token)
        or re.fullmatch(rf"{MONTH_NAME_RE}[-/]\d{{2,4}}", token)
        or re.fullmatch(rf"\d{{2,4}}[-/]{MONTH_NAME_RE}", token)
        or re.fullmatch(r"\d{4}(?:y|year|full|fullset|like|used|new)", token)
        or re.fullmatch(r"\d{4}n\d{1,2}", token)
        or re.fullmatch(r"(?:new|n)\d{1,2}[/-]\d{1,4}", token)
        or re.fullmatch(r"[a-z]+\d{4}y?", token)
    )


def looks_like_descriptor_number_token(token: str, next_token: str) -> bool:
    return bool(re.fullmatch(r"\d{3,4}", token) and next_token.isalpha())


def looks_like_ordinal_detail_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(?:st|nd|rd|th)", token))


def looks_like_leetspeak_detail_token(token: str) -> bool:
    return token in {"b0x", "d1al", "pap3rs", "r3ady", "reta1l"}


def looks_like_hyphenated_descriptor_number(token: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}-[a-z]+", token))


def looks_like_bare_reference_after_price(
    token: str,
    next_token: str,
    previous_token: str,
) -> bool:
    if not re.fullmatch(r"\d{2}-\d{2}", token):
        return False
    if not next_token.isalpha() or next_token in {"new", "used", "full", "set"}:
        return False
    return looks_like_price_token(previous_token) or looks_like_compact_currency_prefixed_price(
        previous_token
    )


def looks_like_link_count_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}links?", token))


def looks_like_repeat_reference_detail(
    token: str,
    next_token: str,
    previous_token: str,
) -> bool:
    return bool(
        re.fullmatch(r"\d{4}", token)
        and previous_token.isalpha()
        and (
            looks_like_year_token(next_token)
            or looks_like_date_or_condition_token(next_token)
        )
    )


def looks_like_plain_price_before_currency(token: str, next_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{5,8}", token)
        and next_token in {"hkd", "usd", "usdt", "eur", "aed", "chf"}
    )


def looks_like_plain_price_before_label_note(token: str, next_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{5,8}|\d{1,3}\.\d{1,3}", token)
        and next_token in {"lab", "label", "lbl", "lnl", "ship", "shipping"}
    )


def looks_like_split_thousands_price(token: str, next_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{1,3}", token)
        and re.fullmatch(r"\d{3}(?:hk|hkd|usd|usdt|eur|aed|chf)?", next_token)
    )


def looks_like_decimal_price_before_currency(token: str, next_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{1,4}\.\d{1,3}", token)
        and next_token in {"hkd", "usd", "usdt", "eur", "aed", "chf"}
    )


def looks_like_decimal_size_before_unit(token: str, next_token: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}\.\d{1,2}", token) and next_token == "mm")


def looks_like_plain_price_after_date(token: str, previous_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{5,8}", token)
        and (
            looks_like_year_token(previous_token)
            or looks_like_date_or_condition_token(previous_token)
        )
    )


def looks_like_plain_price_after_currency(token: str, previous_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{5,8}", token)
        and previous_token in {"hkd", "usd", "usdt", "eur", "aed", "chf"}
    )


def looks_like_decimal_price_after_currency(token: str, previous_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{1,4}\.\d{1,3}", token)
        and previous_token in {"hkd", "usd", "usdt", "eur", "aed", "chf"}
    )


def looks_like_plain_price_after_descriptor(token: str, previous_token: str) -> bool:
    return bool(
        re.fullmatch(r"\d{5,8}", token)
        and previous_token in {"full", "set", "fullset", "used", "new", "nos", "likenew"}
    )


def looks_like_caliber_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{3}[a-z]{1,3}", token))


def looks_like_listing_stock_code(token: str) -> bool:
    return bool(re.fullmatch(r"sw\d{2,5}", token))


def looks_like_size_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d{2,3}mm(?:\d{4})?", token))


def looks_like_reference_detail_after_size(
    token: str,
    next_token: str,
    previous_token: str,
) -> bool:
    if not previous_token or not looks_like_size_token(previous_token):
        return False
    return "/" in token or looks_like_date_or_condition_token(next_token)


def looks_like_year_token(token: str) -> bool:
    if not token.isdigit() or len(token) != 4:
        return False
    year = int(token)
    return 1900 <= year <= 2099
