"""Tests for the import root, the pending values, and the locator boundary (D333)."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.library_import import locator
from infiniteprep.services.library_import.importer import ImporterRegistry
from infiniteprep.services.library_import.locations import FormatId, KnownLocation
from infiniteprep.services.library_import.merge import MergeContext, merge_library
from infiniteprep.services.library_import.models import (
    ImportedPendingValue,
    ImportedTrack,
    ImportedValue,
    SourceLibrary,
)
from infiniteprep.services.library_import.plan import ImportPlan
from infiniteprep.services.library_import.service import (
    LibraryImportRequest,
    LibraryImportService,
)

LIBRARY_FILE = "collection.nml"


class FakeImporter:
    """Read a fake library that names the tracks of a folder."""

    def __init__(self, tracks: tuple[ImportedTrack, ...], format_id: str = "fake") -> None:
        self.format_id = format_id
        self._tracks = tracks

    def read_library(self, source_path: Path) -> SourceLibrary:
        """Return a fake library that names the given tracks."""
        return SourceLibrary(
            source_format=self.format_id,
            source_path=str(source_path),
            version="1.0",
            tracks=self._tracks,
        )


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    """Return a repository on a file database."""
    return Repository(DatabaseManager(tmp_path / "library.sqlite3"))


def make_service(repository: Repository) -> LibraryImportService:
    """Return a service that reads a fake library with no tracks."""
    registry = ImporterRegistry()
    registry.register(FakeImporter(()))
    return LibraryImportService(repository, registry)


def make_library(music: Path) -> Path:
    """Write a fake library file and return its path."""
    path = music / LIBRARY_FILE
    path.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    return path


def test_a_relative_root_stops_the_import(repository: Repository, tmp_path: Path) -> None:
    """A root that names no absolute folder stops the run before the plan starts."""
    music = tmp_path / "music"
    music.mkdir()
    library_path = make_library(music)
    service = make_service(repository)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=Path("music"))
    )

    assert "absolute" in result.error
    assert result.has_changes is False


def test_a_root_that_does_not_exist_stops_the_import(
    repository: Repository, tmp_path: Path
) -> None:
    """A root that names no existing folder stops the run."""
    music = tmp_path / "music"
    music.mkdir()
    library_path = make_library(music)
    service = make_service(repository)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=tmp_path / "absent")
    )

    assert "does not exist" in result.error
    assert result.has_changes is False


def test_a_root_that_names_a_file_stops_the_import(repository: Repository, tmp_path: Path) -> None:
    """A root that names a file stops the run."""
    music = tmp_path / "music"
    music.mkdir()
    library_path = make_library(music)
    service = make_service(repository)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=library_path)
    )

    assert "not a folder" in result.error
    assert result.has_changes is False


def test_a_root_that_is_a_link_stops_the_import(repository: Repository, tmp_path: Path) -> None:
    """A root that names a link stops the run."""
    music = tmp_path / "music"
    music.mkdir()
    library_path = make_library(music)
    link = tmp_path / "linked"
    try:
        link.symlink_to(music, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("the platform does not allow a symbolic link")
    service = make_service(repository)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=link)
    )

    assert "link" in result.error
    assert result.has_changes is False


def test_a_valid_root_starts_the_import(repository: Repository, tmp_path: Path) -> None:
    """A root that names an existing folder starts the run."""
    music = tmp_path / "music"
    music.mkdir()
    library_path = make_library(music)
    service = make_service(repository)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.error == ""


def test_the_plan_keeps_the_pending_values_of_the_merge() -> None:
    """A value with no storage keeps its payload in the plan until the commit."""
    library = SourceLibrary(
        source_format="fake",
        source_path="/music/collection.nml",
        version="1.0",
        tracks=(
            ImportedTrack(
                source_path="/music/track.wav",
                values=(ImportedValue("my_tag", "Value"),),
                pending=(ImportedPendingValue(field_name="other_tag", payload={"value": "Two"}),),
            ),
        ),
    )
    outcome = merge_library(MergeContext(library=library))
    plan = ImportPlan(library=library)

    plan.apply_merge(outcome)

    names = [item.field_name for item in plan.pending_values]
    assert "other_tag" in names
    assert "my_tag" in names
    payload = next(item.payload for item in plan.pending_values if item.field_name == "my_tag")
    assert payload["value"] == "Value"
    assert payload["source_path"] == "/music/track.wav"


def test_the_locator_skips_a_candidate_that_a_link_replaced(tmp_path: Path) -> None:
    """A folder that a link replaced holds no candidate inside the profile."""
    profile = tmp_path / "profile"
    profile.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / LIBRARY_FILE).write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    link = profile / "Music"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("the platform does not allow a symbolic link")
    location = KnownLocation(
        format_id=FormatId.TRAKTOR,
        file_name=LIBRARY_FILE,
        folders=(),
        citation_url="https://support.native-instruments.com/",
        citation_note="The Root Directory holds collection.nml.",
    )

    assert locator._library_in_folder(location, link, profile) == []
