"""Behavioral tests for the step that finds the installed DJ libraries (D333)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from infiniteprep.services.library_import.locations import (
    KNOWN_LOCATIONS,
    FormatId,
    KnownLocation,
    Platform,
    folders_for,
)
from infiniteprep.services.library_import.locator import (
    MAX_SCAN_DEPTH,
    find_installed_libraries,
)
from infiniteprep.services.usb.targets import TargetCandidate
from tests.unit.usb_preflight_support import (
    build_drive,
    build_inspection,
    build_partition,
)


def _location_for(format_id: FormatId) -> KnownLocation:
    """Return the known location of one format."""
    for location in KNOWN_LOCATIONS:
        if location.format_id is format_id:
            return location
    raise AssertionError(f"No known location for {format_id}.")


COLLECTION_NML = '<HEAD PROGRAM="Traktor" VERSION="3.5.1"/>'

# Windows ignores POSIX mode bits, and a symbolic link there needs a privilege.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX links only.")


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    """Return a fake user profile folder."""
    folder = tmp_path / "profile"
    folder.mkdir()
    return folder


def locate(profile: Path) -> list:
    """List the libraries of the profile without probing the real drives."""
    return find_installed_libraries(profile, Platform.WINDOWS, drives=())


def write_traktor_root(profile: Path, version: str = "Traktor 3.5.1") -> Path:
    """Create a Traktor Root Directory holding a collection file."""
    root = profile / "Documents" / "Native Instruments" / version
    root.mkdir(parents=True)
    (root / "collection.nml").write_text(COLLECTION_NML, encoding="utf-8")
    return root


def test_locator_finds_a_registered_location(profile: Path) -> None:
    """A library in a known folder is listed with its path and its format."""
    root = write_traktor_root(profile)

    found = locate(profile)

    assert len(found) == 1
    assert found[0].format_id is FormatId.TRAKTOR
    assert found[0].file_path == root / "collection.nml"
    assert found[0].folder_path == root


def test_locator_reads_the_version_from_the_traktor_folder_name(profile: Path) -> None:
    """The Traktor version sits in the folder name, so the header is not needed."""
    write_traktor_root(profile, version="Traktor 3.2.1")

    found = locate(profile)

    assert found[0].version == "3.2.1"


def test_locator_finds_several_libraries(profile: Path) -> None:
    """A Traktor root and a Serato folder both appear in the list."""
    write_traktor_root(profile)
    serato = profile / "Music" / "_Serato_"
    serato.mkdir(parents=True)
    (serato / "database V2").write_bytes(b"database")

    found = locate(profile)

    formats = {item.format_id for item in found}
    assert formats == {FormatId.TRAKTOR, FormatId.SERATO}


@posix_only
def test_locator_skips_a_symlink_that_leaves_the_profile(profile: Path, tmp_path: Path) -> None:
    """A folder that links out of the profile is not followed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "collection.nml").write_text(COLLECTION_NML, encoding="utf-8")
    parent = profile / "Documents" / "Native Instruments"
    parent.mkdir(parents=True)
    link = parent / "Traktor 3.5.1"
    link.symlink_to(outside, target_is_directory=True)

    found = locate(profile)

    assert found == []


def test_locator_stops_at_the_depth_bound(profile: Path) -> None:
    """A library below the depth bound of the scan stays out of the list."""
    deep = profile / "Documents" / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "collection.nml").write_text(COLLECTION_NML, encoding="utf-8")

    found = locate(profile)

    assert all(item.file_path != deep / "collection.nml" for item in found)
    assert MAX_SCAN_DEPTH <= 4


