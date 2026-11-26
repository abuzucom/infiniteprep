"""Focused coverage for safe paths, locator edges, mounts, and relocation bounds."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import IO, cast

import pytest

from infiniteprep.hashing import compute_sha256
from infiniteprep.services.library_import import filesystem, locator, relocation
from infiniteprep.services.library_import.filesystem import (
    FileIdentity,
    OpenedFile,
    file_identity,
    identity_still_matches,
    is_link_or_junction,
    link_identity,
    open_inside_root,
    real_children,
    resolve_inside_root,
)
from infiniteprep.services.library_import.locations import FormatId, Platform
from infiniteprep.services.library_import.locator import (
    find_installed_libraries,
    read_library_version,
)
from infiniteprep.services.library_import.relocation import (
    MissingReference,
    find_relocated_files,
)
from infiniteprep.services.usb.targets import TargetCandidate
from tests.unit.usb_preflight_support import (
    build_drive,
    build_inspection,
    build_partition,
)


class InvalidDescriptorHandle:
    """Expose a descriptor that the operating system cannot inspect."""

    def fileno(self) -> int:
        """Return a descriptor that cannot identify an open file."""
        return -1


def test_filesystem_helpers_refuse_missing_paths(tmp_path: Path) -> None:
    """Missing paths have no identity and no link identity."""
    missing = tmp_path / "missing"

    assert file_identity(missing) is None
    assert link_identity(missing) is None
    assert is_link_or_junction(missing) is False


def test_resolve_inside_root_accepts_the_root_and_a_missing_child(tmp_path: Path) -> None:
    """The root and a strict-free child path resolve inside the root."""
    root = tmp_path / "root"
    root.mkdir()

    assert resolve_inside_root(root, root) == root.resolve()
    assert resolve_inside_root(root / "missing.wav", root) == (root / "missing.wav").resolve()


def test_open_inside_root_reports_a_failed_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operating system open failure produces no opened file."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio bytes")

    def fail_open(*args: object) -> int:
        """Fail the operating system open call."""
        raise OSError("the file cannot be opened")

    monkeypatch.setattr(filesystem.os, "open", fail_open)

    assert open_inside_root(track, root) is None


def test_real_children_excludes_a_linked_folder(tmp_path: Path) -> None:
    """A linked child folder never enters the real child list."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")

    assert real_children(root) == []


def test_identity_still_matches_detects_a_replaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed directory entry identity fails the open file identity check."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio")
    opened = open_inside_root(track, root)
    assert opened is not None
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self: SimpleNamespace(st_dev=2, st_ino=2),
    )

    with opened:
        assert identity_still_matches(opened) is False


@pytest.mark.parametrize("root_kind", ["relative", "file"])
def test_relocation_rejects_an_invalid_root(tmp_path: Path, root_kind: str) -> None:
    """A root that cannot bound the search leaves every reference unresolved."""
    root = Path("relative-root")
    if root_kind == "file":
        root = tmp_path / "root.txt"
        root.write_text("content", encoding="utf-8")
    reference = MissingReference(path="old/track.wav", file_size=4, content_hash="abc")

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None
    assert results[0].confirmed is False


def test_relocation_stops_at_the_entry_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate after the entry bound never reaches the hash read."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "a-first.txt").write_text("first", encoding="utf-8")
    candidate = root / "z-last.txt"
    candidate.write_text("last", encoding="utf-8")
    reference = MissingReference(
        path="old/z-last.txt",
        file_size=4,
        content_hash=compute_sha256(candidate),
    )
    monkeypatch.setattr(relocation, "MAX_SEARCH_ENTRIES", 1)

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None


