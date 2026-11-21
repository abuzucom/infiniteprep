"""A stand-in for waveform analysis in tests that cover other detail panel behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.ui.waveform_worker import WaveformAnalysisPool

# The pool and task signals emit (file_id, generation, payload). The payload holds
# the analysis result, the overview data, or the failure message.
SIGNAL_FILE_ID_INDEX = 0
SIGNAL_GENERATION_INDEX = 1
SIGNAL_PAYLOAD_INDEX = 2


def skip_waveform_analysis(
    pool: WaveformAnalysisPool,
    path: Path,
    file_id: str,
    generation: int,
    fake_lossless_method: str | None = None,
) -> None:
    """Accept a waveform job and queue nothing, so no worker thread decodes audio.

    The parameter names match WaveformAnalysisPool.submit, because callers pass
    fake_lossless_method by keyword.
    """
    _ = (pool, path, file_id, generation, fake_lossless_method)


def keep_waveform_pool_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the pool's submit with the idle stand-in for the current test."""
    monkeypatch.setattr(WaveformAnalysisPool, "submit", skip_waveform_analysis)
