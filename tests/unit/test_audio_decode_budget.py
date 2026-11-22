"""Tests for the analyzer decode buffer, the analyzer frame cap, and the transcoder rate cap."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from infiniteprep.adapters.analyzers import local_dsp
from infiniteprep.adapters.analyzers.local_dsp import read_bounded_mono_pcm
from infiniteprep.adapters.transcoder import safe_transcoder
from infiniteprep.adapters.transcoder.safe_transcoder import SafeTranscoderAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from tests.fixtures.stereo_wav import STEREO_WAV_SAMPLE_RATE, write_stereo_wav

SAMPLE_RATE = STEREO_WAV_SAMPLE_RATE
ONE_SECOND = SAMPLE_RATE
SMALL_FRAME_CAP = 1_000
RATE_CAP_BELOW_FIXTURE = SAMPLE_RATE - 1


def test_known_length_decode_never_joins_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream of known length fills one buffer, so the mono mix never exists twice."""
    path = write_stereo_wav(tmp_path / "stereo.wav", ONE_SECOND * 3)
    whole, _ = sf.read(str(path), dtype="float64", always_2d=True)
    expected = np.mean(whole, axis=1)

    def reject_join(_chunks: object) -> None:
        pytest.fail("The decode joined chunks and held the mono mix twice.")

    monkeypatch.setattr(local_dsp.np, "concatenate", reject_join)

    mono, _ = read_bounded_mono_pcm(path)

    assert np.array_equal(mono, expected)


def test_short_stream_returns_only_the_decoded_frames(tmp_path: Path) -> None:
    """A buffer larger than the stream shrinks to the decoded frames and owns its memory."""
    path = write_stereo_wav(tmp_path / "stereo.wav", ONE_SECOND)

    mono = local_dsp._decode_mono_into_buffer(path, ONE_SECOND * 2)

    assert mono.size == ONE_SECOND
    assert mono.base is None


def test_analyzer_decode_stops_at_the_frame_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mono frame cap limits the decode below the duration limit."""
    path = write_stereo_wav(tmp_path / "stereo.wav", ONE_SECOND)
    monkeypatch.setattr(local_dsp, "MAX_DSP_MONO_FRAMES", SMALL_FRAME_CAP)

    mono, _ = read_bounded_mono_pcm(path)

    assert mono.size == SMALL_FRAME_CAP


def test_transcoder_rejects_high_sample_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bounds check refuses a file whose sample rate exceeds the transcoder cap."""
    path = write_stereo_wav(tmp_path / "stereo.wav", ONE_SECOND)
    monkeypatch.setattr(safe_transcoder, "MAX_TRANSCODE_SAMPLE_RATE_HZ", RATE_CAP_BELOW_FIXTURE)
    transcoder = SafeTranscoderAdapter(
        Repository(DatabaseManager(":memory:")), backup_dir=tmp_path / "backups"
    )

    with pytest.raises(ValueError, match="sample rate"):
        transcoder._verify_audio_bounds(path)
