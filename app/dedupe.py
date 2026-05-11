from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol, TypeVar

from app.matcher import normalize_text


class HasDedupeFields(Protocol):
    listing_text: str
    seller: str | None
    posted_date: str | None


T = TypeVar("T", bound=HasDedupeFields)


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


def latest_dedupe_key(listing_text: str, seller: str | None = None) -> str:
    return "|".join([normalize_text(listing_text), normalize_text(seller)])


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
