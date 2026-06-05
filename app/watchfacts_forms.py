from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


SEARCH_FORM_SELECTOR = "#mode3Form"


@dataclass(frozen=True)
class SearchFormFields:
    action_url: str
    token: str


def search_form_data(token: str, query: str) -> dict[str, str]:
    return {
        "_token": token,
        "listingType": "sale",
        "reference": query,
        "region": "",
        "dial_color": "",
        "is_bundle": "",
        "sort_by": "price-low",
        "created_days": "90",
    }


def extract_search_form_fields(base_url: str, html: str) -> SearchFormFields | None:
    soup = BeautifulSoup(html, "lxml")
    form = soup.select_one(SEARCH_FORM_SELECTOR)
    if form is None:
        return None

    token_input = form.select_one('input[name="_token"]')
    token = token_input.get("value") if token_input is not None else None
    action = form.get("action")
    if not isinstance(token, str) or not token:
        return None
    if not isinstance(action, str) or not action:
        return None

    action_url = urljoin(base_url, action)
    if not same_watchfacts_site(base_url, action_url):
        raise ValueError("WatchFacts search form action is cross-origin")
    return SearchFormFields(action_url=action_url, token=token)


def same_watchfacts_site(left_url: str, right_url: str) -> bool:
    left = urlparse(left_url)
    right = urlparse(right_url)
    return (
        left.scheme == right.scheme
        and _without_leading_www(left.hostname or "")
        == _without_leading_www(right.hostname or "")
        and (left.port or _default_port(left.scheme))
        == (right.port or _default_port(right.scheme))
    )


def url_host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").casefold()


def cookie_domain_matches_host(domain: str, host: str) -> bool:
    normalized_domain = domain.lstrip(".").casefold()
    normalized_host = host.casefold()
    return (
        normalized_host == normalized_domain
        or normalized_host.endswith(f".{normalized_domain}")
        or normalized_domain.endswith(f".{normalized_host}")
    )


def _without_leading_www(host: str) -> str:
    normalized = host.casefold()
    if normalized.startswith("www."):
        return normalized[4:]
    return normalized


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None