def test_relocation_stops_at_the_hash_byte_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate that exceeds the byte bound never confirms."""
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "track.wav"
    candidate.write_bytes(b"audio")
    reference = MissingReference(
        path="old/track.wav",
        file_size=5,
        content_hash=compute_sha256(candidate),
    )
    monkeypatch.setattr(relocation, "MAX_HASH_BYTES", 0)

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None


def test_relocation_digest_reports_a_failed_handle_read() -> None:
    """A handle read failure produces an empty digest."""

    class ReadFailure:
        """Fail every seek and read operation."""

        def seek(self, offset: int, whence: int = 0) -> int:
            """Fail the seek operation."""
            raise OSError("the handle cannot seek")

    handle = cast(IO[bytes], ReadFailure())

    assert relocation._digest_of_handle(handle) == ""


def test_relocation_opened_size_reports_a_failed_stat() -> None:
    """An invalid descriptor produces no opened file size."""
    handle = cast(IO[bytes], InvalidDescriptorHandle())
    opened = OpenedFile(
        path=Path("track.wav"),
        handle=handle,
        identity=FileIdentity(device=1, inode=1),
    )

    assert relocation._opened_size(opened) is None


def test_relocation_ignores_an_unreadable_folder() -> None:
    """An unreadable child folder contributes no files or folders."""

    class UnreadableFolder:
        """Fail the directory listing operation."""

        def iterdir(self):
            """Fail the directory listing operation."""
            raise OSError("the folder cannot be read")

    folder = cast(Path, UnreadableFolder())

    assert relocation._split_children(folder) == ([], [])


def test_library_version_header_supports_case_and_spacing(tmp_path: Path) -> None:
    """A version header accepts case changes and spacing around its value."""
    library = tmp_path / "collection.nml"
    library.write_bytes(b'<HEAD VERSION = "3.5.1"/>')

    assert read_library_version(library) == "3.5.1"


def test_library_version_header_rejects_a_long_or_late_value(tmp_path: Path) -> None:
    """A long value and a value after the header limit do not match."""
    long_library = tmp_path / "long.nml"
    long_library.write_bytes(b'version = "' + (b"x" * 33) + b'"')
    late_library = tmp_path / "late.nml"
    late_library.write_bytes(b" " * locator.HEADER_READ_BYTES + b'version = "3.5.1"')

    assert read_library_version(long_library) is None
    assert read_library_version(late_library) is None


def test_folder_version_accepts_only_a_numeric_suffix() -> None:
    """A folder version needs a final component that starts with a digit."""
    assert locator._folder_version(Path("Traktor 3.5.1")) == "3.5.1"
    assert locator._folder_version(Path("Traktor")) is None
    assert locator._folder_version(Path("Traktor release")) is None
    assert locator._folder_version(Path("Traktor 3")) == "3"


def test_locator_matches_a_mount_once_for_duplicate_mount_points(tmp_path: Path) -> None:
    """A device listed at the same mount point twice yields one library."""
    stick = tmp_path / "stick"
    library = stick / "PIONEER" / "rekordbox"
    library.mkdir(parents=True)
    (library / "export.pdb").write_bytes(b"device library")
    candidate = TargetCandidate(
        name="sdb",
        inspection=build_inspection(
            drive=build_drive(name="sdb"),
            partitions=(build_partition(mount_points=(str(stick), str(stick))),),
        ),
        error=None,
    )

    found = find_installed_libraries(tmp_path, Platform.WINDOWS, drives=(candidate,))
    usb = [item for item in found if item.format_id is FormatId.REKORDBOX_USB]

    assert len(usb) == 1
    assert usb[0].file_path == library / "export.pdb"


def test_locator_ignores_a_device_without_an_inspection(tmp_path: Path) -> None:
    """A failed device inspection contributes no mount matches."""
    candidate = TargetCandidate(name="failed", inspection=None, error="failed")

    found = find_installed_libraries(tmp_path, Platform.WINDOWS, drives=(candidate,))

    assert all(item.format_id is not FormatId.REKORDBOX_USB for item in found)


def test_locator_stops_at_the_scan_entry_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bounded walk returns no more than the configured entry count."""
    root = tmp_path / "root"
    root.mkdir()
    for index in range(3):
        (root / f"child{index}").mkdir()
    monkeypatch.setattr(locator, "MAX_SCAN_ENTRIES", 1)

    assert len(locator._walk_bounded(root)) == 1


def test_locator_reports_a_failed_candidate_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate whose final stat fails still appears with no modified time."""
    folder = tmp_path / "library"
    folder.mkdir()
    candidate = folder / "collection.nml"
    candidate.write_text("content", encoding="utf-8")
    location = locator.KnownLocation(
        format_id=FormatId.TRAKTOR,
        file_name="collection.nml",
        folders=(),
        citation_url="https://example.invalid/",
        citation_note="Note",
    )
    original_stat = Path.stat
    stat_count = 0

    def fail_second_stat(path: Path, *, follow_symlinks: bool = True):
        """Fail the stat call that reads the modified time."""
        nonlocal stat_count
        if path == candidate and follow_symlinks:
            stat_count += 1
            if stat_count > 1:
                raise OSError("the candidate cannot be read")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_second_stat)

    found = locator._library_in_folder(location, folder)

    assert len(found) == 1
    assert found[0].modified_at is None


def test_locator_reports_a_failed_profile_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile path that cannot resolve holds no candidates."""
    profile = tmp_path / "profile"
    profile.mkdir()

    def fail_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        """Fail the operating system path resolution."""
        raise OSError("the path cannot be resolved")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert locator._folder_inside_profile(profile / "Music", profile) is False
