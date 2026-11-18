"""Tests that USB path parts Windows trims to ".." or "." are refused."""

from __future__ import annotations

import pytest

from infiniteprep.services.usb._shared import is_safe_absolute_path, is_safe_relative_path

TRIMMED_PARENT_PATHS = (
    ".. ",
    "crate/.. /outside.aiff",
    "crate\\..  \\outside.aiff",
    "crate/.../outside.aiff",
    "crate/... /outside.aiff",
    ". ",
    "crate/ . /track.aiff",
    "crate/  /track.aiff",
)
NAMES_WITH_DOTS_OR_SPACES = (
    "./track.aiff",
    "crate/.hidden/track.aiff",
    "crate/track..final.aiff",
    "crate/my track .aiff",
    "crate/... track.aiff",
)


@pytest.mark.parametrize("relative_path", TRIMMED_PARENT_PATHS)
def test_dot_space_part_is_not_a_safe_relative_path(relative_path: str) -> None:
    """A part made only of dots and spaces, other than ".", is refused."""
    assert not is_safe_relative_path(relative_path)


@pytest.mark.parametrize("relative_path", NAMES_WITH_DOTS_OR_SPACES)
def test_names_holding_dots_or_spaces_stay_safe(relative_path: str) -> None:
    """A name that holds other characters besides dots and spaces stays valid."""
    assert is_safe_relative_path(relative_path)


@pytest.mark.parametrize("mount_point", ["/media/.. /etc", "D:\\USB\\.. \\Windows"])
def test_dot_space_part_is_not_a_safe_absolute_path(mount_point: str) -> None:
    """The journal mount point check refuses the same trimmed parent parts."""
    assert not is_safe_absolute_path(mount_point)
