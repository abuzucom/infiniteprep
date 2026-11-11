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


def test_zoom_paint_survives_near_zero_analyzed_duration(qtbot: Any) -> None:
    """A denormal analyzed duration cannot overflow the zoom sample index."""
    values = np.ones(SAMPLE_COUNT, dtype=np.float32)
    bar = WaveformOverviewBar()
    qtbot.addWidget(bar)
    bar.resize(BAR_WIDTH_PX, BAR_HEIGHT_PX)
    bar.spectrum = SpectrumWaveform(values, values, values, values, NEAR_ZERO_DURATION_SECONDS)
    bar.view_mode = ZOOM_MODE
    bar.set_playback(0.0, TRACK_DURATION_SECONDS)

    rendered = bar.grab()

    assert rendered.width() == BAR_WIDTH_PX
    assert rendered.height() == BAR_HEIGHT_PX