def test_locator_reads_no_file_before_the_dj_picks(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The locator opens no file, so nothing changes before the choice."""
    root = write_traktor_root(profile)
    collection = root / "collection.nml"
    before = collection.stat().st_mtime_ns
    opened: list[Path] = []

    def recording_open(self: Path, *args: object, **kwargs: object):
        """Record the path that was opened, then open it as usual."""
        opened.append(self)
        return original_open(self, *args, **kwargs)

    original_open = Path.open
    monkeypatch.setattr(Path, "open", recording_open)
    locate(profile)

    assert collection.stat().st_mtime_ns == before
    assert opened == []


def test_locator_reads_no_file_of_a_format_without_a_folder_version(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A format with no version in its folder name is not opened either."""
    serato = profile / "Music" / "_Serato_"
    serato.mkdir(parents=True)
    (serato / "database V2").write_bytes(b"database bytes")
    opened: list[Path] = []

    def recording_open(self: Path, *args: object, **kwargs: object):
        """Record the path that was opened, then open it as usual."""
        opened.append(self)
        return original_open(self, *args, **kwargs)

    original_open = Path.open
    monkeypatch.setattr(Path, "open", recording_open)
    found = locate(profile)

    assert any(item.format_id is FormatId.SERATO for item in found)
    assert opened == []


def test_the_locator_finds_a_rekordbox_export_on_a_mounted_drive(tmp_path: Path) -> None:
    """A stick that holds a rekordbox export offers the database files it holds."""
    stick = tmp_path / "stick"
    library = stick / "PIONEER" / "rekordbox"
    library.mkdir(parents=True)
    (library / "export.pdb").write_bytes(b"device library")
    (library / "exportLibrary.db").write_bytes(b"one library")
    drives = (
        TargetCandidate(
            name="sdb",
            inspection=build_inspection(
                drive=build_drive(name="sdb"),
                partitions=(build_partition(mount_points=(str(stick),)),),
            ),
            error=None,
        ),
    )

    found = find_installed_libraries(tmp_path, Platform.WINDOWS, drives=drives)

    usb = [item for item in found if item.format_id is FormatId.REKORDBOX_USB]
    assert {item.file_path.name for item in usb} == {"export.pdb", "exportLibrary.db"}
    assert all(item.file_path.parent == library for item in usb)


def test_the_locator_finds_a_rekordbox_export_on_an_hfs_drive(tmp_path: Path) -> None:
    """A stick formatted with HFS names the hidden folder with a leading dot."""
    stick = tmp_path / "stick"
    library = stick / ".PIONEER" / "rekordbox"
    library.mkdir(parents=True)
    (library / "export.pdb").write_bytes(b"device library")
    drives = (
        TargetCandidate(
            name="sdb",
            inspection=build_inspection(
                drive=build_drive(name="sdb"),
                partitions=(build_partition(mount_points=(str(stick),)),),
            ),
            error=None,
        ),
    )

    found = find_installed_libraries(tmp_path, Platform.MACOS, drives=drives)

    usb = [item for item in found if item.format_id is FormatId.REKORDBOX_USB]
    assert [item.file_path.name for item in usb] == ["export.pdb"]


def test_the_locator_ignores_a_usb_folder_in_the_profile_folder(profile: Path) -> None:
    """A device location never holds a candidate in the user profile folder."""
    library = profile / "PIONEER" / "rekordbox"
    library.mkdir(parents=True)
    (library / "export.pdb").write_bytes(b"device library")

    found = locate(profile)

    assert all(item.format_id is not FormatId.REKORDBOX_USB for item in found)


def test_the_locator_skips_a_scan_root_that_is_a_link(profile: Path, tmp_path: Path) -> None:
    """A scan root that points outside the profile holds no candidate."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "collection.nml").write_text(COLLECTION_NML, encoding="utf-8")
    documents = profile / "Documents"
    documents.mkdir()
    try:
        documents.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")

    found = locate(profile)

    assert all(item.file_path != outside / "collection.nml" for item in found)


def test_the_locator_skips_a_known_folder_that_is_a_link(profile: Path, tmp_path: Path) -> None:
    """A known folder that points outside the profile holds no candidate."""
    outside = tmp_path / "outside"
    native = outside / "Native Instruments"
    native.mkdir(parents=True)
    (native / "collection.nml").write_text(COLLECTION_NML, encoding="utf-8")
    documents = profile / "Documents"
    documents.mkdir()
    try:
        documents.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")

    found = locate(profile)

    assert all(item.file_path != native / "collection.nml" for item in found)


def test_the_known_folders_of_a_location_belong_to_one_platform() -> None:
    """A folder declared for another platform never holds a candidate."""
    mixxx = _location_for(FormatId.MIXXX)

    windows = folders_for(mixxx, Platform.WINDOWS)
    macos = folders_for(mixxx, Platform.MACOS)
    linux = folders_for(mixxx, Platform.LINUX)

    assert windows == ("AppData/Local/Mixxx",)
    assert "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx" in macos
    assert ".mixxx" in linux
    assert not set(windows) & set(macos)
    assert not set(windows) & set(linux)


def test_locator_marks_the_observed_rekordbox_folder(profile: Path) -> None:
    """A location with no vendor page is marked observed."""
    folder = profile / "AppData" / "Roaming" / "rekordbox" / "rekordbox"
    folder.mkdir(parents=True)
    (folder / "rekordbox.xml").write_text("<DJ_PLAYLISTS/>", encoding="utf-8")

    found = locate(profile)

    observed = [item for item in found if item.observed]
    assert len(observed) == 1
    assert observed[0].format_id is FormatId.REKORDBOX
    assert observed[0].citation_url.startswith("https://")
