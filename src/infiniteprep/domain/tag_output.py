"""Format key and BPM text for on-disk tags."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    format_canonical_key,
    parse_key_string,
)

BPM_DECIMAL_PLACES: Final[tuple[int, ...]] = (0, 1, 2)
BPM_TEXT: Final[re.Pattern[str]] = re.compile(r"[0-9]+(?:\.[0-9]+)?")
PADDED_WHEEL_KEY_LENGTH: Final[int] = 3


@dataclass(frozen=True)
class TagOutputFormat:
    """Snapshot the preferences used to prepare a tag write."""

    key_notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT
    leading_zero: bool = False
    bpm_decimals: int = 0

    def format_tags(self, tags: dict[str, str]) -> dict[str, str]:
        """Return formatted tag text while preserving the approved values."""
        formatted_tags = tags.copy()
        key = parse_key_string(tags.get(FieldName.KEY))
        if key is not None:
            formatted = format_canonical_key(*key, self.key_notation)
            if self.leading_zero and self.key_notation != KeyDisplayNotation.STANDARD:
                formatted = formatted.zfill(PADDED_WHEEL_KEY_LENGTH)
            formatted_tags[FieldName.KEY] = formatted
        if FieldName.BPM in tags:
            formatted_tags[FieldName.BPM] = self._format_bpm(tags[FieldName.BPM])
        return formatted_tags

    def _format_bpm(self, raw_bpm: str) -> str:
        """Round exact decimal text with ties rounded up."""
        if not BPM_TEXT.fullmatch(raw_bpm.strip()):
            raise ValueError("Enter a nonnegative BPM with a period as the decimal separator.")
        try:
            precision = Decimal(1).scaleb(-self.bpm_decimals)
            result = Decimal(raw_bpm).quantize(precision, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError("BPM is outside the supported range. Enter a shorter value.") from exc
        return format(result, "f")
