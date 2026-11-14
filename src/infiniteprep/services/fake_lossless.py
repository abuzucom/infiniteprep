"""Fake-lossless audio detection using spectral cutoff and encoder traces (D332)."""

from __future__ import annotations

import logging
import stat
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

from infiniteprep.services.waveform_analysis import (
    WaveformAnalysisCancelled,
    _check_cancelled,
)
from infiniteprep.ui.audio_player import (
    MAX_PREVIEW_DURATION_SEC,
    MAX_PREVIEW_FILE_BYTES,
    STREAM_BLOCK_FRAMES,
)

ANALYZER_VERSION = 1
LOSSLESS_FORMATS = frozenset({"FLAC", "WAV", "AIFF"})
TRACE_SPECTRAL_CUTOFF = "spectral_cutoff"
TRACE_MP3_LOWPASS_SHELF = "mp3_lowpass_shelf"
TRACE_AAC_SBR_GAP = "aac_sbr_gap"

MIN_ANALYSIS_SAMPLE_RATE_HZ = 22050
FFT_SIZE = 4096
HOP_SIZE = 2048
MAX_STORED_FRAMES = 512
SILENCE_RMS_THRESHOLD = 1e-5
POWER_EPSILON = 1e-12

REF_BAND_LOW_HZ = 1000.0
REF_BAND_HIGH_HZ = 10000.0
REF_PERCENTILE = 90.0
TIME_PERCENTILE = 90.0

SMOOTH_BAND_HZ = 200.0
CUTOFF_ENERGY_DROP_DB = -45.0
CUTOFF_NYQUIST_RATIO = 0.88
MAX_EXPECTED_CUTOFF_HZ = 19500.0
MIN_CUTOFF_SEARCH_HZ = 1000.0

SHELF_MIN_HZ = 14000.0
SHELF_MAX_HZ = 20800.0
SHELF_STEP_HZ = 250.0
SHELF_WINDOW_HZ = 1000.0
SHELF_DROP_DB = 22.0
SHELF_FLOOR_DB = -38.0

SBR_MIN_HZ = 7000.0
SBR_MAX_HZ = 16500.0
SBR_STEP_HZ = 250.0
SBR_GAP_WIDTHS_HZ = (1000.0, 1500.0, 2000.0)
SBR_FLANK_LOW_HZ = 1500.0
SBR_FLANK_HIGH_HZ = 2000.0
SBR_LOW_DROP_DB = 15.0
SBR_HIGH_RISE_DB = 10.0

logger = logging.getLogger(__name__)


class FakeLosslessMethod(StrEnum):
    """Supported fake-lossless detection methods (D332)."""

    SIGNAL_ANALYSIS = "signal_analysis"


@dataclass(frozen=True)
class FakeLosslessResult:
    """Outcome of fake-lossless analysis for a single audio file."""

    is_suspect: bool
    cutoff_hz: float | None
    sample_rate: int
    nyquist_hz: float
    traces: tuple[str, ...]
    confidence: float
    method: str

    def format_warning(self) -> str | None:
        """Return a user-facing warning string when the track is suspect."""
        if not self.is_suspect:
            return None
        cutoff_text = (
            f"{self.cutoff_hz / 1000.0:.1f} kHz" if self.cutoff_hz is not None else "unknown"
        )
        traces_text = ", ".join(self.traces) if self.traces else "none"
        return (
            f"Suspected fake lossless (cutoff {cutoff_text}, "
            f"traces: {traces_text}, confidence {self.confidence:.2f})"
        )


