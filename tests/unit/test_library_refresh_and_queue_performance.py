"""Table refresh, playlist order, journal checks, layout saves, and queued transcodes scale."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, OperationStatus
from infiniteprep.domain.models import AnalysisResult, ChangeRecord, FieldProposal, utc_now_iso
from infiniteprep.domain.playlist_order import order_tracks
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind, BatchState
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.transcode_batch import TranscodeBatchSummary, transcode_files
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.action_handlers import run_transcode_with_confirmation
from infiniteprep.ui.batch_queue import format_batch_result
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.qt_dialogs import DialogRecorder

# Positional argument index of progress(done, total, path).
PROGRESS_DONE_INDEX = 0

SMALL_LIBRARY = 2
LARGE_LIBRARY = 6
RESIZE_STEPS = 5
COLUMN_WIDTH_START = 150
TWO_SECOND_WAIT_MS = 2000


class CountingSettingsManager:
    """Keep settings in memory and count each save."""

    def __init__(self) -> None:
        self.settings = AppSettings()
        self.save_count = 0

    def load(self) -> AppSettings:
        """Return a separate settings value for each load."""
        return replace(self.settings)

    def save(self, settings: AppSettings) -> None:
        """Retain a copy of the saved value and count the save."""
        self.settings = replace(settings)
        self.save_count += 1


@contextmanager
def count_statements(repo: Repository) -> Iterator[list[str]]:
    """Record every SQL statement the repository connection runs inside the block."""
    statements: list[str] = []
    conn: sqlite3.Connection = repo.db.connect()
    conn.set_trace_callback(statements.append)
    try:
        yield statements
    finally:
        conn.set_trace_callback(None)


def seed_proposal_and_analysis(repo: Repository, file_id: str) -> None:
    """Store one pending field proposal and one analysis result for file_id."""
    repo.upsert_field_proposal(
        FieldProposal(
            id=f"proposal-{file_id}",
            file_id=file_id,
            field_name=FieldName.GENRE.value,
            proposed_value="House",
            provider="test",
            provider_entity_id=f"entity-{file_id}",
        )
    )
    repo.upsert_analysis_result(
        AnalysisResult(
            id=f"analysis-{file_id}",
            file_id=file_id,
            analyzer_name="test",
            analyzer_version="1",
            configuration={},
            bpm=128.0,
            key_tonic=None,
            key_mode=None,
            analysis_strength=None,
        )
    )


def build_library(tmp_path: Path, count: int) -> tuple[Repository, ImportService, list[str]]:
    """Import count WAV files, each with approved fields, a proposal, and an analysis result."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MetadataTagAdapter())
    paths = [
        create_wav_fixture(tmp_path / f"lib{count}" / f"track{index}.wav", duration_sec=0.5)
        for index in range(count)
    ]
    file_ids = [asset.id for asset in importer.import_paths(paths).imported_files]
    review = ReviewService(repo)
    for file_id in file_ids:
        review.set_manual_field_value(file_id, FieldName.ARTIST.value, "Sasha")
        review.set_manual_field_value(file_id, FieldName.BPM.value, "128")
        review.set_manual_field_value(file_id, FieldName.KEY.value, "Am")
        seed_proposal_and_analysis(repo, file_id)
    return repo, importer, file_ids


def refresh_statement_count(qtbot: Any, tmp_path: Path, count: int) -> int:
    """Return the SQL statement count of one table refresh over count files."""
    repo, importer, _ids = build_library(tmp_path, count)
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    with count_statements(repo) as statements:
        window.refresh_file_table()
    return len(statements)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> CountingSettingsManager:
    """Keep the window settings in memory and count the saves."""
    manager = CountingSettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def test_table_refresh_queries_stay_flat(
    qtbot: Any, tmp_path: Path, settings: CountingSettingsManager
) -> None:
    """A refresh runs a fixed number of queries, however many files the library holds."""
    small = refresh_statement_count(qtbot, tmp_path, SMALL_LIBRARY)
    large = refresh_statement_count(qtbot, tmp_path, LARGE_LIBRARY)

    assert large == small


def test_table_refresh_still_shows_approved_values(
    qtbot: Any, tmp_path: Path, settings: CountingSettingsManager
) -> None:
    """The bulk reads give each row its own approved values."""
    repo, importer, _ids = build_library(tmp_path, SMALL_LIBRARY)
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)

    window.search_input.setText("sasha")

    assert window.file_table.rowCount() == SMALL_LIBRARY


def test_bulk_readers_match_the_per_file_readers(tmp_path: Path) -> None:
    """Each bulk reader returns what the per-file reader returns for every file."""
    repo, _importer, file_ids = build_library(tmp_path, SMALL_LIBRARY)

    approved = repo.approved_fields_by_file()
    proposals = repo.field_proposals_by_file(file_ids)
    analyses = repo.analysis_results_by_file(file_ids[:1])

    for file_id in file_ids:
        assert approved[file_id] == repo.get_approved_fields_map(file_id)
        assert proposals[file_id] == repo.list_field_proposals(file_id)
        assert len(proposals[file_id]) == 1
    # The filtered reader returns the requested file only, with its own results.
    assert set(analyses) == {file_ids[0]}
    assert analyses[file_ids[0]] == repo.list_analysis_results(file_ids[0])


