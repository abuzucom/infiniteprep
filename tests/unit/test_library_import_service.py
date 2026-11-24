"""End-to-end tests for the library import service with a fake source (D333)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import mock

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import ApprovalOrigin
from infiniteprep.domain.models import ApprovedField, ImportedLibrary
from infiniteprep.services.library_import.importer import ImporterRegistry
from infiniteprep.services.library_import.models import (
    ImportedPendingValue,
    ImportedTrack,
    ImportedValue,
    SourceLibrary,
)
from infiniteprep.services.library_import.path_mapping import PathMapping
from infiniteprep.services.library_import.service import (
    LibraryImportRequest,
    LibraryImportService,
)
from tests.fixtures.audio_factory import create_wav_fixture

SHORT_DURATION_SEC = 0.5
LIBRARY_FILE = "collection.nml"


class FakeImporter:
    """Read a fake library that names the tracks of a folder."""

    def __init__(self, tracks: tuple[ImportedTrack, ...], format_id: str = "fake") -> None:
        self.format_id = format_id
        self._tracks = tracks
        self.read_paths: list[Path] = []

    def read_library(self, source_path: Path) -> SourceLibrary:
        """Return a fake library that names the given tracks."""
        self.read_paths.append(source_path)
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


@pytest.fixture
def music(tmp_path: Path) -> Path:
    """Return a folder that holds the audio files."""
    folder = tmp_path / "music"
    folder.mkdir()
    return folder


def write_library(music: Path) -> Path:
    """Write a fake library file and return its path."""
    path = music / LIBRARY_FILE
    path.write_text('<HEAD PROGRAM="fake"/>', encoding="utf-8")
    return path


def make_service(repository: Repository, tracks: tuple[ImportedTrack, ...]) -> LibraryImportService:
    """Return a service that reads a fake library with the given tracks."""
    registry = ImporterRegistry()
    registry.register(FakeImporter(tracks))
    return LibraryImportService(repository, registry)


def test_import_commits_the_tracks_of_the_fake_source(repository: Repository, music: Path) -> None:
    """A commit stores the track rows, the approved values, and the library record."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(result)

    stored = repository.list_files()
    assert len(stored) == 1
    assert stored[0].current_path == str(track_path)
    approved = repository.get_approved_field(stored[0].id, "title")
    assert approved is not None
    assert approved.approved_value == "Song"
    assert len(repository.list_imported_libraries()) == 1


def test_import_leaves_the_source_library_untouched(repository: Repository, music: Path) -> None:
    """The import reads the source library and never writes to it."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    before = library_path.read_bytes()
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(result)

    assert library_path.read_bytes() == before


def test_import_reports_a_conflict_with_a_stored_value(repository: Repository, music: Path) -> None:
    """A track with a stored value of its own raises a conflict and a proposal."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "New"),)),),
    )
    first = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(first)
    stored = repository.list_files()[0]
    repository.upsert_approved_field(
        _approved_field(stored.id, "title", "Old"),
    )

    second = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert len(second.conflicts) == 1
    assert second.conflicts[0].stored_value == "Old"
    assert second.conflicts[0].imported_value == "New"


def test_import_holds_a_value_that_waits_for_its_storage(
    repository: Repository, music: Path
) -> None:
    """A cue value has no storage yet, so the import holds it as pending."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (
            ImportedTrack(
                source_path=str(track_path),
                values=(ImportedValue("title", "Song"),),
                pending=(ImportedPendingValue("cues", {"count": 2}),),
            ),
        ),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.pending_fields == ["cues"]


def test_import_leaves_a_track_without_an_audio_file_unresolved(
    repository: Repository, music: Path
) -> None:
    """A track whose audio file is missing stays unresolved and writes no row."""
    library_path = write_library(music)
    missing = music / "gone.wav"
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(missing), values=(ImportedValue("title", "Song"),)),),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.unresolved_paths == [str(missing)]
    assert result.plan.assets == []


def test_a_cancelled_import_writes_nothing(repository: Repository, music: Path) -> None:
    """A plan that is never committed leaves the database empty."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )

    service.build_plan(LibraryImportRequest(source_path=library_path, format_id="fake", root=music))

    assert repository.list_files() == []
    assert repository.list_imported_libraries() == []


