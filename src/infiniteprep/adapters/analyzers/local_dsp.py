"""Built-in BSD-compatible DSP audio analyzer for BPM, musical key, and rip diagnostics."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf

from infiniteprep.domain.enums import KeyMode, PitchClass
from infiniteprep.domain.models import AnalysisResult

logger = logging.getLogger(__name__)

MAX_ANALYSIS_FILE_BYTES: Final[int] = 524_288_000  # 500 MiB
MAX_ANALYSIS_DURATION_SEC: Final[float] = 1800.0  # 30 minutes
ENVELOPE_BINS: Final[int] = 64

PITCH_CLASSES_ORDERED: Final[tuple[PitchClass, ...]] = (
    PitchClass.C,
    PitchClass.C_SHARP,
    PitchClass.D,
    PitchClass.E_FLAT,
    PitchClass.E,
    PitchClass.F,
    PitchClass.F_SHARP,
    PitchClass.G,
    PitchClass.A_FLAT,
    PitchClass.A,
    PitchClass.B_FLAT,
    PitchClass.B,
)

MAJOR_PROFILE: Final[tuple[float, ...]] = (
    6.35,
    2.23,
    3.48,
    2.33,
    4.38,
    4.09,
    2.52,
    5.19,
    2.39,
    3.66,
    2.29,
    2.88,
)
MINOR_PROFILE: Final[tuple[float, ...]] = (
    6.33,
    2.68,
    3.52,
    5.38,
    2.60,
    3.53,
    2.54,
    4.75,
    3.98,
    2.69,
    3.34,
    3.17,
)


def compute_rip_quality_warnings(mono: np.ndarray, sample_rate: int) -> list[str]:
    """Check PCM signal for digital clipping, DC offset, and leading/trailing silence."""
    warnings: list[str] = []
    if mono.size == 0 or sample_rate <= 0:
        return warnings

    dc_val = float(np.mean(mono))
    if abs(dc_val) > 0.015:
        warnings.append(
            f"Rip Quality Warning: DC offset detected ({dc_val:+.3f}); "
            "apply high-pass / DC-removal filter."
        )

    clipped_count = int(np.sum(np.abs(mono) >= 0.998))
    if clipped_count >= 12:
        warnings.append(
            f"Rip Quality Warning: Digital clipping detected ({clipped_count} clipped samples); "
            "check recording gain."
        )

    win = max(1, sample_rate // 10)
    num_blocks = mono.size // win
    if num_blocks >= 4:
        blocks = mono[: num_blocks * win].reshape(num_blocks, win)
        block_ac_rms = np.sqrt(
            np.mean((blocks - np.mean(blocks, axis=1, keepdims=True)) ** 2, axis=1)
        )
        active_blocks = np.flatnonzero(block_ac_rms > 0.0018)
        if active_blocks.size > 0:
            lead_sec = float(active_blocks[0] * win) / float(sample_rate)
            trail_sec = float((num_blocks - 1 - active_blocks[-1]) * win) / float(sample_rate)
            if lead_sec >= 2.0 or trail_sec >= 2.0:
                warnings.append(
                    f"Rip Quality Warning: Excessive silence detected "
                    f"(leading={lead_sec:.1f}s, trailing={trail_sec:.1f}s); consider trimming."
                )
    return warnings


def estimate_bpm_from_onset_envelope(
    mono: np.ndarray, sample_rate: int, min_bpm: float = 80.0, max_bpm: float = 175.0
) -> float | None:
    """Estimate tempo in BPM using smoothed energy envelope autocorrelation."""
    if mono.size < sample_rate or sample_rate <= 0:
        return None
    hop = max(1, sample_rate // 200)
    env_rate = float(sample_rate) / float(hop)
    frames = mono[: (mono.size // hop) * hop].reshape(-1, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    env = rms - float(np.mean(rms))
    norm = float(np.sum(env * env))
    if norm <= 1e-12:
        return None
    autocorr = np.correlate(env, env, mode="full")[env.size - 1 :] / norm
    min_lag = max(1, round((60.0 / max_bpm) * env_rate))
    max_lag = min(autocorr.size - 1, round((60.0 / min_bpm) * env_rate))
    if max_lag <= min_lag:
        return None
    best_lag = min_lag + int(np.argmax(autocorr[min_lag : max_lag + 1]))
    return round((60.0 * env_rate) / float(best_lag), 1) if best_lag > 0 else None


def estimate_key_from_chroma(
    mono: np.ndarray, sample_rate: int
) -> tuple[PitchClass | None, KeyMode | None, float]:
    """Estimate canonical (PitchClass, KeyMode, strength) via Krumhansl-Kessler chroma profiles."""
    if mono.size < 4096 or sample_rate <= 0:
        return None, None, 0.0
    max_samples = min(mono.size, sample_rate * 30)
    segment = mono[:max_samples] - float(np.mean(mono[:max_samples]))
    spectrum = np.abs(np.fft.rfft(segment * np.hanning(segment.size)))
    freqs = np.fft.rfftfreq(segment.size, d=1.0 / float(sample_rate))

    chroma = _accumulate_chroma_bins(spectrum, freqs)
    chroma_std = float(np.std(chroma))
    if chroma_std <= 1e-9:
        return PitchClass.A, KeyMode.MINOR, 0.0
    chroma_norm = (chroma - float(np.mean(chroma))) / chroma_std
    return _match_key_profiles(chroma_norm)


def _accumulate_chroma_bins(spectrum: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Fold FFT magnitude bins in [55 Hz, 2000 Hz] into 12 semitone pitch classes."""
    chroma = np.zeros(12, dtype=np.float64)
    mask = (freqs >= 55.0) & (freqs <= 2000.0)
    valid_freqs = freqs[mask]
    valid_mag = spectrum[mask]
    if valid_freqs.size == 0:
        return chroma
    midi_notes = np.round(69.0 + 12.0 * np.log2(valid_freqs / 440.0)).astype(int)
    pitch_classes = np.mod(midi_notes, 12)
    for pc_idx in range(12):
        chroma[pc_idx] = float(np.sum(valid_mag[pitch_classes == pc_idx]))
    return chroma


