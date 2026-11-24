"""Tests for the row mapping, the relocation row, and the change count (D333)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.library_import import relocation
from infiniteprep.services.library_import.importer import ImporterRegistry
from infiniteprep.services.library_import.locations import FormatId
from infiniteprep.services.library_import.locator import LocatedLibrary
from infiniteprep.services.library_import.models import (
    ImportedTrack,
    ImportedValue,
    SourceLibrary,
)
from infiniteprep.services.library_import.relocation import MissingReference
from infiniteprep.services.library_import.service import (
    LibraryImportRequest,
    LibraryImportService,
)
from infiniteprep.ui.library_import_dialog import LibraryImportDialog
from tests.fixtures.audio_factory import create_wav_fixture

SHORT_DURATION_SEC = 0.5
LIBRARY_FILE = "collection.nml"
TRACK_BYTES = b"the track bytes"


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


def build_library(path: str, format_id: FormatId) -> LocatedLibrary:
    """Return one located library for the dialog."""
    return LocatedLibrary(
        format_id=format_id,
        file_path=Path(path),
        folder_path=Path(path).parent,
        version="1.0",
        modified_at=1_700_000_000.0,
        observed=False,
        citation_url="https://support.native-instruments.com/",
        citation_note="The Root Directory holds collection.nml.",
    )


def test_the_dialog_picks_the_library_of_the_visible_row(qtbot) -> None:
    """A hidden library that comes first never takes the pick of a visible row."""
    hidden = build_library("/music/hidden.nml", FormatId.SERATO)
    visible = build_library("/music/collection.nml", FormatId.TRAKTOR)
    dialog = LibraryImportDialog([hidden, visible], readable_formats=[FormatId.TRAKTOR])
    qtbot.addWidget(dialog)

    dialog.found_list.setCurrentRow(0)

    picked = dialog.chosen_library()
    assert picked is not None
    assert picked.file_path == Path("/music/collection.nml")


def test_the_dialog_reports_no_library_when_no_format_is_readable(qtbot) -> None:
    """The placeholder row of an empty list names no library."""
    dialog = LibraryImportDialog(
        [build_library("/music/hidden.nml", FormatId.SERATO)], readable_formats=[]
    )
    qtbot.addWidget(dialog)

    dialog.found_list.setCurrentRow(0)

    assert dialog.chosen_library() is None


def test_a_relocated_track_keeps_its_library_row(repository: Repository, tmp_path: Path) -> None:
    """A track that moved keeps its row, so the library holds no second copy."""
    music = tmp_path / "music"
    music.mkdir()
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = music / LIBRARY_FILE
    library_path.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    registry = ImporterRegistry()
    registry.register(
        FakeImporter(
            (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),)
        )
    )
    service = LibraryImportService(repository, registry)
    first = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(first)
    stored = repository.list_files()
    assert len(stored) == 1
    moved_root = tmp_path / "moved"
    moved_root.mkdir()
    moved = moved_root / "track.wav"
    moved.write_bytes(track_path.read_bytes())
    track_path.unlink()

    second = service.build_plan(
        LibraryImportRequest(
            source_path=library_path,
            format_id="fake",
            root=tmp_path,
            relocation_root=moved_root,
        )
    )
    service.commit(second)

    after = repository.list_files()
    assert len(after) == 1
    assert after[0].id == stored[0].id
    assert after[0].current_path == str(moved)


def test_an_import_that_found_no_track_still_reports_a_change(
    repository: Repository, tmp_path: Path
) -> None:
    """The library record and the path mappings are changes worth committing."""
    music = tmp_path / "music"
    music.mkdir()
    library_path = music / LIBRARY_FILE
    library_path.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    registry = ImporterRegistry()
    registry.register(FakeImporter(()))
    service = LibraryImportService(repository, registry)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.has_changes is True
    service.commit(result)
    assert len(repository.list_imported_libraries()) == 1


def test_the_relocation_hash_refuses_a_file_that_a_link_replaced(tmp_path: Path) -> None:
    """A candidate that a link replaced never reads the file the link points at."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(TRACK_BYTES)
    link = root / "track.wav"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("the platform does not allow a symbolic link")
    reference = MissingReference(
        path="/music/track.wav",
        file_size=os.lstat(link).st_size,
        content_hash=compute_sha256(outside),
    )

    results = relocation.find_relocated_files([reference], root)

    assert results[0].confirmed is False
