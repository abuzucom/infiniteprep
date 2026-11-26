"""Tests that a crafted audio header cannot claim unbounded decode memory or analysis time."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from infiniteprep.adapters.analyzers import local_dsp
from infiniteprep.adapters.analyzers.local_dsp import (
    MAX_DSP_CHANNELS,
    LocalDspAnalyzer,
    estimate_bpm_from_onset_envelope,
    read_bounded_mono_pcm,
)
from infiniteprep.adapters.transcoder import safe_transcoder
from infiniteprep.adapters.transcoder.safe_transcoder import (
    MAX_TRANSCODE_CHANNELS,
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
    TranscodeResult,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.transcode_batch import transcode_files

SAMPLE_RATE = 8_000
ONE_SECOND = SAMPLE_RATE
CLICK_TRACK_RATE = 1_000
CLICK_TRACK_SECONDS = 600
CLICK_PERIOD_SAMPLES = CLICK_TRACK_RATE // 2
CLICK_TRACK_BPM = 120.0
TEMPO_TIME_LIMIT_SECONDS = 3.0
SMALL_SAMPLE_BUDGET = 1_000


def _write_wav(path: Path, channels: int, frames: int = ONE_SECOND) -> Path:
    """Write a PCM WAV whose channels each hold a different constant level."""
    levels = np.linspace(0.1, 0.5, channels)
    sf.write(str(path), np.tile(levels, (frames, 1)), SAMPLE_RATE, subtype="PCM_16")
    return path


def _transcoder(tmp_path: Path) -> SafeTranscoderAdapter:
    """Return a transcoder over an empty in-memory library."""
    return SafeTranscoderAdapter(
        Repository(DatabaseManager(":memory:")), backup_dir=tmp_path / "backups"
    )


def test_analyzer_decode_averages_channels(tmp_path: Path) -> None:
    """The block decode returns the same mono mix as a whole-file decode."""
    path = _write_wav(tmp_path / "stereo.wav", channels=2, frames=ONE_SECOND * 3)
    whole, _ = sf.read(str(path), dtype="float64", always_2d=True)

    mono, sample_rate = read_bounded_mono_pcm(path)

    assert sample_rate == SAMPLE_RATE
    assert np.array_equal(mono, np.mean(whole, axis=1))


def test_analyzer_decode_stops_duration_limit(tmp_path: Path) -> None:
    """A duration limit shorter than the file caps the decoded frame count."""
    path = _write_wav(tmp_path / "long.wav", channels=2, frames=ONE_SECOND * 3)

    mono, _ = read_bounded_mono_pcm(path, max_duration_sec=1.0)

    assert mono.size == ONE_SECOND


def test_analyzer_rejects_excess_channels(tmp_path: Path) -> None:
    """A header that declares more channels than the cap raises before any decode."""
    path = _write_wav(tmp_path / "wide.wav", channels=MAX_DSP_CHANNELS + 1)

    with pytest.raises(ValueError, match="Channel count"):
        read_bounded_mono_pcm(path)


def test_analyzer_reports_rejected_file(tmp_path: Path) -> None:
    """The analyzer returns a failure result with a warning for a rejected file."""
    path = _write_wav(tmp_path / "wide.wav", channels=MAX_DSP_CHANNELS + 1)

    result = LocalDspAnalyzer().analyze(path, file_id="wide")

    assert result.bpm is None
    assert "Audio decode failed" in result.warnings[0]


def test_analyzer_reports_out_of_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A decode that runs out of memory yields a failure result, not an exception."""
    path = _write_wav(tmp_path / "stereo.wav", channels=2)

    def raise_memory_error(_path: Path) -> None:
        raise MemoryError("decode buffer")

    monkeypatch.setattr(local_dsp, "read_bounded_mono_pcm", raise_memory_error)

    result = LocalDspAnalyzer().analyze(path, file_id="stereo")

    assert result.bpm is None
    assert "Audio decode failed" in result.warnings[0]


def test_long_track_tempo_within_limit() -> None:
    """Ten minutes of clicks at 120 BPM resolve to 120.0 well inside the time limit."""
    mono = np.zeros(CLICK_TRACK_RATE * CLICK_TRACK_SECONDS, dtype=np.float64)
    mono[::CLICK_PERIOD_SAMPLES] = 1.0

    started = time.perf_counter()
    bpm = estimate_bpm_from_onset_envelope(mono, CLICK_TRACK_RATE)
    elapsed = time.perf_counter() - started

    assert bpm == CLICK_TRACK_BPM
    assert elapsed < TEMPO_TIME_LIMIT_SECONDS


def test_transcoder_rejects_excess_channels(tmp_path: Path) -> None:
    """The bounds check refuses a file that declares more channels than the cap."""
    path = _write_wav(tmp_path / "wide.wav", channels=MAX_TRANSCODE_CHANNELS + 1)

    with pytest.raises(ValueError, match="channels"):
        _transcoder(tmp_path)._verify_audio_bounds(path)


def test_transcoder_rejects_oversized_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bounds check refuses a file whose frames times channels exceed the sample budget."""
    path = _write_wav(tmp_path / "stereo.wav", channels=2)
    monkeypatch.setattr(safe_transcoder, "MAX_TRANSCODE_DECODED_SAMPLES", SMALL_SAMPLE_BUDGET)

    with pytest.raises(ValueError, match="samples"):
        _transcoder(tmp_path)._verify_audio_bounds(path)


def test_transcoder_accepts_stereo_file(tmp_path: Path) -> None:
    """A short stereo file passes the bounds check."""
    path = _write_wav(tmp_path / "stereo.wav", channels=2)

    _transcoder(tmp_path)._verify_audio_bounds(path)


class _MemoryStarvedTranscoder:
    """Stands in for the transcoder and runs out of memory on the first file only."""

    def __init__(self, output_path: Path) -> None:
        """Store the output path that the second file reports."""
        self._output_path = output_path
        self._calls = 0

    def transcode_file(
        self, file_id: str, *, preset: TranscodePreset, mode: TranscodeMode
    ) -> TranscodeResult:
        """Raise MemoryError for the first call and return a result for every later call."""
        self._calls += 1
        if self._calls == 1:
            raise MemoryError("decode buffer")
        return TranscodeResult(file_id, self._output_path, self._output_path, SAMPLE_RATE, "PCM_16")


def test_batch_continues_after_memory_error(tmp_path: Path) -> None:
    """The batch lists the out-of-memory file as a failure and still converts the next file."""
    transcoder = _MemoryStarvedTranscoder(tmp_path / "out.wav")

    summary = transcode_files(
        transcoder,
        ["first", "second"],
        preset=TranscodePreset.XDJ_LOSSLESS_AUTO,
        mode=TranscodeMode.SIBLING_EXPORT,
    )

    assert list(summary.failures) == ["first"]
    assert summary.failures["first"].startswith("MemoryError")
    assert [result.file_id for result in summary.results] == ["second"]
