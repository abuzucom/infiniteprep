"""Behavioral tests for text and graphic contrast and the spectrum band legend."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.audio_player import (
    OVERVIEW_MODE,
    PLAYED_BAR_COLOR,
    PLAYHEAD_COLOR,
    SPECTRUM_BAND_COLORS,
    SPECTRUM_OVERLAY_ALPHA,
    UNPLAYED_BAR_COLOR,
    WAVEFORM_BACKGROUND_COLOR,
    ZOOM_MODE,
    AudioPreviewWidget,
)
from infiniteprep.ui.contrast import (
    GRAPHIC_CONTRAST_MINIMUM,
    TEXT_CONTRAST_MINIMUM,
    blend_over,
    contrast_ratio,
    warning_text_color,
)
from infiniteprep.ui.detail_panel import DetailPanel

LIGHT_BACKGROUNDS: tuple[str, ...] = ("#ffffff", "#f0f0f0", "#fafafa")
DARK_BACKGROUNDS: tuple[str, ...] = ("#181c24", "#232629", "#31363b")
DARK_THEME_SHEET: str = "QWidget { background-color: #31363b; color: #ffffff; }"
MAX_CONTRAST: float = 21.0


@pytest.fixture
def dark_app_sheet(qapp: QApplication) -> Iterator[None]:
    """Apply a dark application style sheet and restore the previous one afterward."""
    previous = qapp.styleSheet()
    qapp.setStyleSheet(DARK_THEME_SHEET)
    yield
    qapp.setStyleSheet(previous)


def label_contrast(label: QLabel) -> float:
    """Return the contrast of a label's text color against its own background."""
    label.ensurePolished()
    palette = label.palette()
    text = palette.color(QPalette.ColorRole.WindowText)
    background = palette.color(QPalette.ColorRole.Window)
    return contrast_ratio(text, background)


def test_contrast_ratio_spans_one_to_twenty_one() -> None:
    """Black on white gives 21:1, and a color against itself gives 1:1."""
    assert contrast_ratio(QColor("#000000"), QColor("#ffffff")) == pytest.approx(MAX_CONTRAST)
    assert contrast_ratio(QColor("#3b82f6"), QColor("#3b82f6")) == pytest.approx(1.0)


@pytest.mark.parametrize("background", LIGHT_BACKGROUNDS + DARK_BACKGROUNDS)
def test_warning_color_reaches_text_contrast_on_every_theme(background: str) -> None:
    """The chosen warning amber reaches 4.5:1 on light and dark theme backgrounds."""
    surface = QColor(background)

    assert contrast_ratio(warning_text_color(surface), surface) >= TEXT_CONTRAST_MINIMUM


def test_warning_label_reaches_text_contrast_on_the_native_theme(qtbot: Any) -> None:
    """The warnings label text reaches 4.5:1 on its own background in the default theme."""
    repository = Repository(DatabaseManager(":memory:"))
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)

    assert label_contrast(panel.warnings_label) >= TEXT_CONTRAST_MINIMUM


def test_warning_label_follows_a_dark_theme_sheet(qtbot: Any, dark_app_sheet: None) -> None:
    """Under a dark theme sheet the warnings label switches amber and keeps 4.5:1."""
    repository = Repository(DatabaseManager(":memory:"))
    panel = DetailPanel(repository, ReviewService(repository))
    qtbot.addWidget(panel)

    assert label_contrast(panel.warnings_label) >= TEXT_CONTRAST_MINIMUM


@pytest.mark.parametrize("color", (PLAYED_BAR_COLOR, UNPLAYED_BAR_COLOR, PLAYHEAD_COLOR))
def test_waveform_marks_reach_graphic_contrast(color: str) -> None:
    """Played bars, unplayed bars, and the playhead reach 3:1 on the waveform background."""
    background = QColor(WAVEFORM_BACKGROUND_COLOR)

    assert contrast_ratio(QColor(color), background) >= GRAPHIC_CONTRAST_MINIMUM


@pytest.mark.parametrize("band", tuple(SPECTRUM_BAND_COLORS))
def test_translucent_spectrum_bands_reach_graphic_contrast(band: str) -> None:
    """Each band, drawn at the overlay alpha, reaches 3:1 on the waveform background."""
    background = QColor(WAVEFORM_BACKGROUND_COLOR)
    color = QColor(SPECTRUM_BAND_COLORS[band])
    color.setAlpha(SPECTRUM_OVERLAY_ALPHA)

    assert contrast_ratio(blend_over(color, background), background) >= GRAPHIC_CONTRAST_MINIMUM


def test_spectrum_legend_names_each_band_in_zoom_view_only(qtbot: Any) -> None:
    """The zoom view shows a legend that names every band. The overview hides it."""
    player = AudioPreviewWidget()
    qtbot.addWidget(player)
    legend_texts = [label.text() for label in player.spectrum_legend.findChildren(QLabel)]
    assert player.spectrum_legend.isHidden()

    player.view_combo.setCurrentText(ZOOM_MODE)
    shown_in_zoom = not player.spectrum_legend.isHidden()
    player.view_combo.setCurrentText(OVERVIEW_MODE)

    assert shown_in_zoom
    assert player.spectrum_legend.isHidden()
    assert [text for text in legend_texts if text] == list(SPECTRUM_BAND_COLORS)
