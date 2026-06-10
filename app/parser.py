from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, Tag


@dataclass(frozen=True)
class ListingCandidate:
    listing_text: str
    seller: str | None = None
    seller_phone: str | None = None
    posted_date: str | None = None
    image_url: str | None = None
    source_url: str | None = None
    match_text: str | None = None


LISTING_SELECTORS = [
    "[data-listing]",
    "article",
    ".listing",
    ".listing-card",
    ".watch-listing",
    ".product",
]


def parse_listings(html: str) -> list[ListingCandidate]:
    json_candidates = _parse_json_listings(html)
    if json_candidates is not None:
        return json_candidates

    soup = BeautifulSoup(html, "lxml")
    nodes = _find_listing_nodes(soup)
    return [candidate for node in nodes if (candidate := _parse_listing_node(node))]


def _parse_json_listings(value: str) -> list[ListingCandidate] | None:
    stripped = value.lstrip()
    if not stripped.startswith(("{", "[")):
        return None

    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict):
        listings = payload.get("listings")
    else:
        listings = payload
    if not isinstance(listings, list):
        return None

    candidates: list[ListingCandidate] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for item in listings:
        if not isinstance(item, dict):
            continue
        for candidate in _parse_json_listing_item(item):
            key = (
                candidate.listing_text,
                candidate.seller,
                candidate.posted_date,
                candidate.source_url,
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return candidates


def _parse_json_listing_item(item: dict[str, Any]) -> list[ListingCandidate]:
    seller = _clean_text(item.get("companyName")) or _clean_text(item.get("fromName"))
    posted_date = _format_json_date(item.get("repostedAt") or item.get("createdOn"))
    source_url = _json_source_url(item)
    parent_text = _clean_text(item.get("title"))
    parent_image = _json_image_url(item)
    parent_colors = _json_dial_colors(item)
    parent_match_text = _build_match_text(
        parent_text,
        *parent_colors,
    )

    candidates: list[ListingCandidate] = []
    if parent_text:
        candidates.append(
            ListingCandidate(
                listing_text=parent_text,
                seller=seller or None,
                seller_phone=_json_seller_phone(item),
                posted_date=posted_date,
                image_url=parent_image,
                source_url=source_url,
                match_text=parent_match_text,
            )
        )

    nested = item.get("listings")
    if isinstance(nested, list):
        for nested_item in nested:
            if not isinstance(nested_item, dict):
                continue
            nested_title = _clean_text(nested_item.get("title"))
            if nested_title and nested_title == parent_text:
                continue
            nested_text = _json_nested_text(nested_item)
            if not nested_text or nested_text == parent_text:
                continue
            nested_match_text = _build_match_text(nested_text, parent_match_text)
            candidates.append(
                ListingCandidate(
                    listing_text=nested_text,
                    seller=seller or None,
                    seller_phone=_json_seller_phone(item),
                    posted_date=posted_date,
                    image_url=_json_image_url(nested_item) or parent_image,
                    source_url=source_url,
                    match_text=nested_match_text,
                )
            )
    return candidates


def _find_listing_nodes(soup: BeautifulSoup) -> list[Tag]:
    for selector in LISTING_SELECTORS:
        nodes = soup.select(selector)
        if nodes:
            return [node for node in nodes if isinstance(node, Tag)]
    return []


def _parse_listing_node(node: Tag) -> ListingCandidate | None:
    listing_text = _field_text(
        node,
        "listing-text",
        ".product-description .title-link, .listing-text, .text, .title",
    )
    if not listing_text:
        listing_text = _clean_text(node.get_text(" ", strip=True))
    if not listing_text:
        return None

    return ListingCandidate(
        listing_text=listing_text,
        seller=_field_text(
            node,
            "seller",
            ".product-rate-removed .blur-premium, .seller, .seller-name",
        ),
        seller_phone=_html_seller_phone(node),
        posted_date=_field_text(
            node,
            "posted-date",
            '[id^="countDownText"] .text-dark, .posted-date, .date, time',
        ),
        image_url=_image_url(node),
        source_url=_source_url(node),
    )


def _field_text(node: Tag, data_field: str, selector: str) -> str | None:
    field = node.select_one(f'[data-field="{data_field}"]')
    if field is None:
        field = node.select_one(selector)
    if field is None:
        return None
    return _clean_text(field.get_text(" ", strip=True)) or None


def _image_url(node: Tag) -> str | None:
    image = node.select_one("img")
    if image is None:
        return None
    value = image.get("src") or image.get("data-src")
    return _clean_text(value) if isinstance(value, str) else None


def _source_url(node: Tag) -> str | None:
    link = node.select_one("a[href]")
    if link is None:
        return None
    value = link.get("href")
    return _clean_text(value) if isinstance(value, str) else None


def _clean_text(value: str | None) -> str:
    return " ".join(str(value).split()) if value else ""


def _build_match_text(*parts: str | None) -> str:
    return " ".join(part for part in parts if part)


def _json_dial_colors(item: dict[str, Any]) -> list[str]:
    colors: list[str] = []
    color = _clean_text(item.get("dialColor"))
    if color:
        colors.append(color)

    nested_listings = item.get("listings")
    if isinstance(nested_listings, list):
        for nested_item in nested_listings:
            if not isinstance(nested_item, dict):
                continue
            nested_color = _clean_text(nested_item.get("dialColor"))
            if nested_color and nested_color not in colors:
                colors.append(nested_color)

    return colors


def _json_nested_text(item: dict[str, Any]) -> str:
    parts = [
        _clean_text(item.get("brand")),
        _clean_text(item.get("model")),
        _clean_text(item.get("normalizedReference") or item.get("reference")),
        _clean_text(item.get("dialColor")),
        _clean_text(item.get("title")),
    ]
    return _clean_text(" ".join(part for part in parts if part))


def _json_image_url(item: dict[str, Any]) -> str | None:
    value = item.get("frontImage") or item.get("imageUrl") or item.get("image_url")
    if isinstance(value, str):
        return _clean_text(value) or None

    nested = item.get("listings")
    if isinstance(nested, list):
        for nested_item in nested:
            if isinstance(nested_item, dict):
                nested_url = _json_image_url(nested_item)
                if nested_url:
                    return nested_url
    return None


def _json_seller_phone(item: dict[str, Any]) -> str | None:
    return (
        _clean_phone(item.get("companyWhatsapp"))
        or _clean_phone(item.get("whatsappNumber"))
        or _clean_phone(item.get("phoneNumber"))
    )


def _html_seller_phone(node: Tag) -> str | None:
    field = node.select_one('[data-field="seller-phone"], [data-field="phone"]')
    if field is not None:
        value = field.get("content") or field.get_text(" ", strip=True)
        phone = _clean_phone(value)
        if phone:
            return phone

    link = node.select_one(
        'a[href^="https://wa.me/"], a[href^="http://wa.me/"], '
        'a[href*="api.whatsapp.com/send"], a[href^="whatsapp:"]'
    )
    if link is None:
        return None
    href = link.get("href")
    if not isinstance(href, str):
        return None
    return _clean_phone(href)


def _clean_phone(value: object) -> str | None:
    if value is None:
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) < 8 or len(digits) > 15:
        return None
    if digits.startswith("0"):
        return None
    return digits


def _json_source_url(item: dict[str, Any]) -> str | None:
    number = item.get("number")
    if number is None:
        return None
    return f"/flash-sales/{number}"


def _format_json_date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    for date_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(normalized[:19], date_format)
            return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
        except ValueError:
            continue
    return _clean_text(normalized)
