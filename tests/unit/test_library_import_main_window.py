"""Behavioral tests for wiring LibraryImportDialog into WorkflowBar and MainWindow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import BatchKind
from infiniteprep.services.library_import import (
    FormatId,
    ImporterRegistry,
    ImportedTrack,
    ImportedValue,
    LibraryImportService,
    LocatedLibrary,
    NoImporterError,
    SourceLibrary,
)
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import library_import_dialog as dialog_module
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.library_import_dialog import LibraryImportDialog
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.workflow_bar import WorkflowBar
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.memory_settings import MemorySettingsManager


class _FakeImporter:
    """Minimal in-memory importer for wiring tests."""

    format_id = FormatId.TRAKTOR

    def __init__(self, tracks: tuple[ImportedTrack, ...]) -> None:
        self._tracks = tracks

    def read_library(self, source_path: Path) -> SourceLibrary:
        """Return a SourceLibrary containing the configured tracks."""
        return SourceLibrary(
            format_id=self.format_id,
            source_path=source_path,
            version="3.11.0",
            tracks=self._tracks,
        )


@pytest.fixture
def memory_settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep window settings in memory during MainWindow tests."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def _located(path: Path, format_id: str = FormatId.TRAKTOR) -> LocatedLibrary:
    """Return one LocatedLibrary pointing at path."""
    return LocatedLibrary(
        format_id=format_id,
        file_path=path,
        folder_path=path.parent,
        version="3.11.0",
        modified_at=1_700_000_000.0,
        observed=False,
        citation_url="https://example.invalid/traktor",
        citation_note="Test fixture",
    )


def test_workflow_bar_has_library_button(qtbot: Any) -> None:
    """WorkflowBar exposes an Import DJ library tool button that emits its signal."""
    bar = WorkflowBar()
    qtbot.addWidget(bar)
    emitted = CallRecorder(0)
    bar.import_library_requested.connect(emitted)

    bar.import_library_btn.click()

    assert len(emitted) == 1
    assert bar.import_library_btn.accessibleName() == "Import DJ library"


def test_busy_queue_locks_library_button(
    qtbot: Any, tmp_path: Path, memory_settings: MemorySettingsManager
) -> None:
    """MainWindow disables import_library_btn while the batch queue is busy."""
    repository = Repository(DatabaseManager(":memory:"))
    window = MainWindow(
        repository=repository,
        import_service=ImportService(repository, MetadataTagAdapter()),
        review_service=ReviewService(repository),
    )
    qtbot.addWidget(window)
    assert window.import_library_btn.isEnabled()

    gate = False

    def wait_until_released(is_cancelled: Any, report: Any) -> str:
        """Hold the worker briefly until cancelled or released."""
        while not gate and not is_cancelled():
            qtbot.wait(10)
        return "done"

    window.queue_batch(BatchKind.ANALYZE, "Hold", wait_until_released)
    qtbot.waitUntil(lambda: not window.import_library_btn.isEnabled())

    gate = True
    qtbot.waitUntil(lambda: window.import_library_btn.isEnabled())


def test_dialog_builds_plan_before_review(qtbot: Any, tmp_path: Path) -> None:
    """Advancing from Map to Review builds the plan and populates Review lists."""
    root = tmp_path / "music"
    root.mkdir()
    track_path = create_wav_fixture(root / "song.wav", duration_sec=0.5)
    library_file = tmp_path / "collection.nml"
    library_file.write_text("<NML/>", encoding="utf-8")

    repository = Repository(DatabaseManager(":memory:"))
    registry = ImporterRegistry()
    registry.register(
        _FakeImporter(
            tracks=(
                ImportedTrack(
                    source_id="t1",
                    source_path=str(track_path),
                    values=(ImportedValue("title", "Sunset"),),
                ),
                ImportedTrack(
                    source_id="t2",
                    source_path=str(root / "missing.wav"),
                    values=(),
                ),
            )
        )
    )
    service = LibraryImportService(repository, registry=registry)
    dialog = LibraryImportDialog(
        [_located(library_file)],
        readable_formats=registry.format_ids(),
        plan_builder=lambda lib, mappings, root_text: service.build_plan(
            main_window_module.LibraryImportRequest(
                source_path=lib.file_path,
                format_id=lib.format_id,
                root=Path(root_text),
                mappings=tuple(mappings),
            )
        ),
    )
    qtbot.addWidget(dialog)

    dialog.found_list.setCurrentRow(0)
    dialog.next_btn.click()
    dialog.root_edit.setText(str(root))
    dialog.next_btn.click()

    assert dialog.steps.currentIndex() == 2
    assert dialog.pending_result() is not None
    assert dialog.unresolved_list.count() == 1


def test_dialog_handles_missing_importer_error(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NoImporterError warns in plain text and keeps the dialog on the Map step."""
    library_file = tmp_path / "collection.nml"
    library_file.write_text("<NML/>", encoding="utf-8")
    shown: list[tuple[str, str]] = []

    def record_warning(
        parent: Any, icon: QMessageBox.Icon, title: str, text: str
    ) -> None:
        shown.append((title, text))

    monkeypatch.setattr(dialog_module, "show_plain_message", record_warning)

    def raise_missing(_lib: Any, _mappings: Any, _root: str) -> Any:
        raise NoImporterError("No importer reads the traktor format.")

    dialog = LibraryImportDialog(
        [_located(library_file)],
        readable_formats=(FormatId.TRAKTOR,),
        plan_builder=raise_missing,
    )
    qtbot.addWidget(dialog)

    dialog.found_list.setCurrentRow(0)
    dialog.next_btn.click()
    dialog.root_edit.setText(str(tmp_path))
    dialog.next_btn.click()

    assert dialog.steps.currentIndex() == 1
    assert dialog.pending_result() is None
    assert len(shown) == 1
    assert "No importer reads the traktor format." in shown[0][1]


def test_main_window_commits_library_import(
    qtbot: Any,
    tmp_path: Path,
    memory_settings: MemorySettingsManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MainWindow commits an accepted library import and refreshes the table."""
    root = tmp_path / "music"
    root.mkdir()
    track_path = create_wav_fixture(root / "imported.wav", duration_sec=0.5)
    library_file = tmp_path / "collection.nml"
    library_file.write_text("<NML/>", encoding="utf-8")

    repository = Repository(DatabaseManager(":memory:"))
    registry = ImporterRegistry()
    registry.register(
        _FakeImporter(
            tracks=(
                ImportedTrack(
                    source_id="t1",
                    source_path=str(track_path),
                    values=(ImportedValue("title", "Horizon"),),
                ),
            )
        )
    )
    service = LibraryImportService(repository, registry=registry)
    window = MainWindow(
        repository=repository,
        import_service=ImportService(repository, MetadataTagAdapter()),
        review_service=ReviewService(repository),
        library_import_service=service,
    )
    qtbot.addWidget(window)

    monkeypatch.setattr(
        main_window_module,
        "find_installed_libraries",
        lambda *_args, **_kwargs: [_located(library_file)],
    )

    def drive_dialog(self: LibraryImportDialog) -> int:
        self.found_list.setCurrentRow(0)
        self.next_btn.click()
        self.root_edit.setText(str(root))
        self.next_btn.click()
        self.commit_btn.click()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(LibraryImportDialog, "exec", drive_dialog)

    window.import_library_btn.click()

    assets = repository.list_files()
    assert len(assets) == 1
    assert window.file_table.rowCount() == 1