def _match_key_profiles(
    chroma_norm: np.ndarray,
) -> tuple[PitchClass, KeyMode, float]:
    """Correlate normalized 12-bin chroma vector against all 24 major and minor profiles."""
    maj = np.array(MAJOR_PROFILE, dtype=np.float64)
    maj = (maj - float(np.mean(maj))) / float(np.std(maj))
    min_p = np.array(MINOR_PROFILE, dtype=np.float64)
    min_p = (min_p - float(np.mean(min_p))) / float(np.std(min_p))

    best_score = -2.0
    best_tonic = PitchClass.A
    best_mode = KeyMode.MINOR
    for shift, tonic in enumerate(PITCH_CLASSES_ORDERED):
        rotated = np.roll(chroma_norm, -shift)
        maj_corr = float(np.dot(rotated, maj) / 12.0)
        min_corr = float(np.dot(rotated, min_p) / 12.0)
        if maj_corr > best_score:
            best_score, best_tonic, best_mode = maj_corr, tonic, KeyMode.MAJOR
        if min_corr > best_score:
            best_score, best_tonic, best_mode = min_corr, tonic, KeyMode.MINOR
    strength = round(max(0.0, min(1.0, (best_score + 1.0) / 2.0)), 3)
    return best_tonic, best_mode, strength


def compute_compact_envelope(mono: np.ndarray, bins: int = ENVELOPE_BINS) -> list[float]:
    """Compute a normalized peak amplitude envelope with the requested bin count."""
    if mono.size == 0 or bins <= 0:
        return [0.0] * max(1, bins)
    abs_mono = np.abs(mono)
    chunks = np.array_split(abs_mono, bins)
    peaks = [float(np.max(c)) if c.size > 0 else 0.0 for c in chunks]
    peak_max = max(peaks) if peaks else 0.0
    if peak_max <= 0.0:
        return [0.0] * bins
    return [round(p / peak_max, 4) for p in peaks]


def read_bounded_mono_pcm(
    audio_reference: Path,
    max_bytes: int = MAX_ANALYSIS_FILE_BYTES,
    max_duration_sec: float = MAX_ANALYSIS_DURATION_SEC,
) -> tuple[np.ndarray, int]:
    """Read up to max_duration_sec of audio as mono float64 after checking file size."""
    size_bytes = audio_reference.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(
            f"Audio file '{audio_reference.name}' ({size_bytes} bytes) exceeds maximum "
            f"analysis limit ({max_bytes} bytes)."
        )
    info = sf.info(str(audio_reference))
    sr = max(1, int(info.samplerate))
    max_frames = max(1, round(sr * max_duration_sec))
    frames_to_read = min(int(info.frames), max_frames) if info.frames > 0 else max_frames
    data, sample_rate = sf.read(
        str(audio_reference), frames=frames_to_read, dtype="float64", always_2d=True
    )
    mono = np.mean(data, axis=1) if data.size > 0 else np.zeros(0, dtype=np.float64)
    return mono, int(sample_rate)


class LocalDspAnalyzer:
    """Built-in local audio analyzer computing BPM, canonical key, and rip diagnostics."""

    analyzer_name: str = "local_dsp"
    analyzer_version: str = "0.1.0"

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status for built-in LocalDspAnalyzer."""
        return True, None

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Analyze audio file locally for BPM, canonical musical key, and rip warnings."""
        try:
            mono, sample_rate = read_bounded_mono_pcm(audio_reference)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Local DSP decode failed for %s: %s", audio_reference, exc)
            return AnalysisResult(
                id=str(uuid.uuid4()),
                file_id=file_id,
                analyzer_name=self.analyzer_name,
                analyzer_version=self.analyzer_version,
                configuration={"error": str(exc)},
                bpm=None,
                key_tonic=None,
                key_mode=None,
                analysis_strength=0.0,
                warnings=[f"Audio decode failed ({exc}); verify file integrity."],
            )

        warnings = compute_rip_quality_warnings(mono, sample_rate)
        bpm = estimate_bpm_from_onset_envelope(mono, sample_rate)
        tonic, mode, strength = estimate_key_from_chroma(mono, sample_rate)
        envelope = compute_compact_envelope(mono, bins=ENVELOPE_BINS)

        return AnalysisResult(
            id=str(uuid.uuid4()),
            file_id=file_id,
            analyzer_name=self.analyzer_name,
            analyzer_version=self.analyzer_version,
            configuration={"sample_rate": sample_rate, "waveform_envelope": envelope},
            bpm=bpm,
            key_tonic=tonic,
            key_mode=mode,
            analysis_strength=strength,
            warnings=warnings,
        )
