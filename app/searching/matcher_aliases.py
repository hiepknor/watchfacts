from __future__ import annotations

from collections.abc import Iterable

import unicodedata


def _normalize_alias_token(token: str) -> str:
    if not token:
        return ""

    normalized = unicodedata.normalize("NFKD", token).casefold()
    return "".join(
        char for char in normalized if not unicodedata.category(char).startswith("M")
    )


# Centralized descriptor alias map (shared by query parsing and listing matching).
_DESCRIPTOR_CANONICAL_GROUPS = {
    "choco": ("choco", "chocolate", "cho"),
    "mete": ("mete", "meteorite"),
    "gray": ("gray", "grey"),
}

_DESCRIPTOR_CANONICAL_MAP = {
    alias: canonical
    for canonical, aliases in _DESCRIPTOR_CANONICAL_GROUPS.items()
    for alias in aliases
}


def canonicalize_descriptor_token(token: str) -> str:
    normalized = _normalize_alias_token(token)
    if not normalized:
        return ""
    return _DESCRIPTOR_CANONICAL_MAP.get(normalized, normalized)


def canonicalize_descriptor_tokens(
    tokens: Iterable[str],
) -> tuple[str, ...]:
    return tuple(
        canonicalize_descriptor_token(token)
        for token in tokens
        if canonicalize_descriptor_token(token)
    )


def canonicalize_descriptor_tokens_as_set(
    tokens: Iterable[str],
) -> set[str]:
    return set(canonicalize_descriptor_tokens(tokens))
