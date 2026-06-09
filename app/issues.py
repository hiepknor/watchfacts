from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SuspiciousIssue:
    reason: str
    severity: int


_PRICE_CURRENCY_SEP_RE = r"\s*[:;.,-]?\s*"
CURRENCY_TOKENS = {"hkd", "usd", "usdt", "eur", "aed", "chf"}
PRICE_ALIAS_CURRENCIES = {"euro": "eur", "hkdl": "hkd"}
PRICE_MARKERS = {"price", "$", "💰", "💲"}
_PRICE_AMOUNT_RE = re.compile(
    r"\d+(?:[.,]\d+)*(?:\s*(?:k|m|u|mil|million))?",
    re.IGNORECASE,
)
KARAT_GOLD_RE = re.compile(
    r"\b(?:9|10|14|18|19|20|21|22|24)k\s+(?:(?:rose|yellow|white|pink)\s+)?gold\b",
    re.IGNORECASE,
)


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

    if not issues and _missing_price_evidence(normalized):
        issues.append(SuspiciousIssue("missing_price_evidence", 1))

    return _dedupe_issues(issues)


def _ends_with_currency(value: str) -> bool:
    if _has_price_evidence(value):
        return False
    tokens = value.split()
    if not tokens:
        return False

    trailing_currency = _normalize_currency_token(tokens[-1])
    if trailing_currency not in CURRENCY_TOKENS:
        return False

    if len(tokens) >= 2 and _normalize_currency_token(tokens[-2]) in CURRENCY_TOKENS:
        if len(tokens) == 2:
            return False
        if not _looks_like_price_token(tokens[-3].strip(":,.;/-")):
            return False

    if len(tokens) >= 2 and _looks_like_price_token(tokens[-2].strip(":,.;/-$")):
        return False
    if len(tokens) >= 3:
        suffix = _normalize_currency_token(tokens[-2])
        amount_prefix = tokens[-3].strip(":,.;/-")
        if suffix in {"k", "m", "mil", "million"} and _looks_like_price_token(amount_prefix):
            return False
    return True


def _normalize_currency_token(value: str) -> str:
    return value.strip(":,.;/-").casefold()


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
    if _has_price_evidence(stripped):
        return False
    last_token = stripped.split()[-1].strip(":,.;/-").casefold()
    if last_token in PRICE_MARKERS:
        return True
    if stripped[-1] in {"$", "💰", "💲"}:
        return not _has_price_before_trailing_marker(stripped)
    return False


def _has_price_before_trailing_marker(value: str) -> bool:
    return bool(
        re.search(r"\d+(?:[,.]\d+)*(?:\.\d+)?(?:k|m)?(?:hk|hkd|us|usd|usdt)?[$💰💲]$", value)
    )


def _has_price_evidence(value: str) -> bool:
    if _extract_currency_prices(value):
        return True
    normalized = _normalize_price_currencies(value)
    normalized = KARAT_GOLD_RE.sub(" ", normalized)
    amount = r"\d+(?:[,.]\d+)*(?:\.\d+)?(?:k|m|u)?"
    currency = r"(?:hk|hkd|us|usd|usdt|eur|aed|chf)"
    money_symbols = r"$€£¥💰💲"
    return bool(
        re.search(rf"\b{currency}{_PRICE_CURRENCY_SEP_RE}{amount}(?=\b|\\W)", normalized)
        or re.search(rf"\b{amount}{_PRICE_CURRENCY_SEP_RE}{currency}(?=\b|\\W)", normalized)
        or re.search(rf"\b{currency}\s*[-~]\s*{amount}\b", normalized)
        or re.search(rf"\b{amount}\s*{currency}\s*~\s*{amount}\s*{currency}\b", normalized)
        or re.search(rf"\b[a-z]?\d+[-/]{amount}\s*{currency}\b", normalized)
        or re.search(rf"[{money_symbols}]\s*{amount}", normalized)
        or re.search(rf"[{money_symbols}]\s*{amount}\s*{currency}", normalized)
        or re.search(rf"\b{amount}\s*[{money_symbols}]", normalized)
        or re.search(rf"\b{amount}\s*{currency}\s*[{money_symbols}]", normalized)
        or re.search(rf"\b({_PRICE_AMOUNT_RE.pattern})\s*\+\s*(?:lnl|lab|label|lbl)\b", normalized)
        or re.search(rf"\b(?:lnl|lab|label|lbl)\s*\+\s*({_PRICE_AMOUNT_RE.pattern})\b", normalized)
        or re.search(r"\b(?:19|20)\d{2}\s+\d{5,8}\b", normalized)
        or re.search(r"\b(?:full\s+set|fullset|used|new|naked)\s+\d{5,8}\b", normalized)
        or re.search(r"\b\d{5,8}\s*(?:lab|label|lbl|ship|shipping)\b", normalized)
        or re.search(r"\b\d{1,3}(?:\.\d{1,3})?\s*\+\s*(?:lnl|lab|label|lbl)\b", normalized)
        or re.search(r"\b\d{1,3},\d{3}(?:\+\s*)?(?:lab|label|lbl|ship|shipping)?\b", normalized)
        or re.search(r"\b\d{3,4}\s*nfc\b", normalized)
        or re.search(r"\b\d+(?:\.\d+)?[km]\b", normalized)
        or re.search(r"\b\d{2,3},\d{1,2}\b", normalized)
    )


