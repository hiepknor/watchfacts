from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag


@dataclass(frozen=True)
class ListingCandidate:
    listing_text: str
    seller: str | None = None
    posted_date: str | None = None
    image_url: str | None = None
    source_url: str | None = None


LISTING_SELECTORS = [
    "[data-listing]",
    "article",
    ".listing",
    ".listing-card",
    ".watch-listing",
]


def parse_listings(html: str) -> list[ListingCandidate]:
    soup = BeautifulSoup(html, "lxml")
    nodes = _find_listing_nodes(soup)
    return [candidate for node in nodes if (candidate := _parse_listing_node(node))]


def _find_listing_nodes(soup: BeautifulSoup) -> list[Tag]:
    for selector in LISTING_SELECTORS:
        nodes = soup.select(selector)
        if nodes:
            return [node for node in nodes if isinstance(node, Tag)]
    return []


def _parse_listing_node(node: Tag) -> ListingCandidate | None:
    listing_text = _field_text(node, "listing-text", ".listing-text, .text, .title")
    if not listing_text:
        listing_text = _clean_text(node.get_text(" ", strip=True))
    if not listing_text:
        return None

    return ListingCandidate(
        listing_text=listing_text,
        seller=_field_text(node, "seller", ".seller, .seller-name"),
        posted_date=_field_text(node, "posted-date", ".posted-date, .date, time"),
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
    return " ".join(value.split()) if value else ""
