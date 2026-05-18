from __future__ import annotations

import re
import unicodedata


TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*", re.IGNORECASE)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        char for char in normalized if not unicodedata.category(char).startswith("M")
    )
    tokens = TOKEN_RE.findall(normalized)
    return " ".join(tokens)


def tokenize_query(query: str) -> list[str]:
    return normalize_text(query).split()


def compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
