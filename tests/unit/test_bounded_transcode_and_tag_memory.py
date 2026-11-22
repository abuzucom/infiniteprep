"""Tests for block transcoding, the raw tag text cap, the Ogg file map, and analyzer rate caps."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from infiniteprep.adapters.analyzers import local_dsp
from infiniteprep.adapters.analyzers.local_dsp import read_bounded_mono_pcm
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MAX_RAW_TAG_TEXT_CHARS,
    RAW_TAG_SUMMARY_PREFIX_CHARS,
    MetadataTagAdapter,
    bound_raw_tag_value,
)
from infiniteprep.adapters.metadata_io.ogg_reader import OggFormatError, read_ogg
from infiniteprep.adapters.transcoder.block_resampler import (
    count_source_frames,
    iter_resampled_blocks,
    output_frame_count,
    resample_ratio,
)
from infiniteprep.adapters.transcoder.safe_transcoder import (
    RESAMPLE_HEADROOM,
    write_resampled_audio,
)
from infiniteprep.domain.similarity import MAX_SIMILARITY_CHARS, string_similarity
from tests.fixtures.ogg_bytes import make_synthetic_vorbis_ogg

SIGNAL_SECONDS = 1.3
# A block far below the default forces many block edges inside the test signal.
SMALL_BLOCK_FRAMES = 4_096
EQUALITY_TOLERANCE = 1e-9
# One step of 24-bit PCM, the quantization of the written test files.
PCM_24_STEP = 2.0**-23
RATE_PAIRS = [(96_000, 48_000), (44_100, 48_000), (48_000, 44_100), (88_200, 44_100)]
LONG_TAG_CHARS = MAX_RAW_TAG_TEXT_CHARS * 3
RANDOM_SEED = 2026
SIGNAL_AMPLITUDE = 0.6
STEREO = 2


def _random_signal(rate: int) -> np.ndarray:
    """Return a reproducible stereo noise signal of SIGNAL_SECONDS at rate."""
    generator = np.random.default_rng(RANDOM_SEED)
    frames = round(rate * SIGNAL_SECONDS)
    return generator.uniform(-SIGNAL_AMPLITUDE, SIGNAL_AMPLITUDE, size=(frames, STEREO))


def _write_float_wav(path: Path, signal: np.ndarray, rate: int) -> Path:
    """Write signal as a 64-bit float WAV, so the read returns the exact samples."""
    sf.write(str(path), signal, rate, subtype="DOUBLE")
    return path


@pytest.mark.parametrize(("source_rate", "target_rate"), RATE_PAIRS)
def test_block_resample_matches_a_whole_file_resample(
    tmp_path: Path, source_rate: int, target_rate: int
) -> None:
    """Joined blocks equal one whole-file resample_poly call, sample for sample."""
    signal = _random_signal(source_rate)
    path = _write_float_wav(tmp_path / "source.wav", signal, source_rate)
    up, down = resample_ratio(source_rate, target_rate)
    expected = resample_poly(signal, up, down, axis=0)

    with sf.SoundFile(str(path)) as source:
        blocks = list(iter_resampled_blocks(source, target_rate, SMALL_BLOCK_FRAMES))
    joined = np.concatenate(blocks)

    assert joined.shape == expected.shape
    assert joined.shape[0] == output_frame_count(signal.shape[0], up, down)
    assert np.allclose(joined, expected, rtol=0.0, atol=EQUALITY_TOLERANCE)
    assert max(block.shape[0] for block in blocks) < signal.shape[0]


def test_matching_rates_pass_the_frames_through(tmp_path: Path) -> None:
    """A source at the target rate yields its own frames in bounded blocks."""
    signal = _random_signal(48_000)
    path = _write_float_wav(tmp_path / "source.wav", signal, 48_000)

    with sf.SoundFile(str(path)) as source:
        blocks = list(iter_resampled_blocks(source, 48_000, SMALL_BLOCK_FRAMES))

    assert np.array_equal(np.concatenate(blocks), signal)
    assert max(block.shape[0] for block in blocks) == SMALL_BLOCK_FRAMES


def test_frame_count_of_a_stream_rewinds_the_source(tmp_path: Path) -> None:
    """The count pass returns the frame total and leaves the stream at its start."""
    signal = _random_signal(44_100)
    path = _write_float_wav(tmp_path / "source.wav", signal, 44_100)

    with sf.SoundFile(str(path)) as source:
        counted = count_source_frames(source, SMALL_BLOCK_FRAMES)
        position = source.tell()

    assert counted == signal.shape[0]
    assert position == 0


def test_transcode_matches_whole_file_result(tmp_path: Path) -> None:
    """The block writer produces the same 24-bit audio as the clipped whole-file resample."""
    signal = _random_signal(96_000) * 2.0
    path = _write_float_wav(tmp_path / "source.wav", signal, 96_000)
    up, down = resample_ratio(96_000, 48_000)
    whole = np.clip(resample_poly(signal, up, down, axis=0), -RESAMPLE_HEADROOM, RESAMPLE_HEADROOM)
    expected_path = tmp_path / "expected.wav"
    sf.write(str(expected_path), whole, 48_000, subtype="PCM_24", format="WAV")
    output_path = tmp_path / "output.wav"

    write_resampled_audio(path, output_path, 48_000, "PCM_24", "WAV")

    written, written_rate = sf.read(str(output_path), dtype="float64", always_2d=True)
    expected, _ = sf.read(str(expected_path), dtype="float64", always_2d=True)
    assert written_rate == 48_000
    assert written.shape == expected.shape
    assert np.allclose(written, expected, rtol=0.0, atol=PCM_24_STEP)


def test_short_raw_tag_value_stays_whole() -> None:
    """A raw tag value at the cap keeps every character."""
    value = "x" * MAX_RAW_TAG_TEXT_CHARS

    assert bound_raw_tag_value(value) == value


def test_long_raw_tag_value_becomes_a_summary() -> None:
    """A raw tag value past the cap keeps its head, its length, and its digest."""
    value = "y" * LONG_TAG_CHARS
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()

    summary = bound_raw_tag_value(value)

    assert isinstance(summary, str)
    assert summary.startswith("y" * RAW_TAG_SUMMARY_PREFIX_CHARS)
    assert f"{LONG_TAG_CHARS} chars" in summary
    assert digest in summary
    assert len(summary) < MAX_RAW_TAG_TEXT_CHARS


def test_raw_tag_list_values_are_bounded_in_order() -> None:
    """Each string in a list value is bounded and the list keeps its order."""
    bounded = bound_raw_tag_value(["short", "z" * LONG_TAG_CHARS, 7])

    assert isinstance(bounded, list)
    assert bounded[0] == "short"
    assert f"{LONG_TAG_CHARS} chars" in bounded[1]
    assert bounded[2] == 7


def test_import_read_summarizes_a_long_vorbis_comment(tmp_path: Path) -> None:
    """A long custom Vorbis comment reaches the raw tags as a summary, not whole."""
    long_value = "p" * LONG_TAG_CHARS
    path = tmp_path / "long.ogg"
    path.write_bytes(make_synthetic_vorbis_ogg(fields=[("CUSTOMNOTE", long_value)]))

    metadata = MetadataTagAdapter().read_metadata(path)

    stored = str(metadata.raw_custom_tags["customnote"])
    assert "chars, sha256" in stored
    assert len(stored) < MAX_RAW_TAG_TEXT_CHARS


def test_ogg_reader_parses_a_mapped_file(tmp_path: Path) -> None:
    """A path input parses through the file map with the same result as bytes."""
    data = make_synthetic_vorbis_ogg(fields=[("ARTIST", "Mapped")])
    path = tmp_path / "mapped.ogg"
    path.write_bytes(data)

    from_path = read_ogg(path)
    from_bytes = read_ogg(data)

    assert from_path.codec == from_bytes.codec
    assert from_path.duration == from_bytes.duration
    assert from_path.bitrate == from_bytes.bitrate
    assert list(from_path.comment.fields) == list(from_bytes.comment.fields)
    assert ("ARTIST", "Mapped") in list(from_path.comment.fields)


def test_ogg_reader_rejects_an_empty_file(tmp_path: Path) -> None:
    """An empty file raises the format error instead of a map error."""
    path = tmp_path / "empty.ogg"
    path.write_bytes(b"")

    with pytest.raises(OggFormatError, match="empty"):
        read_ogg(path)


def test_similarity_uses_leading_chars_only() -> None:
    """Text past the similarity cap takes no part in the score."""
    shared = "a" * MAX_SIMILARITY_CHARS

    assert string_similarity(shared + "left tail", shared + "right tail") == 1.0
    assert string_similarity("Deep Dish", "Deep Dish") == 1.0
    assert string_similarity("Deep Dish", "Sasha") < 1.0


def test_analyzer_rejects_a_sample_rate_above_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The analyzer refuses a file whose sample rate exceeds the cap before any decode."""
    path = _write_float_wav(tmp_path / "source.wav", _random_signal(8_000), 8_000)
    monkeypatch.setattr(local_dsp, "MAX_DSP_SAMPLE_RATE_HZ", 8_000 - 1)

    with pytest.raises(ValueError, match="Sample rate"):
        read_bounded_mono_pcm(path)
