from __future__ import annotations

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
