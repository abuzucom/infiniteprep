"""Behavioral tests for the one-transaction folder import with per-file savepoints (D238)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
    TechnicalAndTagMetadata,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import BatchIntegrityError, Repository
from infiniteprep.domain.enums import GroupingBasis, SourceType
from infiniteprep.domain.models import SourceGroup
from infiniteprep.services.import_service import ImportOutcome, ImportService
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_COUNT = 3
SHORT_DURATION_SEC = 0.5


class FailingTagAdapter(MetadataTagAdapter):
    """Raise a chosen exception for one chosen file, as an unexpected parser error would."""

    def __init__(self, failing_name: str, error: BaseException | None = None) -> None:
        super().__init__()
        self.failing_name = failing_name
        self.error = error or ZeroDivisionError("division by zero")

    def read_metadata(self, file_path: Path) -> TechnicalAndTagMetadata:
        """Raise the chosen exception on the chosen file, and read every other file."""
        if file_path.name == self.failing_name:
            raise self.error
        return super().read_metadata(file_path)


def make_tracks(folder: Path, count: int = TRACK_COUNT) -> list[Path]:
    """Create count short WAV files with distinct content under folder."""
    return [
        create_wav_fixture(folder / f"track{index}.wav", duration_sec=SHORT_DURATION_SEC + index)
        for index in range(count)
    ]


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    """Return a repository on a file database, so other threads see only committed rows."""
    return Repository(DatabaseManager(tmp_path / "library.sqlite3"))


def test_batch_imports_every_file_and_reports_progress(
    repository: Repository, tmp_path: Path
) -> None:
    """Each file gets one progress call before it imports, and the batch commits at the end."""
    tracks = make_tracks(tmp_path / "crate")
    calls: list[tuple[int, int, str]] = []

    report = ImportService(repository).import_paths(
        [tmp_path / "crate"], progress=lambda done, total, path: calls.append((done, total, path))
    )

    assert report.outcome is ImportOutcome.COMMITTED
    assert report.scanned_count == TRACK_COUNT
    assert calls == [
        (index + 1, TRACK_COUNT, str(track.resolve())) for index, track in enumerate(tracks)
    ]
    assert len(repository.list_files()) == TRACK_COUNT


def test_other_connections_see_nothing_until_the_batch_commits(
    repository: Repository, tmp_path: Path
) -> None:
    """A reader on another thread counts no imported rows while the batch is still open."""
    make_tracks(tmp_path / "crate")
    counts_seen_elsewhere: list[int] = []

    def count_from_other_thread(done: int, total: int, _path: str) -> None:
        if done == total:
            reader = threading.Thread(
                target=lambda: counts_seen_elsewhere.append(len(repository.list_files()))
            )
            reader.start()
            reader.join()

    ImportService(repository).import_paths([tmp_path / "crate"], progress=count_from_other_thread)

    assert counts_seen_elsewhere == [0]
    assert len(repository.list_files()) == TRACK_COUNT


def test_cancel_rolls_back_the_whole_batch(repository: Repository, tmp_path: Path) -> None:
    """Cancel after the first file stops before the next file and keeps nothing."""
    make_tracks(tmp_path / "crate")
    checks: list[int] = []

    def cancel_after_first_file() -> bool:
        checks.append(1)
        return len(checks) > 1

    report = ImportService(repository).import_paths(
        [tmp_path / "crate"], should_cancel=cancel_after_first_file
    )

    assert report.outcome is ImportOutcome.CANCELLED
    assert report.imported_files == []
    assert repository.list_files() == []
    assert repository.list_source_groups() == []


def test_a_file_that_fails_is_skipped_and_the_rest_commit(
    repository: Repository, tmp_path: Path
) -> None:
    """A file removed after the scan fails alone, and the other files still import."""
    tracks = make_tracks(tmp_path / "crate")
    vanishing = tracks[1].resolve()

    def remove_second_file(_done: int, _total: int, path: str) -> None:
        if path == str(vanishing):
            vanishing.unlink()

    report = ImportService(repository).import_paths(
        [tmp_path / "crate"], progress=remove_second_file
    )

    assert report.outcome is ImportOutcome.COMMITTED
    assert [path for path, _reason in report.failed_files] == [str(vanishing)]
    assert sorted(asset.current_path for asset in repository.list_files()) == sorted(
        str(track.resolve()) for track in (tracks[0], tracks[2])
    )


def test_any_error_in_one_file_skips_only_that_file(repository: Repository, tmp_path: Path) -> None:
    """An error type the reader does not expect still skips one file, not the whole batch."""
    tracks = make_tracks(tmp_path / "crate")
    service = ImportService(repository, FailingTagAdapter(tracks[1].name))

    report = service.import_paths([tmp_path / "crate"])

    assert report.outcome is ImportOutcome.COMMITTED
    assert report.failed_files == [
        (str(tracks[1].resolve()), "ZeroDivisionError: division by zero")
    ]
    assert len(repository.list_files()) == TRACK_COUNT - 1


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(1)])
def test_an_interrupt_in_one_file_rolls_back_the_whole_batch(
    repository: Repository, tmp_path: Path, interrupt: BaseException
) -> None:
    """KeyboardInterrupt and SystemExit are not Exception. They escape the per-file handler."""
    tracks = make_tracks(tmp_path / "crate")
    service = ImportService(repository, FailingTagAdapter(tracks[1].name, interrupt))

    with pytest.raises(type(interrupt)):
        service.import_paths([tmp_path / "crate"])

    assert repository.list_files() == []
    assert repository.list_source_groups() == []


def test_each_thread_gets_its_own_connection(repository: Repository) -> None:
    """A worker thread's connection is a different object from the calling thread's."""
    main_connection = repository.db.connect()
    worker_connections: list[object] = []
    worker = threading.Thread(target=lambda: worker_connections.append(repository.db.connect()))
    worker.start()
    worker.join()

    assert worker_connections
    assert worker_connections[0] is not main_connection
    assert repository.db.connect() is main_connection


def test_a_batch_refuses_an_open_transaction(repository: Repository) -> None:
    """A batch never absorbs or discards uncommitted work already on the connection."""
    connection = repository.db.connect()
    connection.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="uncommitted transaction"), repository.batch():
            pass
    finally:
        connection.rollback()


def test_an_unexpected_error_rolls_back_and_propagates(
    repository: Repository, tmp_path: Path
) -> None:
    """An error outside the per-file scope rolls the batch back and reaches the caller."""
    make_tracks(tmp_path / "crate")
    checks: list[int] = []

    def fail_on_second_check() -> bool:
        checks.append(1)
        if len(checks) > 1:
            raise RuntimeError("cancel check failed")
        return False

    with pytest.raises(RuntimeError, match="cancel check failed"):
        ImportService(repository).import_paths(
            [tmp_path / "crate"], should_cancel=fail_on_second_check
        )

    assert repository.list_files() == []


def test_a_second_batch_on_the_same_thread_is_refused(repository: Repository) -> None:
    """Nested batches on one thread raise instead of silently joining the outer batch."""
    with (
        repository.batch(),
        pytest.raises(RuntimeError, match="already open"),
        repository.batch(),
    ):
        pass


def test_a_savepoint_that_cannot_roll_back_aborts_the_batch(repository: Repository) -> None:
    """When an item's rollback fails, the whole batch rolls back and no partial item stays."""
    group = SourceGroup(
        id="group-1",
        user_label="Crate",
        grouping_basis=GroupingBasis.FOLDER_HYPOTHESIS,
        original_folder="/music/crate",
        source_type=SourceType.UNKNOWN,
        notes="",
    )
    with (
        pytest.raises(BatchIntegrityError, match="could not roll back"),
        repository.batch() as batch,
        batch.savepoint(),
    ):
        repository.upsert_source_group(group)
        # Releasing the savepoint early makes the later ROLLBACK TO fail.
        repository.db.connect().execute("RELEASE batch_item")
        raise OSError("item failed")

    assert repository.list_source_groups() == []


def test_writes_outside_a_batch_still_commit_at_once(
    repository: Repository, tmp_path: Path
) -> None:
    """The plain import path with no callbacks keeps its earlier visible result."""
    make_tracks(tmp_path / "crate", count=1)

    report = ImportService(repository).import_paths([tmp_path / "crate"])

    assert report.outcome is ImportOutcome.COMMITTED
    assert len(report.imported_files) == 1
    assert len(repository.list_files()) == 1
