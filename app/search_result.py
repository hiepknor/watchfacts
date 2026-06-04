from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchResult:
    listing_text: str
    seller: str | None = None
    posted_date: str | None = None
    image_url: str | None = None
    source_url: str | None = None
    similar_results: tuple["SearchResult", ...] = ()
    raw_listing_text: str | None = None
    seller_phone: str | None = None


def search_result_to_dict(
    result: SearchResult,
    *,
    include_similar: bool = True,
    include_raw: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "listing_text": result.listing_text,
        "seller": result.seller,
        "posted_date": result.posted_date,
        "image_url": result.image_url,
        "source_url": result.source_url,
        "seller_phone": result.seller_phone,
    }
    if include_similar:
        payload["similar_results"] = [
            search_result_to_dict(
                similar,
                include_similar=True,
                include_raw=include_raw,
            )
            for similar in result.similar_results
        ]
    else:
        payload["similar_results"] = []
    if include_raw:
        payload["raw_listing_text"] = result.raw_listing_text
    return payload


def search_results_to_dicts(
    results: list[SearchResult],
    *,
    include_similar: bool = True,
    include_raw: bool = True,
) -> list[dict[str, Any]]:
    return [
        search_result_to_dict(
            result,
            include_similar=include_similar,
            include_raw=include_raw,
        )
        for result in results
    ]
