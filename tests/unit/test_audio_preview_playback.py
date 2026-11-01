"""Tests for audio preview error reporting, keyboard seeking, and backend wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QWidget

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import detail_panel as detail_panel_module
from infiniteprep.ui.audio_player import (
    MS_PER_SECOND,
    POSITION_SLIDER_NAME,
    SLIDER_PAGE_STEP_MS,
    SLIDER_SINGLE_STEP_MS,
    AudioPreviewWidget,
    format_clock,
)
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_SECONDS = 30.0


@pytest.fixture
def preview(qtbot: Any, tmp_path: Path) -> AudioPreviewWidget:
    """Load a 30 s WAV into a silent preview widget."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    widget.load_file(create_wav_fixture(tmp_path / "track.wav", duration_sec=TRACK_SECONDS))
    return widget


def test_the_slider_is_named_and_spans_the_track(preview: AudioPreviewWidget) -> None:
    """Assistive technology finds the slider by name, and its range covers the track."""
    slider = preview.position_slider

    assert slider.accessibleName() == POSITION_SLIDER_NAME
    assert slider.maximum() == pytest.approx(TRACK_SECONDS * MS_PER_SECOND, abs=1)
    assert slider.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_arrow_and_page_keys_seek_by_one_and_ten_seconds(
    qtbot: Any, preview: AudioPreviewWidget
) -> None:
    """Arrow keys move 1 s, Page keys move 10 s, and the accessible time follows."""
    slider = preview.position_slider
    slider.setFocus()

    qtbot.keyClick(slider, Qt.Key.Key_Right)
    assert preview.current_position_sec == pytest.approx(SLIDER_SINGLE_STEP_MS / MS_PER_SECOND)

    qtbot.keyClick(slider, Qt.Key.Key_PageUp)
    expected = (SLIDER_SINGLE_STEP_MS + SLIDER_PAGE_STEP_MS) / MS_PER_SECOND
    assert preview.current_position_sec == pytest.approx(expected)
    assert slider.accessibleDescription() == format_clock(expected)


def test_programmatic_seeks_move_the_slider_without_feedback(
    preview: AudioPreviewWidget,
) -> None:
    """A cue jump moves the slider once, and the slider does not seek back on its own."""
    seeks: list[int] = []
    preview.position_slider.valueChanged.connect(seeks.append)

    preview.jump_to_cue("50% Drop")

    assert preview.position_slider.value() == int(preview.current_position_sec * MS_PER_SECOND)
    assert seeks == []


def test_a_player_error_shows_plain_text_and_resets_play(
    preview: AudioPreviewWidget,
) -> None:
    """A playback failure appears as plain text, and the button returns to Play."""
    preview.play_btn.setText("Pause")

    preview._on_player_error(QMediaPlayer.Error.FormatError, "<b>decoder missing</b>")

    assert preview.status_label.textFormat() == Qt.TextFormat.PlainText
    assert "Playback failed: <b>decoder missing</b>" in preview.status_label.text()
    assert preview.play_btn.text() == "Play"


def test_loading_a_file_clears_an_old_error(preview: AudioPreviewWidget, tmp_path: Path) -> None:
    """A new track starts with an empty status line."""
    preview._on_player_error(QMediaPlayer.Error.ResourceError, "gone")

    preview.load_file(create_wav_fixture(tmp_path / "next.wav", duration_sec=1.0))

    assert preview.status_label.text() == ""


def test_the_main_window_passes_the_audio_backend_flag(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enable_audio_backend reaches the preview widget without starting a real player."""
    received: list[bool] = []

    def recording_preview(
        parent: QWidget | None = None, enable_multimedia_backend: bool = False
    ) -> AudioPreviewWidget:
        received.append(enable_multimedia_backend)
        return AudioPreviewWidget(parent)

    monkeypatch.setattr(detail_panel_module, "AudioPreviewWidget", recording_preview)
    repo = Repository(DatabaseManager(":memory:"))

    window = MainWindow(
        repository=repo,
        import_service=ImportService(repo),
        review_service=ReviewService(repo),
        enable_audio_backend=True,
    )
    qtbot.addWidget(window)

    assert received == [True]
