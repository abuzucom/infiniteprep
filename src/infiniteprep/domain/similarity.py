"""Text similarity used to rank catalog candidates against a query (D394)."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Final

SIMILARITY_PRECISION_DIGITS: Final[int] = 4


def string_similarity(left: str | None, right: str | None) -> float:
    """Compute normalized case-insensitive similarity in [0.0, 1.0]."""
    if not left or not right:
        return 0.0
    a = left.strip().lower()
    b = right.strip().lower()
    if a == b:
        return 1.0
    return round(SequenceMatcher(None, a, b).ratio(), SIMILARITY_PRECISION_DIGITS)
