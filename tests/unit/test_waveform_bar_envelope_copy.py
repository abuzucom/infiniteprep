"""The waveform bar owns its fallback envelope independently of the caller's list."""

from __future__ import annotations

from typing import Any

from infiniteprep.ui.audio_player import WaveformOverviewBar

ORIGINAL_PEAKS = [0.2, 0.8]
MUTATED_PEAK = 0.5


def test_caller_mutation_does_not_change_the_fallback_envelope(qtbot: Any) -> None:
    """A later change to the passed list leaves the stored overview intact."""
    bar = WaveformOverviewBar()
    qtbot.addWidget(bar)
    peaks = list(ORIGINAL_PEAKS)

    bar.set_envelope(peaks)
    peaks[0] = MUTATED_PEAK

    assert bar.fallback_envelope == ORIGINAL_PEAKS
    assert bar.envelope == ORIGINAL_PEAKS
