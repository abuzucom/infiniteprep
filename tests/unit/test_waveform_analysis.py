"""Generated audio exercises spectrum analysis and disk caching."""

import os
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from infiniteprep.services import waveform_analysis as analysis
from infiniteprep.services.waveform_analysis import (
    SpectrumWaveform,
    WaveformAnalysisCancelled,
    analyze_waveform,
    downsample_waveform,
)


def write_tone(path: Path, frequency: float, seconds: float = 1.25) -> Path:
    """Create a floating-point sine fixture without stored audio assets."""
    sample_rate = 48000
    times = np.arange(round(sample_rate * seconds)) / sample_rate
    sf.write(path, 0.8 * np.sin(2 * np.pi * frequency * times), sample_rate, subtype="FLOAT")
    return path


def test_cancelled_analysis_exits_before_file_access(tmp_path: Path) -> None:
    """A canceled worker does not continue hashing or decoding its stale path."""
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(WaveformAnalysisCancelled):
        analyze_waveform(tmp_path / "missing.wav", cancel_event=cancel_event)


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


@pytest.mark.parametrize("invalid", [np.zeros(3), np.zeros((2, 2))])
def test_waveform_rejects_inconsistent_shapes(invalid: np.ndarray) -> None:
    """Every band must hold one peak per overall column."""
    with pytest.raises(ValueError, match=r"one-dimensional.*same length"):
        SpectrumWaveform(invalid, np.zeros(4), np.zeros(4), np.zeros(4), 1.0)


@pytest.mark.parametrize("width", [1, 2, 3, 4, 5, 7, 19])
def test_overview_retains_final_peak_and_immutability(width: int) -> None:
    """Downsampling and expansion retain the final sample in immutable arrays."""
    samples = np.array([0.0, 0.2, 0.1, 1.0], dtype=np.float32)
    waveform = SpectrumWaveform(samples, samples, samples, samples, 1.0)
    samples[-1] = 0
    overview = downsample_waveform(waveform, width)
    for name in analysis.FIELDS:
        peaks = getattr(overview, name)
        assert len(peaks) == width
        assert peaks.max() == 1
        assert not peaks.flags.writeable
        with pytest.raises(ValueError):
            peaks[0] = 0


@pytest.mark.parametrize("budget", [0, 1])
def test_insufficient_budget_skips_cache_creation(tmp_path: Path, budget: int) -> None:
    """Zero and undersized budgets still return the analyzed track peaks."""
    source = write_tone(tmp_path / "tone.wav", 1000)
    expected = analyze_waveform(source, tmp_path / "reference")
    cache = tmp_path / "disabled"
    actual = analyze_waveform(source, cache, max_cache_bytes=budget)
    for name in analysis.FIELDS:
        np.testing.assert_array_equal(getattr(actual, name), getattr(expected, name))
    assert not cache.exists()


def test_hashing_rechecks_bytes_after_file_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real file growth after stat must not defeat the streaming byte cap."""
    source = write_tone(tmp_path / "growing.wav", 60)
    limit = source.stat().st_size
    original_stat = Path.stat
    grew = False

    def stat_then_grow(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal grew
        result = original_stat(path, *args, **kwargs)
        if path == source and not grew:
            grew = True
            with source.open("ab") as stream:
                stream.write(b"x")
        return result

    monkeypatch.setattr(analysis, "MAX_PREVIEW_FILE_BYTES", limit)
    monkeypatch.setattr(Path, "stat", stat_then_grow)
    with pytest.raises(ValueError, match="500 MiB"):
        analyze_waveform(source, tmp_path / "cache")
    assert grew


def test_failed_eviction_stops_new_cache_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied filesystem deletion preserves analysis without growing the cache."""
    cache = tmp_path / "cache"
    analyze_waveform(write_tone(tmp_path / "low.wav", 60), cache)
    entry = next(cache.glob("spectrum-*.npz"))
    budget = entry.stat().st_size - 1
    original_unlink = Path.unlink

    def deny_entry_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == entry:
            raise PermissionError("Generated cache fixture denies deletion.")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_entry_unlink)
    source = write_tone(tmp_path / "high.wav", 8000)
    waveform = analyze_waveform(source, cache, max_cache_bytes=budget)
    assert waveform.high[10:].mean() > 0.8
    assert list(cache.glob("spectrum-*.npz")) == [entry]
