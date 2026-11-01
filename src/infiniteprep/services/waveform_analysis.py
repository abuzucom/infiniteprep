"""Bounded streaming spectrum peaks with a content-addressed disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import platformdirs
import soundfile as sf
from scipy.signal import butter, sosfilt

from infiniteprep.ui.audio_player import (
    MAX_PREVIEW_DURATION_SEC,
    MAX_PREVIEW_FILE_BYTES,
    STREAM_BLOCK_FRAMES,
)

ANALYZER_VERSION = 1
COLUMNS_PER_SECOND = 150
FILTER_ORDER = 4
LOW_CUTOFF_HZ = 200
HIGH_CUTOFF_HZ = 3000
DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
HASH_BLOCK_BYTES = 1024 * 1024
FIELDS = ("low", "mid", "high", "overall")
CACHE_NAME = re.compile(r"spectrum-[0-9a-f]{64}\.npz")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpectrumWaveform:
    """Track-normalized band peaks with equal-duration columns."""

    low: np.ndarray
    mid: np.ndarray
    high: np.ndarray
    overall: np.ndarray
    duration_sec: float

    def __post_init__(self) -> None:
        """Own immutable arrays independently of caller buffers."""
        for name in FIELDS:
            array = np.asarray(getattr(self, name), dtype=np.float32)
            immutable = np.frombuffer(array.tobytes(), dtype=np.float32)
            object.__setattr__(self, name, immutable)


def _cache_key(source: Path) -> str:
    """Hash bounded content and all analysis settings."""
    digest = hashlib.sha256()
    bytes_seen = 0
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_BLOCK_BYTES), b""):
            bytes_seen += len(block)
            if bytes_seen > MAX_PREVIEW_FILE_BYTES:
                raise ValueError("Audio exceeds 500 MiB. Supply a smaller track.")
            digest.update(block)
    settings = json.dumps(
        [
            COLUMNS_PER_SECOND,
            FILTER_ORDER,
            LOW_CUTOFF_HZ,
            HIGH_CUTOFF_HZ,
            MAX_PREVIEW_DURATION_SEC,
            STREAM_BLOCK_FRAMES,
        ],
        separators=(",", ":"),
    )
    settings_hash = hashlib.sha256(settings.encode("ascii")).hexdigest()
    payload = f"{digest.hexdigest()}:{ANALYZER_VERSION}:{settings_hash}"
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _filter_bank(sample_rate: int, channels: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Construct causal filters and independent channel state."""
    filters = []
    for cutoff, kind in (
        (LOW_CUTOFF_HZ, "lowpass"),
        ((LOW_CUTOFF_HZ, HIGH_CUTOFF_HZ), "bandpass"),
        (HIGH_CUTOFF_HZ, "highpass"),
    ):
        sections = butter(FILTER_ORDER, cutoff, btype=kind, fs=sample_rate, output="sos")
        filters.append((sections, np.zeros((len(sections), 2, channels))))
    return filters


def _decode_waveform(source: Path) -> SpectrumWaveform:
    """Decode capped blocks while retaining filter state across boundaries."""
    with sf.SoundFile(source) as audio:
        if audio.samplerate <= HIGH_CUTOFF_HZ * 2:
            raise ValueError("Sample rate must exceed 6000 Hz. Resample the track first.")
        frame_count = min(audio.frames, int(audio.samplerate * MAX_PREVIEW_DURATION_SEC))
        duration = frame_count / audio.samplerate
        count = math.ceil(frame_count * COLUMNS_PER_SECOND / audio.samplerate)
        peaks = np.zeros((len(FIELDS), count), dtype=np.float32)
        filters = _filter_bank(audio.samplerate, audio.channels)
        position = 0
        while position < frame_count:
            block = audio.read(
                min(STREAM_BLOCK_FRAMES, frame_count - position),
                dtype="float32",
                always_2d=True,
            )
            if len(block) == 0:
                raise ValueError("Audio ended before its declared length. Repair the track.")
            if not np.isfinite(block).all():
                raise ValueError("Audio contains nonfinite samples. Repair the track.")
            _accumulate_peaks(peaks, filters, block, position, audio.samplerate)
            position += len(block)
    track_peak = float(peaks[-1].max()) if count else 0.0
    if track_peak > 0:
        peaks /= track_peak
    return SpectrumWaveform(*peaks, duration)