def test_import_maps_a_library_path_onto_this_computer(
    repository: Repository, music: Path, tmp_path: Path
) -> None:
    """A mapping moves the path of a track before the import reads the file."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path="D:\\Music\\track.wav", values=(ImportedValue("title", "S"),)),),
    )
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=str(music))

    result = service.build_plan(
        LibraryImportRequest(
            source_path=library_path, format_id="fake", root=music, mappings=(mapping,)
        )
    )

    assert len(result.plan.assets) == 1
    assert result.plan.assets[0].current_path == str(track_path)


def test_import_leaves_a_path_that_escapes_the_root_unresolved(
    repository: Repository, music: Path
) -> None:
    """A mapping that would leave the root leaves the track unresolved."""
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path="D:\\Music\\track.wav", values=(ImportedValue("title", "S"),)),),
    )
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix="/elsewhere")

    result = service.build_plan(
        LibraryImportRequest(
            source_path=library_path, format_id="fake", root=music, mappings=(mapping,)
        )
    )

    assert result.unresolved_paths == ["D:\\Music\\track.wav"]
    assert result.plan.assets == []


def test_import_finds_a_track_that_moved(
    repository: Repository, music: Path, tmp_path: Path
) -> None:
    """A track whose file moved is found by the search and imported at the new path."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )
    first = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(first)
    assert len(repository.list_files()) == 1
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

    assert second.unresolved_paths == []
    assert len(second.relocations) == 1
    assert second.relocations[0].found_path == moved
    assert second.relocations[0].confirmed is True


def test_import_leaves_a_track_outside_the_root_unresolved(
    repository: Repository, music: Path, tmp_path: Path
) -> None:
    """A source library cannot make the import read a file outside the root."""
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"secret bytes")
    library_path = write_library(music)
    service = make_service(
        repository,
        (
            ImportedTrack(
                source_path=str(outside),
                values=(ImportedValue("title", "S"),),
            ),
        ),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.unresolved_paths == [str(outside)]
    assert result.plan.assets == []


def test_import_leaves_a_track_that_leaves_the_root_through_a_link_unresolved(
    repository: Repository, music: Path, tmp_path: Path
) -> None:
    """A track that reaches a file through a link out of the root stays unresolved."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.wav").write_bytes(b"secret bytes")
    link = music / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")
    library_path = write_library(music)
    service = make_service(
        repository,
        (
            ImportedTrack(
                source_path=str(link / "secret.wav"),
                values=(ImportedValue("title", "S"),),
            ),
        ),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.plan.assets == []
    assert result.unresolved_paths == [str(link / "secret.wav")]


def test_the_library_record_and_the_assets_share_one_identifier(
    repository: Repository, music: Path
) -> None:
    """The stored library names the same import as the assets it holds."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(result)

    stored = repository.list_files()[0]
    assert stored.import_batch_id == result.library.id
    assert repository.get_imported_library(result.library.id) is not None


def test_the_import_saves_the_path_mappings(repository: Repository, music: Path) -> None:
    """A commit saves the prefix mappings of the import."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=str(music))

    result = service.build_plan(
        LibraryImportRequest(
            source_path=library_path, format_id="fake", root=music, mappings=(mapping,)
        )
    )
    service.commit(result)

    stored = repository.list_path_mappings(result.library.id)
    assert len(stored) == 1
    assert stored[0].source_prefix == "D:\\Music"
    assert stored[0].target_prefix == str(music)
    assert stored[0].root_path == str(music)


def test_a_failed_library_write_rolls_the_whole_import_back(
    repository: Repository, music: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The library record and the assets share one transaction.

    A failure while the library record is written rolls the assets and the
    approved fields back with it.
    """
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "Song"),)),),
    )
    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    def failing_write(self: Repository, library: ImportedLibrary) -> ImportedLibrary:
        """Fail while the library record is written."""
        raise sqlite3.IntegrityError("the library record cannot be stored")

    monkeypatch.setattr(Repository, "upsert_imported_library", failing_write)

    with pytest.raises(sqlite3.IntegrityError):
        service.commit(result)

    assert repository.list_files() == []
    assert repository.list_imported_libraries() == []
    assert repository.list_source_groups() == []
    assert repository.approved_fields_by_file() == {}


