"""The library filters reach every review status, search the shown cells, and track the theme."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, ReviewStatus
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.main_window import STATUS_FILTER_VALUES, MainWindow, format_status_label
from infiniteprep.ui.theme import get_theme_choices
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.memory_settings import MemorySettingsManager

MIN_THEMES_FOR_SWITCH = 2


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettingsManager:
    """Keep the window settings in memory so no test reads the user settings file."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    return manager


def build_window(
    qtbot: Any, tmp_path: Path, names: list[str]
) -> tuple[MainWindow, Repository, dict[str, str]]:
    """Import untagged WAV files and open a window over them."""
    repo = Repository(DatabaseManager(":memory:"))
    importer = ImportService(repo, MetadataTagAdapter())
    paths = [create_wav_fixture(tmp_path / "crate" / name, duration_sec=0.5) for name in names]
    report = importer.import_paths(paths)
    ids = {Path(asset.current_path).name: asset.id for asset in report.imported_files}
    window = MainWindow(
        repository=repo, import_service=importer, review_service=ReviewService(repo)
    )
    qtbot.addWidget(window)
    return window, repo, ids


def visible_names(window: MainWindow) -> list[str]:
    """Return the file names in table order."""
    return [window.file_table.item(row, 0).text() for row in range(window.file_table.rowCount())]


def test_status_filter_offers_every_review_status() -> None:
    """Each review status has one filter entry, with readable labels."""
    assert set(STATUS_FILTER_VALUES.values()) == {status.value for status in ReviewStatus}
    assert format_status_label(ReviewStatus.READY_FOR_REVIEW) == "Ready for Review"
    assert format_status_label(ReviewStatus.READY_TO_WRITE) == "Ready to Write"


def test_status_filter_shows_a_pipeline_status(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A file the pipeline marks no_match appears under its own filter entry."""
    window, repo, ids = build_window(qtbot, tmp_path, ["matched.wav", "unmatched.wav"])
    repo.update_file_status(ids["unmatched.wav"], review_status=ReviewStatus.NO_MATCH)

    window.status_filter_combo.setCurrentText(format_status_label(ReviewStatus.NO_MATCH))

    assert visible_names(window) == ["unmatched.wav"]


def test_search_ignores_internal_key_names(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """A word from the stored tag structure no longer matches every file."""
    window, _repo, _ids = build_window(qtbot, tmp_path, ["a.wav", "b.wav"])

    window.search_input.setText("standard_tags")

    assert visible_names(window) == []


def test_search_matches_an_approved_value_the_table_shows(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """An approved artist shown in the table is searchable, though no file tag holds it."""
    window, _repo, ids = build_window(qtbot, tmp_path, ["a.wav", "b.wav"])
    window.review_service.set_manual_field_value(ids["b.wav"], FieldName.ARTIST.value, "Sasha")
    window.refresh_file_table()

    window.search_input.setText("sasha")

    assert visible_names(window) == ["b.wav"]


def test_reload_settings_moves_the_theme_picker_to_the_stored_theme(
    qtbot: Any, tmp_path: Path, settings: MemorySettingsManager
) -> None:
    """After the Settings dialog changes the theme, the toolbar picker shows that theme."""
    choices = get_theme_choices()
    if len(choices) < MIN_THEMES_FOR_SWITCH:
        pytest.skip("This build offers one theme only.")
    window, _repo, _ids = build_window(qtbot, tmp_path, ["a.wav"])
    other_theme = next(theme for theme in choices if theme != window.theme_combo.currentData())
    stored = settings.load()
    stored.theme = other_theme
    settings.save(stored)

    window.reload_settings()

    assert window.theme_combo.currentData() == other_theme
