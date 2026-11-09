"""Behavioral coverage for overview and zoom spectrum navigation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter, QPixmap

from infiniteprep.config.settings import DEFAULT_WAVEFORM_ZOOM_SECONDS, SettingsManager
from infiniteprep.services.waveform_analysis import SpectrumWaveform
from infiniteprep.ui.audio_player import AudioPreviewWidget

ANALYZED_FIXTURE_DURATION_SEC = 60.0
ANALYZED_FIXTURE_SAMPLE_COUNT = 600
TRACK_DURATION_SEC = 60.0
ANALYZED_DURATION_SEC = 30.0
WIDGET_WIDTH_PX = 640
WIDGET_HEIGHT_PX = 150
PAINT_WIDTH_PX = 80
PAINT_HEIGHT_PX = 44
MOUSE_CENTER_FRACTION = 0.5
MOUSE_RIGHT_QUARTER_FRACTION = 0.75
OVERVIEW_SEEK_SECONDS = 15.0
ZOOM_START_SECONDS = 30.0
ZERO_DURATION_FALLBACK_SECONDS = 10.0
ZERO_DURATION_PLAYHEAD_SECONDS = 3.0
FALLBACK_PEAK = 0.2
REFRESHED_FALLBACK_PEAK = 0.4
UPPER_FALLBACK_INDEX = 90
ANALYZED_PEAK_INDEX = 10
PIXEL_MID_Y = PAINT_HEIGHT_PX // 2
PIXEL_FALLBACK_X = 1
SPECTRUM_PEAK_SAMPLE_COUNT = 300
ANALYSIS_COLUMN_COUNT = SPECTRUM_PEAK_SAMPLE_COUNT
OVERVIEW_ENVELOPE_BIN_COUNT = 120
MEDIA_DURATION_MS = 12_000
MEDIA_DURATION_SECONDS = 12.0
SEEK_TOLERANCE_SECONDS = 0.1
PLAYHEAD_RATIO_TOLERANCE = 0.01
ZOOM_SELECTION_SECONDS = 16
MOUSE_QUARTER_FRACTION = 0.25
HIGH_FALLBACK_PEAK = 0.8
ZOOM_FALLBACK_START_SECONDS = 45.0
ZOOM_FALLBACK_END_SECONDS = 46.0
ZERO_DURATION_INTERVAL_START_SECONDS = 10.0
ZERO_DURATION_INTERVAL_END_SECONDS = 11.0


def make_waveform() -> SpectrumWaveform:
    """Build immutable spectrum data for navigation tests."""
    values = np.ones(ANALYZED_FIXTURE_SAMPLE_COUNT, dtype=np.float32)
    return SpectrumWaveform(values, values, values, values, ANALYZED_FIXTURE_DURATION_SEC)


def test_zoom_duration_persists_and_defaults_to_eight_seconds(qtbot: Any, tmp_path: Path) -> None:
    """The 8-second default and later zoom duration survive settings reloads."""
    manager = SettingsManager(tmp_path / "settings.json")
    widget = AudioPreviewWidget(settings_manager=manager)
    qtbot.addWidget(widget)

    assert widget.zoom_combo.currentText() == f"{DEFAULT_WAVEFORM_ZOOM_SECONDS} seconds"
    widget.zoom_combo.setCurrentText(f"{ZOOM_SELECTION_SECONDS} seconds")
    assert manager.load().waveform_zoom_seconds == ZOOM_SELECTION_SECONDS


def test_zoom_click_and_drag_seek_with_centered_playhead(qtbot: Any, tmp_path: Path) -> None:
    """Zoom click and drag seek within a centered time window."""
    manager = SettingsManager(tmp_path / "settings.json")
    widget = AudioPreviewWidget(settings_manager=manager)
    qtbot.addWidget(widget)
    widget.resize(WIDGET_WIDTH_PX, WIDGET_HEIGHT_PX)
    widget.show()
    qtbot.wait(20)
    widget.duration_sec = TRACK_DURATION_SEC
    widget.set_spectrum_waveform(make_waveform())
    qtbot.mouseClick(
        widget.waveform_bar,
        Qt.MouseButton.LeftButton,
        pos=QPoint(
            int(widget.waveform_bar.width() * MOUSE_QUARTER_FRACTION),
            int(widget.waveform_bar.height() * MOUSE_CENTER_FRACTION),
        ),
    )
    assert abs(widget.current_position_sec - OVERVIEW_SEEK_SECONDS) < SEEK_TOLERANCE_SECONDS
    widget.seek_to_seconds(ZOOM_START_SECONDS)
    widget.view_combo.setCurrentText("Zoom")
    widget.zoom_combo.setCurrentText(f"{DEFAULT_WAVEFORM_ZOOM_SECONDS} seconds")

    canvas = widget.waveform_bar
    qtbot.mouseClick(
        canvas,
        Qt.MouseButton.LeftButton,
        pos=QPoint(
            int(canvas.width() * MOUSE_RIGHT_QUARTER_FRACTION),
            int(canvas.height() * MOUSE_CENTER_FRACTION),
        ),
    )
    assert widget.current_position_sec > ZOOM_START_SECONDS
    qtbot.mousePress(
        canvas,
        Qt.MouseButton.LeftButton,
        pos=QPoint(
            int(canvas.width() * MOUSE_CENTER_FRACTION),
            int(canvas.height() * MOUSE_CENTER_FRACTION),
        ),
    )
    qtbot.mouseMove(
        canvas,
        pos=QPoint(
            int(canvas.width() * MOUSE_RIGHT_QUARTER_FRACTION),
            int(canvas.height() * MOUSE_CENTER_FRACTION),
        ),
    )
    qtbot.mouseRelease(
        canvas,
        Qt.MouseButton.LeftButton,
        pos=QPoint(
            int(canvas.width() * MOUSE_RIGHT_QUARTER_FRACTION),
            int(canvas.height() * MOUSE_CENTER_FRACTION),
        ),
    )
    assert widget.current_position_sec > ZOOM_START_SECONDS
    assert (
        abs(widget.waveform_bar.playhead_ratio - MOUSE_CENTER_FRACTION) < PLAYHEAD_RATIO_TOLERANCE
    )


def test_overview_remains_visible_without_spectrum_result(qtbot: Any) -> None:
    """Analysis state does not hide the usable overview waveform."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    widget.show()
    widget.waveform_bar.set_envelope([FALLBACK_PEAK, HIGH_FALLBACK_PEAK])
    widget.show_analysis_pending()

    assert widget.waveform_bar.isVisible()
    assert widget.waveform_bar.envelope == [FALLBACK_PEAK, HIGH_FALLBACK_PEAK]
    widget.show_analysis_failure("decode failed")
    assert widget.waveform_bar.isVisible()
    assert widget.waveform_bar.envelope == [FALLBACK_PEAK, HIGH_FALLBACK_PEAK]
    widget.show_status("Playback failed")
    widget.show_analysis_complete()
    assert widget.status_label.text() == "Playback failed"


