"""Behavioral tests for the imported library and path mapping storage (D349)."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.models import ImportedLibrary, LibraryPathMapping

LIBRARY_ID = "library-1"
MAPPING_ID = "mapping-1"


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    """Return a repository on a file database."""
    return Repository(DatabaseManager(tmp_path / "library.sqlite3"))


def make_library(library_id: str = LIBRARY_ID) -> ImportedLibrary:
    """Return an imported library record."""
    return ImportedLibrary(
        id=library_id,
        format_id="traktor",
        source_path="/music/collection.nml",
        detected_version="3.5.1",
        imported_at="2026-11-23T12:00:00.000000+00:00",
    )


def make_mapping(mapping_id: str = MAPPING_ID, library_id: str = LIBRARY_ID) -> LibraryPathMapping:
    """Return a path mapping record."""
    return LibraryPathMapping(
        id=mapping_id,
        library_id=library_id,
        source_prefix="D:\\Music",
        target_prefix="/Users/user/Music",
        root_path="/Users/user/Music",
    )


def test_a_new_library_holds_the_two_import_tables(tmp_path: Path) -> None:
    """The migration creates the imported library and path mapping tables."""
    db_path = tmp_path / "library.sqlite3"
    repository = Repository(DatabaseManager(db_path))
    repository.upsert_imported_library(make_library())
    repository.upsert_path_mapping(make_mapping())

    assert repository.get_imported_library(LIBRARY_ID) is not None
    assert len(repository.list_path_mappings(LIBRARY_ID)) == 1
    assert LATEST_VERSION >= 4


def test_the_library_record_round_trips(repository: Repository) -> None:
    """A stored library keeps its format, its path, and its version."""
    repository.upsert_imported_library(make_library())

    stored = repository.get_imported_library(LIBRARY_ID)

    assert stored is not None
    assert stored.format_id == "traktor"
    assert stored.source_path == "/music/collection.nml"
    assert stored.detected_version == "3.5.1"


def test_the_library_record_updates_on_a_second_write(repository: Repository) -> None:
    """Writing the same library again updates the stored record."""
    repository.upsert_imported_library(make_library())
    updated = ImportedLibrary(
        id=LIBRARY_ID,
        format_id="traktor",
        source_path="/music/collection.nml",
        detected_version="3.6.0",
        imported_at="2026-11-23T13:00:00.000000+00:00",
    )

    repository.upsert_imported_library(updated)

    stored = repository.get_imported_library(LIBRARY_ID)
    assert stored is not None
    assert stored.detected_version == "3.6.0"


def test_the_libraries_come_back_newest_first(repository: Repository) -> None:
    """The list of libraries puts the newest import first."""
    repository.upsert_imported_library(
        ImportedLibrary(
            id="older",
            format_id="traktor",
            source_path="/a",
            imported_at="2026-11-22T12:00:00.000000+00:00",
        )
    )
    repository.upsert_imported_library(make_library())

    libraries = repository.list_imported_libraries()

    assert [item.id for item in libraries] == [LIBRARY_ID, "older"]


def test_the_path_mapping_round_trips(repository: Repository) -> None:
    """A stored mapping keeps both prefixes and the root."""
    repository.upsert_imported_library(make_library())
    repository.upsert_path_mapping(make_mapping())

    mappings = repository.list_path_mappings(LIBRARY_ID)

    assert len(mappings) == 1
    assert mappings[0].source_prefix == "D:\\Music"
    assert mappings[0].target_prefix == "/Users/user/Music"
    assert mappings[0].root_path == "/Users/user/Music"


def test_the_path_mapping_updates_on_a_second_write(repository: Repository) -> None:
    """Writing the same mapping again updates the stored record."""
    repository.upsert_imported_library(make_library())
    repository.upsert_path_mapping(make_mapping())
    repository.upsert_path_mapping(
        LibraryPathMapping(
            id=MAPPING_ID,
            library_id=LIBRARY_ID,
            source_prefix="D:\\Music",
            target_prefix="/Users/user/Other",
            root_path="/Users/user/Other",
        )
    )

    mappings = repository.list_path_mappings(LIBRARY_ID)

    assert len(mappings) == 1
    assert mappings[0].target_prefix == "/Users/user/Other"


def test_a_deleted_path_mapping_is_gone(repository: Repository) -> None:
    """A deleted mapping no longer comes back."""
    repository.upsert_imported_library(make_library())
    repository.upsert_path_mapping(make_mapping())

    repository.delete_path_mapping(MAPPING_ID)

    assert repository.list_path_mappings(LIBRARY_ID) == []