class FakeLosslessDetector(Protocol):
    """Interface for pluggable fake-lossless detectors (D332)."""

    method_name: str

    def detect(
        self,
        file_path: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> FakeLosslessResult:
        """Analyze file_path and return a FakeLosslessResult without blocking conversion."""


def _validate_source(file_path: Path, cancel_event: threading.Event | None) -> Path:
    """Resolve and validate that file_path is a readable regular file within size bounds."""
    _check_cancelled(cancel_event)
    source = Path(file_path).resolve(strict=True)
    _check_cancelled(cancel_event)
    source_stat = source.stat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("Audio source must be a regular audio file.")
    if source_stat.st_size > MAX_PREVIEW_FILE_BYTES:
        raise ValueError("Audio exceeds 500 MiB. Supply a smaller track.")
    return source


def _collect_frame_spectra(
    audio: sf.SoundFile,
    cancel_event: threading.Event | None,
) -> np.ndarray | None:
    """Stream decoded blocks and return stacked non-silent power spectra."""
    frame_limit = min(audio.frames, int(audio.samplerate * MAX_PREVIEW_DURATION_SEC))
    window = np.hanning(FFT_SIZE).astype(np.float32)
    tail = np.empty(0, dtype=np.float32)
    spectra: list[np.ndarray] = []
    position = 0
    while position < frame_limit:
        _check_cancelled(cancel_event)
        to_read = min(STREAM_BLOCK_FRAMES, frame_limit - position)
        block = audio.read(to_read, dtype="float32", always_2d=True)
        if len(block) == 0:
            raise ValueError("Audio ended before its declared length. Repair the track.")
        if not np.isfinite(block).all():
            raise ValueError("Audio contains nonfinite samples. Repair the track.")
        mono = block.mean(axis=1)
        combined = np.concatenate((tail, mono)) if len(tail) else mono
        tail = _extract_fft_frames(combined, window, spectra)
        position += len(block)
    if not spectra:
        return None
    return np.vstack(spectra)


def _extract_fft_frames(
    samples: np.ndarray,
    window: np.ndarray,
    spectra: list[np.ndarray],
) -> np.ndarray:
    """Append power spectra for non-silent windows and return remaining tail samples."""
    offset = 0
    limit = len(samples) - FFT_SIZE + 1
    while offset < limit:
        frame = samples[offset : offset + FFT_SIZE]
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms >= SILENCE_RMS_THRESHOLD:
            spectrum = np.fft.rfft(frame * window)
            power = (spectrum.real * spectrum.real + spectrum.imag * spectrum.imag).astype(
                np.float32
            )
            if len(spectra) < MAX_STORED_FRAMES:
                spectra.append(power)
            else:
                spectra[len(spectra) % MAX_STORED_FRAMES] = np.maximum(
                    spectra[len(spectra) % MAX_STORED_FRAMES], power
                )
        offset += HOP_SIZE
    return samples[offset:]


def _relative_db_profile(spectra: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Compute frequency bins and 90th-percentile relative dB profile."""
    freqs = np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)
    percentile_power = np.percentile(spectra, TIME_PERCENTILE, axis=0)
    db_profile = 10.0 * np.log10(np.maximum(percentile_power, POWER_EPSILON))
    ref_mask = (freqs >= REF_BAND_LOW_HZ) & (freqs <= REF_BAND_HIGH_HZ)
    ref_db = float(np.percentile(db_profile[ref_mask], REF_PERCENTILE)) if ref_mask.any() else 0.0
    return freqs, db_profile - ref_db


def _smooth_profile(freqs: np.ndarray, rel_db: np.ndarray) -> np.ndarray:
    """Apply a uniform frequency smoothing kernel of roughly SMOOTH_BAND_HZ."""
    if len(freqs) < 2:
        return rel_db
    bin_hz = float(freqs[1] - freqs[0])
    kernel_len = max(1, round(SMOOTH_BAND_HZ / bin_hz))
    if kernel_len <= 1:
        return rel_db
    kernel = np.ones(kernel_len, dtype=np.float64) / kernel_len
    return np.convolve(rel_db, kernel, mode="same")


def _detect_spectral_cutoff(
    freqs: np.ndarray,
    smoothed_db: np.ndarray,
    nyquist_hz: float,
) -> tuple[float, bool, float]:
    """Return (cutoff_hz, is_suspect, score) from smoothed relative dB profile."""
    active = np.flatnonzero(
        (freqs >= MIN_CUTOFF_SEARCH_HZ) & (smoothed_db >= CUTOFF_ENERGY_DROP_DB)
    )
    cutoff_hz = float(freqs[active[-1]]) if len(active) else 0.0
    expected_min = min(nyquist_hz * CUTOFF_NYQUIST_RATIO, MAX_EXPECTED_CUTOFF_HZ)
    if cutoff_hz >= expected_min:
        return cutoff_hz, False, 0.0
    deficit_ratio = min(1.0, (expected_min - cutoff_hz) / max(1000.0, expected_min * 0.25))
    score = 0.55 + 0.35 * deficit_ratio
    return cutoff_hz, True, score


def _band_mean(
    freqs: np.ndarray,
    rel_db: np.ndarray,
    low_hz: float,
    high_hz: float,
) -> float | None:
    """Return mean relative dB in [low_hz, high_hz), or None when empty."""
    mask = (freqs >= low_hz) & (freqs < high_hz)
    if not mask.any():
        return None
    return float(np.mean(rel_db[mask]))


def _detect_mp3_shelf(freqs: np.ndarray, rel_db: np.ndarray, nyquist_hz: float) -> float:
    """Return confidence score in [0, 1] for an MP3 lowpass shelf."""
    max_freq = min(SHELF_MAX_HZ, nyquist_hz - SHELF_WINDOW_HZ)
    best_drop = 0.0
    edge = SHELF_MIN_HZ
    while edge <= max_freq:
        below = _band_mean(freqs, rel_db, edge - SHELF_WINDOW_HZ, edge)
        above = _band_mean(freqs, rel_db, edge, edge + SHELF_WINDOW_HZ)
        tail = _band_mean(freqs, rel_db, edge + SHELF_WINDOW_HZ, nyquist_hz)
        if below is not None and above is not None and tail is not None:
            drop = below - above
            if drop >= SHELF_DROP_DB and tail <= SHELF_FLOOR_DB:
                best_drop = max(best_drop, drop)
        edge += SHELF_STEP_HZ
    if best_drop <= 0.0:
        return 0.0
    return min(1.0, 0.65 + 0.35 * min(1.0, (best_drop - SHELF_DROP_DB) / 18.0))


def _detect_sbr_gap(freqs: np.ndarray, rel_db: np.ndarray, nyquist_hz: float) -> float:
    """Return confidence score in [0, 1] for an AAC SBR spectral band gap."""
    best_score = 0.0
    for width in SBR_GAP_WIDTHS_HZ:
        max_start = min(SBR_MAX_HZ, nyquist_hz - width - SBR_FLANK_HIGH_HZ)
        start = SBR_MIN_HZ
        while start <= max_start:
            score = _score_sbr_window(freqs, rel_db, start, width)
            best_score = max(best_score, score)
            start += SBR_STEP_HZ
    return best_score


def _score_sbr_window(
    freqs: np.ndarray,
    rel_db: np.ndarray,
    start_hz: float,
    width_hz: float,
) -> float:
    """Score a candidate SBR gap window [start_hz, start_hz + width_hz]."""
    low_flank = _band_mean(freqs, rel_db, start_hz - SBR_FLANK_LOW_HZ, start_hz)
    gap = _band_mean(freqs, rel_db, start_hz, start_hz + width_hz)
    high_flank = _band_mean(
        freqs, rel_db, start_hz + width_hz, start_hz + width_hz + SBR_FLANK_HIGH_HZ
    )
    if low_flank is None or gap is None or high_flank is None:
        return 0.0
    low_drop = low_flank - gap
    high_rise = high_flank - gap
    if low_drop < SBR_LOW_DROP_DB or high_rise < SBR_HIGH_RISE_DB:
        return 0.0
    excess = min(1.0, ((low_drop - SBR_LOW_DROP_DB) + (high_rise - SBR_HIGH_RISE_DB)) / 20.0)
    return 0.65 + 0.35 * excess


class SignalAnalysisDetector:
    """In-house spectral cutoff and encoder trace detector (D332)."""

    method_name: str = FakeLosslessMethod.SIGNAL_ANALYSIS.value

    def detect(
        self,
        file_path: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> FakeLosslessResult:
        """Analyze file_path for spectral cutoff, MP3 shelf, and AAC SBR gap traces."""
        source = _validate_source(file_path, cancel_event)
        with sf.SoundFile(source) as audio:
            sample_rate = int(audio.samplerate)
            nyquist_hz = sample_rate / 2.0
            if audio.format not in LOSSLESS_FORMATS or sample_rate <= MIN_ANALYSIS_SAMPLE_RATE_HZ:
                return FakeLosslessResult(
                    is_suspect=False,
                    cutoff_hz=None,
                    sample_rate=sample_rate,
                    nyquist_hz=nyquist_hz,
                    traces=(),
                    confidence=0.0,
                    method=self.method_name,
                )
            spectra = _collect_frame_spectra(audio, cancel_event)
        if spectra is None:
            return FakeLosslessResult(
                is_suspect=False,
                cutoff_hz=None,
                sample_rate=sample_rate,
                nyquist_hz=nyquist_hz,
                traces=(),
                confidence=0.0,
                method=self.method_name,
            )
        return _evaluate_spectra(spectra, sample_rate, nyquist_hz, self.method_name)


def _evaluate_spectra(
    spectra: np.ndarray,
    sample_rate: int,
    nyquist_hz: float,
    method_name: str,
) -> FakeLosslessResult:
    """Evaluate cutoff and encoder traces on collected power spectra."""
    freqs, rel_db = _relative_db_profile(spectra, sample_rate)
    smoothed = _smooth_profile(freqs, rel_db)
    cutoff_hz, cutoff_suspect, cutoff_score = _detect_spectral_cutoff(freqs, smoothed, nyquist_hz)
    shelf_score = _detect_mp3_shelf(freqs, rel_db, nyquist_hz)
    sbr_score = _detect_sbr_gap(freqs, rel_db, nyquist_hz)

    traces: list[str] = []
    scores: list[float] = []
    if cutoff_suspect:
        traces.append(TRACE_SPECTRAL_CUTOFF)
        scores.append(cutoff_score)
    if shelf_score > 0.0:
        traces.append(TRACE_MP3_LOWPASS_SHELF)
        scores.append(shelf_score)
    if sbr_score > 0.0:
        traces.append(TRACE_AAC_SBR_GAP)
        scores.append(sbr_score)

    confidence = round(min(1.0, max(scores) + 0.1 * max(0, len(scores) - 1)), 4) if scores else 0.0
    return FakeLosslessResult(
        is_suspect=bool(traces),
        cutoff_hz=round(cutoff_hz, 1),
        sample_rate=sample_rate,
        nyquist_hz=nyquist_hz,
        traces=tuple(traces),
        confidence=confidence,
        method=method_name,
    )


_DETECTOR_FACTORIES: dict[str, type[SignalAnalysisDetector]] = {
    FakeLosslessMethod.SIGNAL_ANALYSIS.value: SignalAnalysisDetector,
}


def available_methods() -> tuple[str, ...]:
    """Return registered fake-lossless detection method identifiers."""
    return tuple(_DETECTOR_FACTORIES.keys())


def get_detector(method: str | None = None) -> FakeLosslessDetector:
    """Instantiate the requested fake-lossless detector or default to signal_analysis."""
    selected = method or FakeLosslessMethod.SIGNAL_ANALYSIS.value
    factory = _DETECTOR_FACTORIES.get(selected)
    if factory is None:
        logger.warning("Unknown fake-lossless method %r; using signal_analysis.", selected)
        factory = _DETECTOR_FACTORIES[FakeLosslessMethod.SIGNAL_ANALYSIS.value]
    return factory()


def detect_fake_lossless(
    file_path: Path,
    *,
    method: str | None = None,
    cancel_event: threading.Event | None = None,
) -> FakeLosslessResult:
    """Run fake-lossless detection on file_path using the selected method."""
    try:
        return get_detector(method).detect(file_path, cancel_event=cancel_event)
    except WaveformAnalysisCancelled:
        raise