def _accumulate_peaks(
    peaks: np.ndarray,
    filters: list[tuple[np.ndarray, np.ndarray]],
    block: np.ndarray,
    position: int,
    sample_rate: int,
) -> None:
    """Reduce absolute channel peaks into stable time columns."""
    columns = (np.arange(len(block)) + position) * COLUMNS_PER_SECOND // sample_rate
    for index, (sections, state) in enumerate(filters):
        filtered, next_state = sosfilt(sections, block, axis=0, zi=state)
        np.copyto(state, next_state)
        np.maximum.at(peaks[index], columns, np.abs(filtered).max(axis=1))
    np.maximum.at(peaks[-1], columns, np.abs(block).max(axis=1))


def _load_cache(path: Path) -> SpectrumWaveform | None:
    """Reject malformed cache data without enabling pickle loading."""
    try:
        with np.load(path, allow_pickle=False) as archive:
            duration = float(archive["duration_sec"].item())
            arrays = [archive[name] for name in FIELDS]
            if not 0 <= duration <= MAX_PREVIEW_DURATION_SEC:
                return None
            count = math.ceil(duration * COLUMNS_PER_SECOND)
            if any(array.shape != (count,) for array in arrays):
                return None
            if any(not np.isfinite(array).all() or (array < 0).any() for array in arrays):
                return None
            return SpectrumWaveform(*arrays, duration)
    except (OSError, ValueError, TypeError, KeyError, EOFError, zipfile.BadZipFile):
        return None


def _store_cache(directory: Path, entry: Path, waveform: SpectrumWaveform) -> None:
    """Publish complete archives atomically without following a cache-file link."""
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"spectrum-{uuid.uuid4().hex}{uuid.uuid4().hex}.npz"
    try:
        with temporary.open("xb") as stream:
            np.savez_compressed(
                stream,
                **{name: getattr(waveform, name) for name in FIELDS},
                duration_sec=waveform.duration_sec,
            )
        os.replace(temporary, entry)
    finally:
        if temporary.exists():
            temporary.unlink()


def _evict_cache(directory: Path, size_limit: int) -> None:
    """Delete only analyzer-owned archives in oldest-write order."""
    entries = [
        path
        for path in directory.iterdir()
        if CACHE_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()
    ]
    entries.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
    total = sum(path.stat().st_size for path in entries)
    for path in entries:
        if total <= size_limit:
            return
        size = path.stat().st_size
        path.unlink()
        total -= size


def analyze_waveform(
    file_path: Path,
    cache_directory: Path | None = None,
    *,
    max_cache_bytes: int = DEFAULT_CACHE_BYTES,
) -> SpectrumWaveform:
    """Analyze a track or reuse matching cached peaks. Cap decoded duration at 30 minutes."""
    source = Path(file_path)
    if max_cache_bytes < 0:
        raise ValueError("Cache size must be nonnegative. Supply a valid byte limit.")
    if source.stat().st_size > MAX_PREVIEW_FILE_BYTES:
        raise ValueError("Audio exceeds 500 MiB. Supply a smaller track.")
    directory = Path(cache_directory or platformdirs.user_cache_dir("InfinitePrep"))
    key = _cache_key(source)
    entry = directory / f"spectrum-{key}.npz"
    waveform = _load_cache(entry)
    if waveform is None:
        waveform = _decode_waveform(source)
        try:
            _store_cache(directory, entry, waveform)
        except OSError:
            logger.warning("Spectrum cache write failed. Check cache directory permissions.")
    try:
        _evict_cache(directory, max_cache_bytes)
    except OSError:
        logger.warning("Spectrum cache eviction failed. Check cache directory permissions.")
    return waveform


def downsample_waveform(waveform: SpectrumWaveform, width: int) -> SpectrumWaveform:
    """Return exactly width peak-preserving overview columns."""
    if width <= 0:
        raise ValueError("Overview width must be positive. Supply a valid column count.")
    count = len(waveform.overall)
    arrays = np.zeros((len(FIELDS), width), dtype=np.float32)
    if count:
        for column in range(width):
            start = column * count // width
            end = max(start + 1, (column + 1) * count // width)
            for index, name in enumerate(FIELDS):
                arrays[index, column] = getattr(waveform, name)[start:end].max()
    return SpectrumWaveform(*arrays, waveform.duration_sec)
