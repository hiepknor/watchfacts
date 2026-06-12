from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Protocol, TypeVar

from app.searching.matcher import normalize_text


class HasDedupeFields(Protocol):
    listing_text: str
    seller: str | None
    posted_date: str | None


T = TypeVar("T", bound=HasDedupeFields)

PRODUCT_REFERENCE_RE = re.compile(
    r"\b(?=[A-Za-z0-9/.-]*\d)[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"""
    (?:
        [$€£¥💲]\s*\d+(?:[,\s.]\d+)*(?:[km])?\s*(?:hkd|usd|usdt|eur|aed|chf)?
        |
        \b(?:hkd|usd|usdt|eur|aed|chf)\s*(?:\d{3,}(?:[,\s.]\d+)*(?:[km])?|\d+(?:\.\d+)?[km])
        |
        \b\d{1,3}(?:[,\s.]\d{3})+(?:\.\d+)?\s*(?:hkd|usd|usdt|eur|aed|chf)?\b
        |
        \b\d+(?:\.\d+)?[km]\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def dedupe_key(
    listing_text: str,
    seller: str | None = None,
    posted_date: str | None = None,
) -> str:
    return "|".join(
        [
            normalize_text(listing_text),
            normalize_text(seller),
            normalize_text(posted_date),
        ]
    )


def unique_listings(listings: Iterable[T]) -> list[T]:
    seen: set[str] = set()
    unique: list[T] = []

    for listing in listings:
        key = dedupe_key(
            listing.listing_text,
            seller=listing.seller,
            posted_date=listing.posted_date,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(listing)

    return unique


def unique_latest_listings(listings: Iterable[T]) -> list[T]:
    unique_by_key: dict[str, T] = {}
    order: list[str] = []

    for listing in listings:
        key = latest_dedupe_key(listing.listing_text, seller=listing.seller)
        if key not in unique_by_key:
            unique_by_key[key] = listing
            order.append(key)
            continue

        if _is_newer(listing.posted_date, unique_by_key[key].posted_date):
            unique_by_key[key] = listing

    return [unique_by_key[key] for key in order]


def unique_latest_by_text(listings: Iterable[T]) -> list[T]:
    unique_by_key: dict[str, T] = {}
    order: list[str] = []

    for listing in listings:
        key = normalize_text(listing.listing_text)
        if key not in unique_by_key:
            unique_by_key[key] = listing
            order.append(key)
            continue

        if _is_newer(listing.posted_date, unique_by_key[key].posted_date):
            unique_by_key[key] = listing

    return [unique_by_key[key] for key in order]


def latest_dedupe_key(listing_text: str, seller: str | None = None) -> str:
    return "|".join([_latest_listing_signature(listing_text), normalize_text(seller)])


def _latest_listing_signature(listing_text: str) -> str:
    references = [
        normalize_text(match.group(0))
        for match in PRODUCT_REFERENCE_RE.finditer(listing_text)
        if _looks_like_product_reference(match.group(0))
    ]
    prices = [_compact_value(match.group(0)) for match in PRICE_RE.finditer(listing_text)]
    if references and prices:
        return " ".join([*dict.fromkeys(references), *dict.fromkeys(prices)])
    if references:
        return " ".join(dict.fromkeys(references))
    return normalize_text(listing_text)


def _looks_like_product_reference(token: str) -> bool:
    normalized = normalize_text(token)
    if re.fullmatch(r"n?\d{1,2}[/-]\d{2,4}y?", normalized):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?[km]", normalized):
        return False
    if normalized.isdigit() and len(normalized) == 4:
        year = int(normalized)
        if 1900 <= year <= 2099:
            return False
    if any(currency in normalized for currency in ("hkd", "usd", "usdt", "eur", "aed", "chf")):
        return False
    if len(normalized) < 4 and "/" not in normalized:
        return False
    return True


def _compact_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_newer(candidate_date: str | None, current_date: str | None) -> bool:
    candidate = _parse_posted_date(candidate_date)
    current = _parse_posted_date(current_date)
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current


def _parse_posted_date(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.split("·", maxsplit=1)[0].strip()
    for date_format in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:19], date_format)
        except ValueError:
            continue
    return None
