"""The zoom selector reads its duration from item data, not from display text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep.config.settings import SUPPORTED_WAVEFORM_ZOOM_SECONDS, SettingsManager
from infiniteprep.ui.audio_player import AudioPreviewWidget

RELABELED_ZOOM_TEXT = "Wide"


def test_zoom_selection_persists_when_the_label_has_no_number(qtbot: Any, tmp_path: Path) -> None:
    """A relabeled zoom entry still selects and persists its duration."""
    manager = SettingsManager(tmp_path / "settings.json")
    widget = AudioPreviewWidget(settings_manager=manager)
    qtbot.addWidget(widget)
    last_index = widget.zoom_combo.count() - 1
    widget.zoom_combo.setItemText(last_index, RELABELED_ZOOM_TEXT)

    widget.zoom_combo.setCurrentIndex(last_index)

    expected_seconds = SUPPORTED_WAVEFORM_ZOOM_SECONDS[-1]
    assert widget.waveform_zoom_seconds == expected_seconds
    assert widget.waveform_bar.zoom_seconds == expected_seconds
    assert manager.load().waveform_zoom_seconds == expected_seconds
