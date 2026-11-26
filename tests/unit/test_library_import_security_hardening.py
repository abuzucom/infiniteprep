"""Behavioral tests for library import security hardening (IMPORT-1, #143)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from infiniteprep.hashing import compute_sha256
from infiniteprep.services.library_import.locations import FormatId, Platform, locations_for
from infiniteprep.services.library_import.locator import _mount_matches
from infiniteprep.services.library_import.path_mapping import (
    PathMapping,
    apply_mapping,
    reverse_mapping,
)
from infiniteprep.services.library_import.relocation import _HashCache


def _make_symlink(link_path: Path, target_path: Path, *, is_dir: bool) -> None:
    """Create a symlink or skip the test when the OS refuses symlink creation."""
    try:
        link_path.symlink_to(target_path, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create symbolic links.")


def test_mount_matches_rejects_linked_pioneer(tmp_path: Path) -> None:
    """A mounted device with a linked PIONEER folder must not escape the mount point."""
    mount_point = tmp_path / "usb_drive"
    mount_point.mkdir()
    outside = tmp_path / "outside_host"
    outside_rb = outside / "rekordbox"
    outside_rb.mkdir(parents=True)
    (outside_rb / "export.pdb").write_bytes(b"pdb-header")

    _make_symlink(mount_point / "PIONEER", outside, is_dir=True)
    device_locations = tuple(
        loc for loc in locations_for(Platform.ANY) if loc.format_id == FormatId.REKORDBOX_USB
    )

    matches = _mount_matches(device_locations, mount_point)

    assert matches == []


def test_mount_matches_allows_linked_mount(tmp_path: Path) -> None:
    """An OS-managed symlink mount point still discovers real files inside the device."""
    real_device = tmp_path / "real_device"
    rb_folder = real_device / "PIONEER" / "rekordbox"
    rb_folder.mkdir(parents=True)
    db_file = rb_folder / "export.pdb"
    db_file.write_bytes(b"pdb-header")

    mount_link = tmp_path / "mounted_usb"
    _make_symlink(mount_link, real_device, is_dir=True)
    device_locations = tuple(
        loc for loc in locations_for(Platform.ANY) if loc.format_id == FormatId.REKORDBOX_USB
    )

    matches = _mount_matches(device_locations, mount_link)

    assert len(matches) == 1
    assert matches[0].file_path == mount_link / "PIONEER" / "rekordbox" / "export.pdb"


def test_relocation_rehashes_modified_file(tmp_path: Path) -> None:
    """A file modified in place with the same inode must not return a stale digest."""
    root = tmp_path / "music"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"first-version")
    first_expected = compute_sha256(track)

    cache = _HashCache(root)
    assert cache.digest_of(track) == first_expected

    # Overwrite in place so the file keeps its device and inode.
    with track.open("r+b") as handle:
        handle.write(b"second-bytes!")
        handle.truncate()
    stat_info = track.stat()
    os.utime(track, ns=(stat_info.st_atime_ns, stat_info.st_mtime_ns + 5_000_000_000))
    second_expected = compute_sha256(track)

    assert second_expected != first_expected
    assert cache.digest_of(track) == second_expected


def test_mapping_handles_file_url_slashes() -> None:
    """A file URL prefix with a trailing slash or repeated slashes maps cleanly."""
    source_path = "file:///Users/user/My%20Music/Sub%20Folder/Track.mp3"
    trailing_mapping = PathMapping(
        source_prefix="file:///Users/user/My Music/",
        target_prefix="/srv/music",
    )
    doubled_mapping = PathMapping(
        source_prefix="file:///Users//user/My Music",
        target_prefix="/srv/music",
    )

    assert apply_mapping(source_path, trailing_mapping) == "/srv/music/Sub Folder/Track.mp3"
    assert apply_mapping(source_path, doubled_mapping) == "/srv/music/Sub Folder/Track.mp3"


def test_reverse_mapping_handles_file_url() -> None:
    """Reverse mapping with a file URL target prefix preserves the exact remainder."""
    mapping = PathMapping(
        source_prefix="D:\\Music",
        target_prefix="file:///Users/user/My Music/",
    )

    mapped = reverse_mapping("file:///Users/user/My%20Music/Club/Track.mp3", mapping)

    assert mapped == "D:/Music/Club/Track.mp3"
