"""Tests for the Windows console and superscript port device names in USB paths."""

from __future__ import annotations

import pytest

from infiniteprep.services.usb._shared import is_safe_relative_path

SUPERSCRIPT_ONE = chr(0x00B9)
SUPERSCRIPT_TWO = chr(0x00B2)
SUPERSCRIPT_THREE = chr(0x00B3)
DEVICE_VARIANT_PATHS = (
    f"crate/COM{SUPERSCRIPT_ONE}.aiff",
    f"crate/com{SUPERSCRIPT_TWO}",
    f"crate/LPT{SUPERSCRIPT_THREE}.wav",
    f"crate/lpt{SUPERSCRIPT_ONE}/track.aiff",
    "crate/CONIN$",
    "crate/conout$.txt",
    "CONIN$/track.aiff",
    "crate/COM0.wav",
    "crate/lpt0",
)
NAMES_LIKE_VARIANTS = (
    "crate/conin.aiff",
    "crate/CONOUT.wav",
    f"crate/COM{SUPERSCRIPT_ONE}0.aiff",
    "crate/LPT10.wav",
)


@pytest.mark.parametrize("relative_path", DEVICE_VARIANT_PATHS)
def test_windows_device_variant_is_not_a_safe_relative_path(relative_path: str) -> None:
    """A superscript port name or a console device name is refused in any component."""
    assert not is_safe_relative_path(relative_path)


@pytest.mark.parametrize("relative_path", NAMES_LIKE_VARIANTS)
def test_names_close_to_device_variants_stay_safe(relative_path: str) -> None:
    """Names that only resemble the device variants stay valid."""
    assert is_safe_relative_path(relative_path)