def test_playlist_order_queries_stay_flat(tmp_path: Path) -> None:
    """Ordering a playlist reads its tracks in a fixed number of queries."""
    small_repo, _, small_ids = build_library(tmp_path, SMALL_LIBRARY)
    large_repo, _, large_ids = build_library(tmp_path, LARGE_LIBRARY)

    with count_statements(small_repo) as small_statements:
        order_tracks(small_repo, small_ids, bpm_first=True)
    with count_statements(large_repo) as large_statements:
        ordered = order_tracks(large_repo, large_ids, bpm_first=True)

    assert len(large_statements) == len(small_statements)
    assert sorted(ordered) == sorted(large_ids)


def test_interrupted_check_reads_in_progress(tmp_path: Path) -> None:
    """The status query returns the in-progress records and no completed ones."""
    repo, _importer, file_ids = build_library(tmp_path, SMALL_LIBRARY)
    for index, status in enumerate((OperationStatus.COMPLETED, OperationStatus.IN_PROGRESS)):
        repo.upsert_change_record(
            ChangeRecord(
                operation_id=f"op-{index}",
                file_id=file_ids[0],
                previous_path="/crate/a.wav",
                new_path="/crate/a.wav",
                previous_tags={},
                written_tags={},
                backup_reference="",
                operation_status=status,
                timestamp=utc_now_iso(),
            )
        )

    pending = repo.list_change_records_with_status(OperationStatus.IN_PROGRESS)

    assert [record.operation_id for record in pending] == ["op-1"]


def test_a_column_drag_saves_the_layout_once(
    qtbot: Any, tmp_path: Path, settings: CountingSettingsManager
) -> None:
    """Many resize steps in a row write the settings file one time."""
    repo, importer, _ids = build_library(tmp_path, SMALL_LIBRARY)
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    header = window.file_table.horizontalHeader()

    for step in range(RESIZE_STEPS):
        header.resizeSection(0, COLUMN_WIDTH_START + step)
    qtbot.waitUntil(lambda: settings.save_count > 0, timeout=TWO_SECOND_WAIT_MS)

    assert settings.save_count == 1


def test_closing_the_window_saves_a_pending_layout(
    qtbot: Any, tmp_path: Path, settings: CountingSettingsManager
) -> None:
    """A resize right before close still reaches the settings file."""
    repo, importer, _ids = build_library(tmp_path, SMALL_LIBRARY)
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    saves_before_resize = settings.save_count
    window.file_table.horizontalHeader().resizeSection(0, COLUMN_WIDTH_START)

    window.close()

    assert settings.save_count == saves_before_resize + 1
    assert settings.settings.file_table_layout


def test_transcode_cancel_skips_next_file(tmp_path: Path) -> None:
    """A cancel request ends the batch before the next file and keeps the finished one."""
    repo, _importer, file_ids = build_library(tmp_path, SMALL_LIBRARY)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")
    seen = CallRecorder(PROGRESS_DONE_INDEX)

    def cancel_after_first_file() -> bool:
        """Ask to stop once one file has reported progress."""
        return bool(seen)

    summary = transcode_files(
        transcoder,
        file_ids,
        preset=TranscodePreset.XDJ_LOSSLESS_AUTO,
        mode=TranscodeMode.SIBLING_EXPORT,
        should_cancel=cancel_after_first_file,
        progress=seen,
    )

    assert summary.stopped_early is True
    assert len(summary.results) == 1
    assert seen == [1]


def test_confirmed_transcode_runs_as_a_queued_batch(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: CountingSettingsManager,
) -> None:
    """With the window queue, the transcode runs on the batch queue and reports there."""
    repo, importer, file_ids = build_library(tmp_path, SMALL_LIBRARY)
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    dialogs = DialogRecorder(QMessageBox.StandardButton.Yes)
    dialogs.install(monkeypatch)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    batch = run_transcode_with_confirmation(
        window,
        repo,
        transcoder,
        file_ids,
        SettingsManager(tmp_path / "settings.json"),
        queue_batch=window.queue_batch,
    )

    assert batch is not None
    assert not isinstance(batch, TranscodeBatchSummary)
    assert batch.kind is BatchKind.TRANSCODE
    qtbot.waitUntil(lambda: batch.state is BatchState.COMPLETED, timeout=30_000)
    assert isinstance(batch.result, TranscodeBatchSummary)
    assert len(batch.result.results) == SMALL_LIBRARY
    assert "Transcoded: 2. Failed: 0." in format_batch_result(batch)
    assert dialogs.texts("information") == []