def test_zero_duration_spectrum_can_render_in_zoom(qtbot: Any) -> None:
    """An empty analysis keeps the zoom paint path safe with a fallback duration."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    empty = np.array([], dtype=np.float32)
    widget.set_spectrum_waveform(SpectrumWaveform(empty, empty, empty, empty, 0.0))
    widget.duration_sec = ZERO_DURATION_FALLBACK_SECONDS
    widget.view_combo.setCurrentText("Zoom")
    widget.waveform_bar.set_playback(ZERO_DURATION_PLAYHEAD_SECONDS, ZERO_DURATION_FALLBACK_SECONDS)
    image = QPixmap(PAINT_WIDTH_PX, PAINT_HEIGHT_PX)
    painter = QPainter(image)

    widget.waveform_bar._paint_zoom(painter, PAINT_WIDTH_PX, PAINT_HEIGHT_PX)

    painter.end()
    assert widget.waveform_bar.duration_sec == ZERO_DURATION_FALLBACK_SECONDS


def test_zero_duration_zoom_fallback_paths_are_safe(qtbot: Any) -> None:
    """Zoom overview and capped fallback painting tolerate an unknown duration."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    widget.waveform_bar.view_mode = "Zoom"
    image = QPixmap(PAINT_WIDTH_PX, PAINT_HEIGHT_PX)
    painter = QPainter(image)

    widget.waveform_bar._paint_zoom_overview(painter, PAINT_WIDTH_PX, PAINT_HEIGHT_PX)
    widget.waveform_bar._paint_zoom_fallback_column(
        painter,
        PIXEL_FALLBACK_X,
        (ZERO_DURATION_INTERVAL_START_SECONDS, ZERO_DURATION_INTERVAL_END_SECONDS),
        PAINT_HEIGHT_PX,
    )

    painter.end()
    assert widget.waveform_bar.duration_sec == 0.0


def test_zoom_uses_fallback_after_analysis_duration_cap(qtbot: Any) -> None:
    """A capped analysis leaves later track regions navigable in zoom mode."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)
    widget.duration_sec = TRACK_DURATION_SEC
    widget.waveform_bar.set_playback(ZOOM_FALLBACK_START_SECONDS, TRACK_DURATION_SEC)
    widget.waveform_bar.set_envelope([FALLBACK_PEAK] * OVERVIEW_ENVELOPE_BIN_COUNT)
    peaks = np.ones(SPECTRUM_PEAK_SAMPLE_COUNT, dtype=np.float32)
    widget.set_spectrum_waveform(
        SpectrumWaveform(peaks, peaks, peaks, peaks, ANALYZED_DURATION_SEC)
    )
    widget.waveform_bar.set_envelope([REFRESHED_FALLBACK_PEAK] * OVERVIEW_ENVELOPE_BIN_COUNT)
    assert widget.waveform_bar.envelope[ANALYZED_PEAK_INDEX] == 1.0
    assert widget.waveform_bar.envelope[UPPER_FALLBACK_INDEX] == REFRESHED_FALLBACK_PEAK
    image = QPixmap(PAINT_WIDTH_PX, PAINT_HEIGHT_PX)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)

    widget.waveform_bar._paint_zoom_column(
        painter,
        PIXEL_FALLBACK_X,
        (PAINT_WIDTH_PX, PAINT_HEIGHT_PX),
        (
            ZOOM_FALLBACK_START_SECONDS,
            ZOOM_FALLBACK_END_SECONDS,
            ANALYSIS_COLUMN_COUNT,
            ANALYZED_DURATION_SEC,
            (),
        ),
    )

    painter.end()
    assert image.toImage().pixelColor(PIXEL_FALLBACK_X, PIXEL_MID_Y).name() == "#6b7280"


def test_backend_duration_updates_waveform_timebase(qtbot: Any) -> None:
    """A duration supplied after probing updates the waveform navigation range."""
    widget = AudioPreviewWidget()
    qtbot.addWidget(widget)

    widget._on_media_duration_changed(MEDIA_DURATION_MS)

    assert widget.duration_sec == MEDIA_DURATION_SECONDS
    assert widget.waveform_bar.duration_sec == MEDIA_DURATION_SECONDS