def test_the_repository_writes_join_the_open_batch(repository: Repository) -> None:
    """A write inside a batch is not visible to another connection until the batch ends.

    The batch owns the transaction. A repository method inside it does not
    commit on its own.
    """
    with repository.batch(immediate=True):
        repository.upsert_imported_library(
            ImportedLibrary(
                id="library-in-batch",
                format_id="traktor",
                source_path="/music/collection.nml",
                detected_version="3.5.1",
                imported_at="2026-11-23T00:00:00.000000+00:00",
            )
        )
        assert repository.get_imported_library("library-in-batch") is not None
        with closing(sqlite3.connect(repository.db.db_path)) as other:
            row = other.execute(
                "SELECT COUNT(*) FROM imported_libraries WHERE id = ?",
                ("library-in-batch",),
            ).fetchone()
        assert row[0] == 0

    assert repository.get_imported_library("library-in-batch") is not None


def test_a_manual_request_reads_through_the_reader_of_its_format(
    repository: Repository, music: Path
) -> None:
    """A request that names the manual format gets the reader of the file."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    importer = FakeImporter(
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "S"),)),),
        format_id="traktor",
    )
    registry = ImporterRegistry()
    registry.register(importer)
    service = LibraryImportService(repository, registry)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="manual", root=music)
    )

    assert importer.read_paths == [library_path]
    assert len(result.plan.assets) == 1


def test_the_imported_assets_name_the_source_group_of_the_import(
    repository: Repository, music: Path
) -> None:
    """Every imported asset stays with the group that holds the imported library."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "S"),)),),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )
    service.commit(result)

    groups = repository.list_source_groups()
    assert len(groups) == 1
    assets = repository.list_files(source_group_id=groups[0].id)
    assert [asset.id for asset in assets] == [result.plan.assets[0].id]


def test_a_file_that_is_replaced_while_it_opens_stays_unresolved(
    repository: Repository, music: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that changes between the check and the read never reaches the app."""
    track_path = music / "track.wav"
    create_wav_fixture(track_path, duration_sec=SHORT_DURATION_SEC)
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(track_path), values=(ImportedValue("title", "S"),)),),
    )
    outside = tmp_path / "outside.wav"
    outside.write_bytes(track_path.read_bytes())
    original_read = MetadataTagAdapter.read_metadata_from_handle

    def swapping_read(self: MetadataTagAdapter, handle: object, file_path: Path):
        """Replace the file with another one while the parser reads it."""
        result = original_read(self, handle, file_path)
        if file_path.name == "track.wav":
            file_path.unlink()
            outside.replace(file_path)
        return result

    monkeypatch.setattr(MetadataTagAdapter, "read_metadata_from_handle", swapping_read)

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.unresolved_paths == [str(track_path)]
    assert result.plan.assets == []


def test_a_parser_that_fails_leaves_the_track_unresolved(
    repository: Repository, music: Path
) -> None:
    """A file whose parser raises leaves the track unresolved."""
    broken = music / "broken.wav"
    broken.write_bytes(b"not a wav file at all")
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(broken), values=(ImportedValue("title", "S"),)),),
    )

    with mock.patch.object(
        MetadataTagAdapter,
        "read_metadata_from_handle",
        side_effect=ValueError("broken header"),
    ):
        result = service.build_plan(
            LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
        )

    assert result.unresolved_paths == [str(broken)]
    assert result.plan.assets == []


def test_a_damaged_file_still_imports_with_its_warning(repository: Repository, music: Path) -> None:
    """A file the parser reads with a warning imports and keeps the warning."""
    damaged = music / "damaged.wav"
    damaged.write_bytes(b"not a wav file at all")
    library_path = write_library(music)
    service = make_service(
        repository,
        (ImportedTrack(source_path=str(damaged), values=(ImportedValue("title", "S"),)),),
    )

    result = service.build_plan(
        LibraryImportRequest(source_path=library_path, format_id="fake", root=music)
    )

    assert result.unresolved_paths == []
    assert len(result.plan.assets) == 1
    warnings = result.plan.assets[0].original_tags["technical"]["warnings"]
    assert any("Damaged" in warning for warning in warnings)


def _approved_field(file_id: str, field_name: str, value: str) -> ApprovedField:
    """Return an approved field for one value."""
    return ApprovedField(
        file_id=file_id,
        field_name=field_name,
        approved_value=value,
        approval_origin=ApprovalOrigin.MANUAL_EDIT,
        approved_at="2026-11-23T00:00:00.000000+00:00",
    )
