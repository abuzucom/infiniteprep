"""Behavioral coverage for overview and zoom spectrum navigation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter, QPixmap

from infiniteprep.config.settings import SettingsManager
from infiniteprep.services.waveform_analysis import SpectrumWaveform
from infiniteprep.ui.audio_player import AudioPreviewWidget


def make_waveform() -> SpectrumWaveform:
    """Build immutable spectrum data for navigation tests."""
    values = np.ones(600, dtype=np.float32)
    return SpectrumWaveform(values, values, values, values, 60.0)


def test_zoom_duration_persists_and_defaults_to_eight_seconds(qtbot: Any, tmp_path: Path) -> None:
    """The 8-second default and later zoom duration survive settings reloads."""
    manager = SettingsManager(tmp_path / "settings.json")
    widget = AudioPreviewWidget(settings_manager=manager)
    qtbot.addWidget(widget)

    assert widget.zoom_combo.currentText() == "8 seconds"
    widget.zoom_combo.setCurrentText("16 seconds")
    assert manager.load().waveform_zoom_seconds == 16


def test_zoom_click_and_drag_seek_with_centered_playhead(qtbot: Any, tmp_path: Path) -> None:
    """Zoom click and drag seek within a centered time window."""
    manager = SettingsManager(tmp_path / "settings.json")
    widget = AudioPreviewWidget(settings_manager=manager)
    qtbot.addWidget(widget)
    widget.resize(640, 150)
    widget.show()
    qtbot.wait(20)
    widget.duration_sec = 60.0
    widget.set_spectrum_waveform(make_waveform())
    qtbot.mouseClick(
        widget.waveform_bar,
        Qt.MouseButton.LeftButton,
        pos=QPoint(widget.waveform_bar.width() // 4, widget.waveform_bar.height() // 2),
    )
    assert abs(widget.current_position_sec - 15.0) < 0.1
    widget.seek_to_seconds(30.0)
    widget.view_combo.setCurrentText("Zoom")
    widget.zoom_combo.setCurrentText("8 seconds")

    canvas = widget.waveform_bar
    qtbot.mouseClick(
        canvas,
        Qt.MouseButton.LeftButton,
        pos=QPoint(canvas.width() * 3 // 4, canvas.height() // 2),
    )
    assert widget.current_position_sec > 30.0
    qtbot.mousePress(
        canvas,
        Qt.MouseButton.LeftButton,
        pos=QPoint(canvas.width() // 2, canvas.height() // 2),
    )
    qtbot.mouseMove(canvas, pos=QPoint(canvas.width() * 3 // 4, canvas.height() // 2))
    qtbot.mouseRelease(
        canvas,
        Qt.MouseButton.LeftButton,
        pos=QPoint(canvas.width() * 3 // 4, canvas.height() // 2),
    )
    assert widget.current_position_sec > 30.0
    assert abs(widget.waveform_bar.playhead_ratio - 0.5) < 0.01


def test_overview_remains_visible_without_spectrum_result(qtbot: Any) -> None:
    """Analysis state does not hide the usable overview waveform."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    widget.show()
    widget.waveform_bar.set_envelope([0.2, 0.8])
    widget.show_analysis_pending()

    assert widget.waveform_bar.isVisible()
    assert widget.waveform_bar.envelope == [0.2, 0.8]
    widget.show_analysis_failure("decode failed")
    assert widget.waveform_bar.isVisible()
    assert widget.waveform_bar.envelope == [0.2, 0.8]
    widget.show_status("Playback failed")
    widget.show_analysis_complete()
    assert widget.status_label.text() == "Playback failed"


def test_zero_duration_spectrum_can_render_in_zoom(qtbot: Any) -> None:
    """An empty analysis keeps the zoom paint path safe with a fallback duration."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    empty = np.array([], dtype=np.float32)
    widget.set_spectrum_waveform(SpectrumWaveform(empty, empty, empty, empty, 0.0))
    widget.duration_sec = 10.0
    widget.view_combo.setCurrentText("Zoom")
    widget.waveform_bar.set_playback(3.0, 10.0)
    image = QPixmap(80, 44)
    painter = QPainter(image)

    widget.waveform_bar._paint_zoom(painter, 80, 44)

    painter.end()
    assert widget.waveform_bar.duration_sec == 10.0


def test_backend_duration_updates_waveform_timebase(qtbot: Any) -> None:
    """A duration supplied after probing updates the waveform navigation range."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)

    widget._on_media_duration_changed(12_000)

    assert widget.duration_sec == 12.0
    assert widget.waveform_bar.duration_sec == 12.0
