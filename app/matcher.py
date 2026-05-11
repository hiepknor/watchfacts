from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Protocol, TypeVar


TOKEN_RE = re.compile(r"[a-z0-9]+")


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
    query_tokens = tokenize_query(query)
    if not query_tokens:
        return False

    normalized_listing = normalize_text(listing_text)
    listing_tokens = set(normalized_listing.split())
    compact_listing = normalized_listing.replace(" ", "")

    return all(
        token in listing_tokens
        or (_looks_like_reference_token(token) and token in compact_listing)
        for token in query_tokens
    )


def filter_matching_listings(query: str, listings: Iterable[T]) -> list[T]:
    return [
        listing
        for listing in listings
        if listing_matches(query, listing.listing_text)
    ]


def _looks_like_reference_token(token: str) -> bool:
    return any(char.isdigit() for char in token)
