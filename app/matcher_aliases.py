from __future__ import annotations

from collections.abc import Iterable

from app.matcher_normalization import normalize_text


_DESCRIPTOR_CANONICAL_GROUPS = {
    "choco": ("choco", "chocolate", "cho"),
    "gray": ("gray", "grey"),
}

_DESCRIPTOR_CANONICAL_MAP = {
    alias: canonical
    for canonical, aliases in _DESCRIPTOR_CANONICAL_GROUPS.items()
    for alias in aliases
}


def canonicalize_descriptor_token(token: str) -> str:
    normalized = normalize_text(token)
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
