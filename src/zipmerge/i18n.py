"""Translate application-owned messages; usable without importing Qt."""

from collections.abc import Callable

_lookup: Callable[[str], str] | None = None


def set_lookup(lookup: Callable[[str], str] | None) -> None:
    global _lookup
    _lookup = lookup


def tr(source: str, *values: object) -> str:
    text = (_lookup(source) or source) if _lookup else source
    return text.format(*values) if values else text
