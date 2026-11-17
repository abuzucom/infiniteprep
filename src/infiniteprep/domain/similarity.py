"""Text similarity used to rank catalog candidates against a query (D394)."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Final

SIMILARITY_PRECISION_DIGITS: Final[int] = 4
# SequenceMatcher costs up to the product of both lengths. Artist and title text past this
# length adds no ranking signal, and a crafted tag can hold megabytes.
MAX_SIMILARITY_CHARS: Final[int] = 512


def string_similarity(left: str | None, right: str | None) -> float:
    """Compute normalized case-insensitive similarity in [0.0, 1.0].

    Only the first MAX_SIMILARITY_CHARS characters of each text take part.
    """
    if not left or not right:
        return 0.0
    a = left.strip().lower()[:MAX_SIMILARITY_CHARS]
    b = right.strip().lower()[:MAX_SIMILARITY_CHARS]
    if a == b:
        return 1.0
    return round(SequenceMatcher(None, a, b).ratio(), SIMILARITY_PRECISION_DIGITS)
