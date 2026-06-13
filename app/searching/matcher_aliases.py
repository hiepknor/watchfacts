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
    "rg": ("rg", "rosegold", "rose-gold"),
    "wg": ("wg", "whitegold", "white-gold"),
}
_COMPOUND_DESCRIPTOR_PHRASES = {
    "rg": (("rose", "gold"),),
    "wg": (("white", "gold"),),
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


def descriptor_exists_in_tokens(descriptor: str, tokens: Iterable[str]) -> bool:
    normalized_descriptor = canonicalize_descriptor_token(descriptor)
    if not normalized_descriptor:
        return False

    normalized_token_list: list[str] = []
    for token in tokens:
        normalized_token = canonicalize_descriptor_token(token)
        if normalized_token:
            normalized_token_list.append(normalized_token)
    normalized_tokens = tuple(normalized_token_list)
    if normalized_descriptor in normalized_tokens:
        return True

    for phrase in _COMPOUND_DESCRIPTOR_PHRASES.get(normalized_descriptor, ()):
        normalized_phrase = tuple(canonicalize_descriptor_token(token) for token in phrase)
        phrase_length = len(normalized_phrase)
        if not normalized_phrase:
            continue
        for index in range(len(normalized_tokens) - phrase_length + 1):
            if normalized_tokens[index : index + phrase_length] == normalized_phrase:
                return True
    return False
