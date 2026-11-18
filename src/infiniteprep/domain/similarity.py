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
    # The cut runs first, so the copies that strip and lower make stay bounded too.
    left_key = left[:MAX_SIMILARITY_CHARS].strip().lower()
    right_key = right[:MAX_SIMILARITY_CHARS].strip().lower()
    if left_key == right_key:
        return 1.0
    return round(SequenceMatcher(None, left_key, right_key).ratio(), SIMILARITY_PRECISION_DIGITS)
