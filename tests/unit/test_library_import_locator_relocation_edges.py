"""Focused coverage for locator confinement and drive failure paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.services.library_import import locator
from infiniteprep.services.library_import.locations import (
    FormatId,
    KnownLocation,
    Platform,
    locations_for,
)
from infiniteprep.services.library_import.locator import read_library_version
from infiniteprep.services.usb.targets import TargetCandidate


def make_location(
    *,
    folders: tuple[tuple[Platform, tuple[str, ...]], ...] = (),
    on_device: bool = False,
) -> KnownLocation:
    """Return a focused known library location."""
    return KnownLocation(
        format_id=FormatId.TRAKTOR,
        file_name="collection.nml",
        folders=folders,
        citation_url="https://example.invalid/",
        citation_note="Test location",
        on_device=on_device,
    )


def test_known_folder_confinement_refuses_a_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known profile folder that is a junction holds no candidate."""
    profile = tmp_path / "profile"
    folder = profile / "Music"
    folder.mkdir(parents=True)
    location = make_location(folders=((Platform.WINDOWS, ("Music",)),))
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == folder,
        raising=False,
    )

    found = locator._known_folder_matches(location, profile, Platform.WINDOWS)

    assert found == []


def test_known_folder_confinement_refuses_an_outside_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known profile folder that resolves outside the profile holds no candidate."""
    profile = tmp_path / "profile"
    folder = profile / "Music"
    outside = tmp_path / "outside"
    folder.mkdir(parents=True)
    outside.mkdir()
    original_resolve = Path.resolve

    def resolve_outside(path: Path, *args: object, **kwargs: object) -> Path:
        """Resolve the known folder outside the profile."""
        if path == folder:
            return outside.resolve()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside)

    assert locator._folder_inside_profile(folder, profile) is False


def test_candidate_confinement_refuses_a_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate folder that is a junction stays outside the profile."""
    profile = tmp_path / "profile"
    folder = profile / "Music"
    candidate = folder / "collection.nml"
    folder.mkdir(parents=True)
    candidate.write_text("content", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == folder,
        raising=False,
    )

    assert locator._candidate_inside_profile(folder, candidate, profile) is False


def test_candidate_confinement_survives_a_resolution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate that cannot resolve stays outside the profile."""
    profile = tmp_path / "profile"
    folder = profile / "Music"
    candidate = folder / "collection.nml"
    folder.mkdir(parents=True)
    candidate.write_text("content", encoding="utf-8")

    def fail_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        """Fail the operating system path resolution."""
        raise OSError("the path cannot be resolved")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert locator._candidate_inside_profile(folder, candidate, profile) is False


def test_candidate_confinement_refuses_an_outside_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate that resolves outside the profile stays outside the profile."""
    profile = tmp_path / "profile"
    folder = profile / "Music"
    candidate = folder / "collection.nml"
    outside = tmp_path / "outside"
    folder.mkdir(parents=True)
    candidate.write_text("content", encoding="utf-8")
    outside.mkdir()
    original_resolve = Path.resolve

    def resolve_outside(path: Path, *args: object, **kwargs: object) -> Path:
        """Resolve the candidate folder outside the profile."""
        if path in (folder, candidate):
            return outside.resolve()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside)

    assert locator._candidate_inside_profile(folder, candidate, profile) is False


def test_library_in_folder_refuses_a_candidate_outside_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate resolved outside the profile never reaches the result."""
    profile = tmp_path / "profile"
    folder = profile / "Music"
    candidate = folder / "collection.nml"
    outside = tmp_path / "outside"
    folder.mkdir(parents=True)
    candidate.write_text("content", encoding="utf-8")
    outside.mkdir()
    original_resolve = Path.resolve

    def resolve_outside(path: Path, *args: object, **kwargs: object) -> Path:
        """Resolve the candidate folder outside the profile."""
        if path in (folder, candidate):
            return outside.resolve()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside)

    assert locator._library_in_folder(make_location(), folder, profile) == []


def test_scan_root_confinement_refuses_a_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan root that is a junction contributes no candidate."""
    profile = tmp_path / "profile"
    root = profile / "Music"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == root,
        raising=False,
    )

    assert locator._scan_root_inside_profile(profile, "Music") is None


def test_scan_root_confinement_survives_a_resolution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan root that cannot resolve contributes no candidate."""
    profile = tmp_path / "profile"
    profile.mkdir()

    def fail_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        """Fail the operating system path resolution."""
        raise OSError("the path cannot be resolved")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert locator._scan_root_inside_profile(profile, "Music") is None


def test_scan_root_confinement_refuses_an_outside_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan root that resolves outside the profile contributes no candidate."""
    profile = tmp_path / "profile"
    root = profile / "Music"
    outside = tmp_path / "outside"
    root.mkdir(parents=True)
    outside.mkdir()
    original_resolve = Path.resolve

    def resolve_outside(path: Path, *args: object, **kwargs: object) -> Path:
        """Resolve the scan root outside the profile."""
        if path == root:
            return outside.resolve()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside)

    assert locator._scan_root_inside_profile(profile, "Music") is None


def test_bounded_walk_refuses_a_junction_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction root never enters the bounded locator walk."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == root,
        raising=False,
    )

    assert locator._walk_bounded(root) == []


def test_scanned_matches_skip_a_linked_scan_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linked scan root contributes no scanned library."""
    profile = tmp_path / "profile"
    documents = profile / "Documents"
    documents.mkdir(parents=True)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == documents,
        raising=False,
    )

    assert locator._scanned_matches(profile, ()) == []


def test_scanned_matches_skip_a_repeated_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder found through two scan roots contributes one result."""
    profile = tmp_path / "profile"
    child = profile / "Music" / "child"
    child.mkdir(parents=True)
    monkeypatch.setattr(locator, "SCAN_FOLDER_NAMES", ("Music", "Music"))

    assert locator._scanned_matches(profile, ()) == []


def test_scanned_matches_require_a_profile_directory(tmp_path: Path) -> None:
    """A missing profile folder contributes no scanned library."""
    profile = tmp_path / "missing"

    assert locator._scanned_matches(profile, ()) == []


def test_drive_matches_require_a_device_location() -> None:
    """A drive cannot match a location that does not belong to a device."""
    fixed = tuple(
        location for location in locations_for(Platform.WINDOWS) if not location.on_device
    )
    candidate = TargetCandidate(name="fixed", inspection=None, error="failed")

    assert locator._drive_matches((candidate,), fixed) == []


def test_drive_failure_contributes_no_mount_match() -> None:
    """A drive with no inspection contributes no mount match."""
    location = make_location(
        folders=((Platform.ANY, ("Music",)),),
        on_device=True,
    )
    candidate = TargetCandidate(name="failed", inspection=None, error="failed")

    assert locator._drive_candidate_matches((location,), candidate) == []


def test_library_version_header_reports_a_read_failure(tmp_path: Path) -> None:
    """A library file that cannot be opened produces no version."""
    assert read_library_version(tmp_path / "missing.nml") is None


def test_library_version_header_accepts_the_maximum_length(tmp_path: Path) -> None:
    """A version header accepts the longest supported version value."""
    library = tmp_path / "collection.nml"
    version = "x" * 32
    library.write_bytes(f'version = "{version}"'.encode())

    assert read_library_version(library) == version
