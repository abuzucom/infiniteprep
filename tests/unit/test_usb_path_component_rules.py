"""Tests for the per-component rules of the USB path validators."""

from __future__ import annotations

import pytest

from infiniteprep.services.usb._shared import is_safe_relative_path, sanitize_mount_point

WINDOWS_DEVICE_PATHS = (
    "CON",
    "crate/nul.aiff",
    "crate/Aux.flac",
    "crate/com1.wav",
    "crate/LPT9.txt",
    "crate/PRN /track.aiff",
    "crate\\con\\track.aiff",
)
STREAM_PATHS = ("crate/track.aiff:hidden", "crate/x:y/track.aiff")
NAMES_LIKE_DEVICES = (
    "crate/console.aiff",
    "crate/nullify.wav",
    "crate/com10.wav",
    "crate/auxiliary/track.flac",
    "crate/track..final.aiff",
)


@pytest.mark.parametrize("relative_path", WINDOWS_DEVICE_PATHS)
def test_windows_device_name_is_unsafe(relative_path: str) -> None:
    """A device name in any component, with or without an extension, is refused."""
    assert not is_safe_relative_path(relative_path)


@pytest.mark.parametrize("relative_path", STREAM_PATHS)
def test_stream_colon_relative_path_unsafe(relative_path: str) -> None:
    """A colon inside a component selects a Windows data stream, so it is refused."""
    assert not is_safe_relative_path(relative_path)


@pytest.mark.parametrize("relative_path", NAMES_LIKE_DEVICES)
def test_device_lookalike_names_stay_safe(relative_path: str) -> None:
    """A longer name that starts like a device name, or holds two dots, stays valid."""
    assert is_safe_relative_path(relative_path)


@pytest.mark.parametrize("raw_mount", ["/media/USB..2024", "/media/archive..backup/stick"])
def test_mount_point_inner_dots_kept(raw_mount: str) -> None:
    """Two dots inside a name are no parent segment, so the mount point stays."""
    assert sanitize_mount_point(raw_mount) == raw_mount


@pytest.mark.parametrize("raw_mount", ["/media/..", "/media/../etc", "/media/stick/.."])
def test_mount_point_parent_segment_rejected(raw_mount: str) -> None:
    """A parent segment anywhere in the mount point yields None."""
    assert sanitize_mount_point(raw_mount) is None
