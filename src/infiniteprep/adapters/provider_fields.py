"""Type and length checks for fields read from catalog and fingerprint API responses.

A remote response is untrusted. A field can hold the wrong type, a control character, or
text up to the whole response cap. Every value that reaches a candidate passes these checks.
"""

from __future__ import annotations

import math
from typing import Any, Final

# Matches the cap of the iTunes adapter. Real artist, title, and label text stays far below it.
MAX_PROVIDER_TEXT_CHARS: Final[int] = 500
# A search page holds at most a few dozen results. A larger list adds no candidate worth a look.
MAX_PROVIDER_LIST_ITEMS: Final[int] = 100
# The cleaning step drops control characters, so it scans more input than it keeps. Twice
# the output cap still fills the cap when up to half of the scanned characters drop out,
# and it bounds the work on a very long field.
CLEAN_SCAN_FACTOR: Final[int] = 2


def provider_text(value: object) -> str | None:
    """Return value as trimmed printable text within the length cap, or None.

    A string or an integer counts as text. A boolean, a float, a container, and an empty
    result return None. Line breaks, tabs, and other control characters drop out.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    head = value[: MAX_PROVIDER_TEXT_CHARS * CLEAN_SCAN_FACTOR]
    cleaned = "".join(character for character in head if character.isprintable())
    return cleaned.strip()[:MAX_PROVIDER_TEXT_CHARS] or None


def provider_number(value: object) -> float | None:
    """Return value as a finite non-negative float, or None for any other value."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        # JSON integers have no size limit. One past the float range reads as absent.
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def provider_dict(value: object) -> dict[str, Any]:
    """Return value when it is a dict, or an empty dict."""
    return value if isinstance(value, dict) else {}


def provider_dicts(value: object) -> list[dict[str, Any]]:
    """Return the dict items of a list, at most MAX_PROVIDER_LIST_ITEMS of them."""
    if not isinstance(value, list):
        return []
    return [item for item in value[:MAX_PROVIDER_LIST_ITEMS] if isinstance(item, dict)]


def provider_texts(value: object) -> list[str]:
    """Return the valid text items of a list, at most MAX_PROVIDER_LIST_ITEMS of them."""
    if not isinstance(value, list):
        return []
    texts = (provider_text(item) for item in value[:MAX_PROVIDER_LIST_ITEMS])
    return [text for text in texts if text]


def first_provider_text(value: object) -> str | None:
    """Return the first valid text item of a list, or None."""
    texts = provider_texts(value)
    return texts[0] if texts else None


def require_payload_dict(payload: object, provider: str) -> dict[str, Any]:
    """Return payload when it is a JSON object. Raise ValueError otherwise.

    The adapters call this before they cache a response, so a malformed body never
    reaches the shared cache.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"{provider} returned a response that is not a JSON object; retry the lookup later."
        )
    return payload