def _raw_much_longer(listing_text: str, raw_text: str) -> bool:
    if not listing_text or listing_text == raw_text:
        return False
    return len(raw_text) >= max(len(listing_text) + 40, int(len(listing_text) * 1.8))


def _missing_price_after_currency(listing_text: str, raw_text: str) -> bool:
    if _has_price_evidence(listing_text):
        return False
    raw_prices = _extract_currency_prices(raw_text)
    if not raw_prices:
        return False
    normalized_listing = _compact_price_text(listing_text.casefold())
    return any(price.casefold() not in normalized_listing for price in raw_prices)


def _extract_currency_prices(raw_text: str) -> set[str]:
    normalized = _normalize_price_currencies(raw_text)
    prices: set[str] = set()
    currency_terms = "|".join(sorted(CURRENCY_TOKENS))

    for currency, amount in re.findall(
        rf"\b({currency_terms})\b{_PRICE_CURRENCY_SEP_RE}({_PRICE_AMOUNT_RE.pattern})",
        normalized,
        flags=re.IGNORECASE,
    ):
        if not _is_significant_currency_amount(amount):
            continue
        compact_amount = _compact_price_text(amount)
        compact_currency = currency.casefold()
        prices.add(f"{compact_currency}{compact_amount}")
        prices.add(f"{compact_amount}{compact_currency}")

    for amount, currency in re.findall(
        rf"({_PRICE_AMOUNT_RE.pattern}){_PRICE_CURRENCY_SEP_RE}\b({currency_terms})\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        if not _is_significant_currency_amount(amount):
            continue
        compact_amount = _compact_price_text(amount)
        compact_currency = currency.casefold()
        prices.add(f"{compact_currency}{compact_amount}")
        prices.add(f"{compact_amount}{compact_currency}")

    for currency_symbol, amount in re.findall(
        r"((?:hk\\$|us\\$)|[€£💰💲$])\s*(" + _PRICE_AMOUNT_RE.pattern + r")",
        normalized,
    ):
        if not _is_significant_currency_amount(amount):
            continue
        compact_amount = _compact_price_text(amount)
        symbol = currency_symbol
        prices.add(compact_amount)
        prices.add(_compact_price_text(f"{symbol}{compact_amount}"))
        prices.add(_compact_price_text(f"{compact_amount}{symbol}"))

    for amount, currency_symbol in re.findall(
        rf"({_PRICE_AMOUNT_RE.pattern})([€£💰💲$])",
        normalized,
    ):
        if not _is_significant_currency_amount(amount):
            continue
        compact_amount = _compact_price_text(amount)
        symbol = currency_symbol
        prices.add(compact_amount)
        prices.add(_compact_price_text(f"{symbol}{compact_amount}"))
        prices.add(_compact_price_text(f"{compact_amount}{symbol}"))

    return prices


def _is_significant_currency_amount(amount: str) -> bool:
    compact = _compact_price_text(amount)
    digits = re.sub(r"\D", "", compact)
    if not digits:
        return False
    if compact.endswith(("k", "m", "u", "mil", "million")):
        return True
    return len(digits) >= 5


def _compact_price_text(value: str) -> str:
    return re.sub(r"[\s,]", "", value.casefold())


def _normalize_price_currencies(value: str) -> str:
    normalized = _price_scan_text(value)
    for alias, canonical in PRICE_ALIAS_CURRENCIES.items():
        normalized = re.sub(
            rf"\b{alias}\b",
            canonical,
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def _price_scan_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        char for char in normalized if not unicodedata.category(char).startswith("M")
    )
    normalized = re.sub(r"\b(hkd|usd|usdt|eur|aed|chf)\1\b", r"\1", normalized)
    return normalized


def _missing_price_evidence(listing_text: str) -> bool:
    if _has_price_evidence(listing_text):
        return False
    if _looks_like_non_sale_request(listing_text):
        return False
    return bool(re.search(r"\b(?=[a-z0-9/.-]*\d)[a-z0-9]+(?:[./-][a-z0-9]+)*\b", listing_text))


def _looks_like_non_sale_request(value: str) -> bool:
    tokens = value.split()
    if not tokens:
        return False
    head = " ".join(tokens[:4])
    return bool(
        tokens[0] in {"lookingfor", "wtb"}
        or head.startswith("looking for")
        or head.startswith("want to buy")
    )


def _dedupe_issues(issues: list[SuspiciousIssue]) -> list[SuspiciousIssue]:
    by_reason: dict[str, SuspiciousIssue] = {}
    for issue in issues:
        existing = by_reason.get(issue.reason)
        if existing is None or issue.severity > existing.severity:
            by_reason[issue.reason] = issue
    return list(by_reason.values())
