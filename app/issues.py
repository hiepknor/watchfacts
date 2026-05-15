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

    if (
        raw_normalized
        and _raw_much_longer(normalized, raw_normalized)
        and not _has_price_evidence(listing_text)
    ):
        issues.append(SuspiciousIssue("raw_much_longer", 2))

    if raw_normalized and _missing_price_after_currency(normalized, raw_normalized):
        issues.append(SuspiciousIssue("missing_price_after_currency", 3))

    return _dedupe_issues(issues)


def _ends_with_currency(value: str) -> bool:
    tokens = value.split()
    if not tokens or tokens[-1].strip(":,.;/-") not in CURRENCY_TOKENS:
        return False
    if len(tokens) >= 2 and _looks_like_price_token(tokens[-2].strip(":,.;/-$")):
        return False
    return True


def _looks_like_price_token(value: str) -> bool:
    if value.isdigit() and len(value) == 4 and 1900 <= int(value) <= 2099:
        return False
    return bool(
        re.fullmatch(r"\d+(?:[,.]\d+)*(?:\.\d+)?(?:k|m)?", value)
        or re.fullmatch(r"\d+(?:\.\d+)?", value)
    )


def _ends_with_price_marker(value: str) -> bool:
    stripped = value.rstrip()
    if not stripped:
        return False
    last_token = stripped.split()[-1].strip(":,.;/-").casefold()
    if last_token in PRICE_MARKERS:
        return True
    if stripped[-1] in {"$", "💰", "💲"}:
        return not _has_price_before_trailing_marker(stripped)
    return False


def _has_price_before_trailing_marker(value: str) -> bool:
    return bool(re.search(r"\d+(?:[,.]\d+)*(?:\.\d+)?(?:k|m)?[$💰💲]$", value))


def _has_price_evidence(value: str) -> bool:
    normalized = value.casefold()
    amount = r"\d+(?:[,.]\d+)*(?:\.\d+)?(?:k|m|u)?"
    currency = r"(?:hk|hkd|usd|usdt|eur|aed|chf)"
    return bool(
        re.search(rf"\b{currency}\s*{amount}\b", normalized)
        or re.search(rf"\b{amount}\s*{currency}\b", normalized)
        or re.search(rf"[$💰💲]\s*{amount}\b", normalized)
        or re.search(rf"\b{amount}\s*[$💰💲]", normalized)
    )


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
