from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SuspiciousIssue:
    reason: str
    severity: int


CURRENCY_TOKENS = {"hkd", "usd", "usdt", "eur", "aed", "chf"}
PRICE_MARKERS = {"price", "$", "💰", "💲"}


def detect_suspicious_result(
    *,
    listing_text: str,
    raw_listing_text: str | None = None,
) -> list[SuspiciousIssue]:
    issues: list[SuspiciousIssue] = []
    normalized = " ".join(listing_text.casefold().split())
    raw_normalized = " ".join((raw_listing_text or "").casefold().split())

    if _ends_with_currency(normalized):
        issues.append(SuspiciousIssue("ends_with_currency", 3))

    if _ends_with_price_marker(normalized):
        issues.append(SuspiciousIssue("ends_with_price_marker", 3))

    if raw_normalized and _raw_much_longer(normalized, raw_normalized):
        issues.append(SuspiciousIssue("raw_much_longer", 2))

    if raw_normalized and _missing_price_after_currency(normalized, raw_normalized):
        issues.append(SuspiciousIssue("missing_price_after_currency", 3))

    return _dedupe_issues(issues)


def _ends_with_currency(value: str) -> bool:
    tokens = value.split()
    return bool(tokens and tokens[-1].strip(":,.;/-") in CURRENCY_TOKENS)


def _ends_with_price_marker(value: str) -> bool:
    stripped = value.rstrip()
    if not stripped:
        return False
    last_token = stripped.split()[-1].strip(":,.;/-").casefold()
    return last_token in PRICE_MARKERS or stripped[-1] in {"$", "💰", "💲"}


def _raw_much_longer(listing_text: str, raw_text: str) -> bool:
    if not listing_text or listing_text == raw_text:
        return False
    return len(raw_text) >= max(len(listing_text) + 40, int(len(listing_text) * 1.8))


def _missing_price_after_currency(listing_text: str, raw_text: str) -> bool:
    raw_prices = {
        f"{currency} {amount}"
        for currency, amount in re.findall(
            r"\b(hkd|usd|usdt|eur|aed|chf)\b\s+(\d{5,8})\b",
            raw_text,
            flags=re.IGNORECASE,
        )
    }
    if not raw_prices:
        return False
    normalized_listing = listing_text.casefold()
    return any(price.casefold() not in normalized_listing for price in raw_prices)


def _dedupe_issues(issues: list[SuspiciousIssue]) -> list[SuspiciousIssue]:
    by_reason: dict[str, SuspiciousIssue] = {}
    for issue in issues:
        existing = by_reason.get(issue.reason)
        if existing is None or issue.severity > existing.severity:
            by_reason[issue.reason] = issue
    return list(by_reason.values())
