"""Zoom painting tolerates an analysis with a near-zero duration."""

from __future__ import annotations

from typing import Any

import numpy as np

from infiniteprep.services.waveform_analysis import SpectrumWaveform
from infiniteprep.ui.audio_player import ZOOM_MODE, WaveformOverviewBar

SAMPLE_COUNT = 600
NEAR_ZERO_DURATION_SECONDS = 1e-320
TRACK_DURATION_SECONDS = 60.0
BAR_WIDTH_PX = 80
BAR_HEIGHT_PX = 44
PLAYHEAD_X = BAR_WIDTH_PX // 2
MID_Y = BAR_HEIGHT_PX // 2
PLAYHEAD_COLOR = "#f59e0b"
BACKGROUND_COLOR = "#181c24"


def test_zoom_paint_survives_near_zero_analyzed_duration(qtbot: Any) -> None:
    """A denormal analyzed duration cannot overflow the zoom sample index."""
    values = np.ones(SAMPLE_COUNT, dtype=np.float32)
    bar = WaveformOverviewBar()
    qtbot.addWidget(bar)
    bar.resize(BAR_WIDTH_PX, BAR_HEIGHT_PX)
    bar.spectrum = SpectrumWaveform(values, values, values, values, NEAR_ZERO_DURATION_SECONDS)
    bar.view_mode = ZOOM_MODE
    bar.set_playback(0.0, TRACK_DURATION_SECONDS)

    rendered = bar.grab().toImage()

    # The playhead is painted last, so its pixel proves the paint pass completed.
    assert rendered.pixelColor(PLAYHEAD_X, MID_Y).name() == PLAYHEAD_COLOR
    # Columns left of the playhead precede the track start and stay background.
    assert rendered.pixelColor(0, MID_Y).name() == BACKGROUND_COLOR
