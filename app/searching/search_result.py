from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.searching.matcher import normalize_text


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
    scope_reason: str | None = None
    image_reason: str | None = None
    price_reason: str | None = None
    segment_reason_codes: tuple[str, ...] = ()


def source_result_id(query: str, rank: int, result: SearchResult) -> str:
    payload = {
        "query": query,
        "rank": rank,
        "listingText": result.listing_text,
        "rawListingText": result.raw_listing_text,
        "sourceUrl": result.source_url,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"watchfacts:{digest[:24]}"


def stable_listing_id(result: SearchResult) -> str:
    source_url = result.source_url
    normalized_payload: str

    if source_url:
        try:
            parsed = urlsplit(source_url)
            normalized_url = urlunsplit(
                (
                    parsed.scheme or "",
                    parsed.netloc.lower(),
                    parsed.path,
                    "",
                    "",
                )
            )
        except ValueError:
            normalized_url = source_url
        listing_signature = normalize_text(result.listing_text)
        if listing_signature:
            normalized_payload = f"{normalized_url}\0{listing_signature}"
        else:
            normalized_payload = normalized_url
    else:
        listing_text = result.listing_text or ""
        fallback_signature = "|".join(
            part
            for part in (
                listing_text,
                result.seller or "",
                result.posted_date or "",
                result.seller_phone or "",
                result.raw_listing_text or "",
            )
            if part
        )
        normalized_payload = fallback_signature or "unknown-listing"

    digest = hashlib.sha256(
        normalized_payload.encode("utf-8"),
    ).hexdigest()
    return f"watchfacts-listing:{digest[:24]}"


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
    if result.scope_reason is not None:
        payload["scope_reason"] = result.scope_reason
    if result.image_reason is not None:
        payload["image_reason"] = result.image_reason
    if result.price_reason is not None:
        payload["price_reason"] = result.price_reason
    if result.segment_reason_codes:
        payload["segment_reason_codes"] = list(result.segment_reason_codes)
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
