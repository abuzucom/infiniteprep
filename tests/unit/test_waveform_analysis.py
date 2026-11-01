"""Generated audio exercises spectrum analysis and disk caching."""

import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from infiniteprep.services.waveform_analysis import analyze_waveform, downsample_waveform


def write_tone(path: Path, frequency: float, seconds: float = 1.25) -> Path:
    """Create a floating-point sine fixture without stored audio assets."""
    sample_rate = 48000
    times = np.arange(round(sample_rate * seconds)) / sample_rate
    sf.write(path, 0.8 * np.sin(2 * np.pi * frequency * times), sample_rate, subtype="FLOAT")
    return path


@pytest.mark.parametrize("frequency,band", [(60, "low"), (1000, "mid"), (8000, "high")])
def test_band_dominance(tmp_path: Path, frequency: float, band: str) -> None:
    waveform = analyze_waveform(write_tone(tmp_path / "tone.wav", frequency), tmp_path / "cache")
    dominant = getattr(waveform, band)[10:].mean()
    for other in {"low", "mid", "high"} - {band}:
        assert dominant > getattr(waveform, other)[10:].mean() * 5
    assert len(waveform.overall) == 188
    assert waveform.overall.max() == pytest.approx(1)
    assert not waveform.low.flags.writeable


def test_cache_content_and_eviction(tmp_path: Path) -> None:
    source = write_tone(tmp_path / "tone.wav", 60)
    cache = tmp_path / "cache"
    first = analyze_waveform(source, cache)
    entry = next(cache.glob("spectrum-*.npz"))
    timestamp = entry.stat().st_mtime_ns
    second = analyze_waveform(source, cache)
    assert entry.stat().st_mtime_ns == timestamp
    np.testing.assert_array_equal(first.low, second.low)
    write_tone(source, 8000)
    analyze_waveform(source, cache)
    assert len(list(cache.glob("spectrum-*.npz"))) == 2
    unrelated = cache / "keep.txt"
    unrelated.write_text("retain", encoding="utf-8")
    analyze_waveform(source, cache, max_cache_bytes=0)
    assert not list(cache.glob("spectrum-*.npz"))
    assert unrelated.read_text(encoding="utf-8") == "retain"


def test_overview_preserves_peaks(tmp_path: Path) -> None:
    waveform = analyze_waveform(write_tone(tmp_path / "tone.wav", 1000), tmp_path / "cache")
    overview = downsample_waveform(waveform, 17)
    assert len(overview.overall) == 17
    assert overview.overall.max() == waveform.overall.max()
    assert overview.duration_sec == waveform.duration_sec
    with pytest.raises(ValueError):
        downsample_waveform(waveform, 0)


def test_silence_and_antiphase(tmp_path: Path) -> None:
    source = write_tone(tmp_path / "tone.wav", 60)
    samples, rate = sf.read(source)
    sf.write(source, np.column_stack((samples, -samples)), rate, subtype="FLOAT")
    waveform = analyze_waveform(source, tmp_path / "cache")
    assert waveform.low.mean() > 0.8
    sf.write(source, np.zeros(480), rate)
    silent = analyze_waveform(source, tmp_path / "cache")
    assert len(silent.overall) == 2
    assert np.all(silent.overall == 0)


def test_cache_evicts_oldest_first(tmp_path: Path) -> None:
    """A newer archive survives a budget that fits only one archive."""
    cache = tmp_path / "cache"
    analyze_waveform(write_tone(tmp_path / "low.wav", 60), cache)
    oldest = next(cache.glob("spectrum-*.npz"))
    os.utime(oldest, (1, 1))
    source = write_tone(tmp_path / "high.wav", 8000)
    analyze_waveform(source, cache)
    newest = next(path for path in cache.glob("spectrum-*.npz") if path != oldest)
    analyze_waveform(source, cache, max_cache_bytes=newest.stat().st_size)
    assert not oldest.exists()
    assert newest.exists()


def test_corrupt_cache_recomputes(tmp_path: Path) -> None:
    """An interrupted cache write cannot replace valid analysis results."""
    source = write_tone(tmp_path / "tone.wav", 1000)
    cache = tmp_path / "cache"
    expected = analyze_waveform(source, cache)
    next(cache.glob("spectrum-*.npz")).write_bytes(b"incomplete")
    actual = analyze_waveform(source, cache)
    np.testing.assert_array_equal(expected.mid, actual.mid)


def test_duration_cap(tmp_path: Path) -> None:
    """Generate silent audio past the cap without allocating a full track array."""
    source = tmp_path / "long.wav"
    with sf.SoundFile(source, mode="w", samplerate=8000, channels=1) as audio:
        for _ in range(1801):
            audio.write(np.zeros(8000, dtype=np.float32))
    waveform = analyze_waveform(source, tmp_path / "cache")
    assert waveform.duration_sec == 1800
    assert len(waveform.overall) == 270000


def test_file_size_cap(tmp_path: Path) -> None:
    """Reject an oversized sparse fixture before decoding or caching."""
    source = tmp_path / "oversized.wav"
    with source.open("wb") as stream:
        stream.truncate(524288001)
    with pytest.raises(ValueError, match="500 MiB"):
        analyze_waveform(source, tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_empty_audio_and_expanded_overview(tmp_path: Path) -> None:
    """Empty tracks produce silent overview columns at the requested width."""
    source = tmp_path / "empty.wav"
    sf.write(source, np.zeros(0), 48000)
    waveform = analyze_waveform(source, tmp_path / "cache")
    assert waveform.duration_sec == 0
    assert len(waveform.overall) == 0
    assert np.all(downsample_waveform(waveform, 7).overall == 0)
