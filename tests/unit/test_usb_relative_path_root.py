"""Tests that a relative USB path must name something below its root, not the root."""

from __future__ import annotations

import pytest

from infiniteprep.services.usb._shared import is_safe_relative_path

ROOT_ONLY_PATHS = (".", "./", ".\\", "./.", "././", ".//.")
CHILD_PATHS = ("./track.aiff", "crate/./track.aiff", ".hidden/track.aiff", "crate/")


@pytest.mark.parametrize("relative_path", ROOT_ONLY_PATHS)
def test_root_only_path_is_not_a_safe_relative_path(relative_path: str) -> None:
    """A path made only of "." components names the root itself and is refused."""
    assert not is_safe_relative_path(relative_path)


@pytest.mark.parametrize("relative_path", CHILD_PATHS)
def test_path_with_a_child_component_stays_safe(relative_path: str) -> None:
    """A path with at least one named component below the root stays valid."""
    assert is_safe_relative_path(relative_path)
