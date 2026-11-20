"""A stand-in for waveform analysis in tests that cover other detail panel behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.ui.waveform_worker import WaveformAnalysisPool


def skip_waveform_analysis(
    _pool: WaveformAnalysisPool,
    _path: Path,
    _file_id: str,
    _generation: int,
    _fake_lossless_method: str | None = None,
) -> None:
    """Accept a waveform job and queue nothing, so no worker thread decodes audio."""


def keep_waveform_pool_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the pool's submit with the idle stand-in for the current test."""
    monkeypatch.setattr(WaveformAnalysisPool, "submit", skip_waveform_analysis)
