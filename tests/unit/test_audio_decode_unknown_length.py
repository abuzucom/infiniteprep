"""Tests that an audio stream of unknown length decodes into one mono buffer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from infiniteprep.adapters.analyzers import local_dsp
from infiniteprep.adapters.analyzers.local_dsp import read_bounded_mono_pcm
from tests.fixtures.stereo_wav import STEREO_WAV_SAMPLE_RATE, write_stereo_wav

SAMPLE_RATE = STEREO_WAV_SAMPLE_RATE
ONE_SECOND = SAMPLE_RATE
STEREO = 2
SMALL_FRAME_CAP = 1_000


def _report_unknown_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the header probe report no frame count, as some streams do."""

    def unknown_length_info(_path: str) -> SimpleNamespace:
        return SimpleNamespace(samplerate=SAMPLE_RATE, channels=STEREO, frames=0)

    monkeypatch.setattr(local_dsp.sf, "info", unknown_length_info)


def _reject_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test when the decode joins chunks and holds the mono mix twice."""

    def reject(_chunks: object) -> None:
        pytest.fail("The decode joined chunks and held the mono mix twice.")

    monkeypatch.setattr(local_dsp.np, "concatenate", reject)


def test_unknown_length_full_decode_no_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream without a declared frame count decodes whole into one buffer."""
    path = write_stereo_wav(tmp_path / "stereo.wav", ONE_SECOND * 3)
    whole, _ = sf.read(str(path), dtype="float64", always_2d=True)
    expected = np.mean(whole, axis=1)
    _report_unknown_length(monkeypatch)
    _reject_join(monkeypatch)

    mono, sample_rate = read_bounded_mono_pcm(path)

    assert sample_rate == SAMPLE_RATE
    assert np.array_equal(mono, expected)


def test_unknown_length_stream_stops_at_the_frame_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frame cap bounds the counted length of a stream without a declared frame count."""
    path = write_stereo_wav(tmp_path / "stereo.wav", ONE_SECOND)
    _report_unknown_length(monkeypatch)
    _reject_join(monkeypatch)
    monkeypatch.setattr(local_dsp, "MAX_DSP_MONO_FRAMES", SMALL_FRAME_CAP)

    mono, _ = read_bounded_mono_pcm(path)

    assert mono.size == SMALL_FRAME_CAP
