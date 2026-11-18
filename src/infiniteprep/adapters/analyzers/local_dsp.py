"""Built-in BSD-compatible DSP audio analyzer for BPM, musical key, and rip diagnostics."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf

from infiniteprep.domain.enums import KeyMode, PitchClass
from infiniteprep.domain.models import AnalysisResult

logger = logging.getLogger(__name__)

MAX_ANALYSIS_FILE_BYTES: Final[int] = 524_288_000  # 500 MiB
MAX_ANALYSIS_DURATION_SEC: Final[float] = 1800.0  # 30 minutes
# A file header declares the channel count and the sample rate. A crafted header can claim
# values that expand a small file to many gigabytes. The values match the preview decoder caps.
MAX_DSP_CHANNELS: Final[int] = 8
MAX_DSP_SAMPLE_RATE_HZ: Final[int] = 384_000
# The mono frame cap covers 30 minutes at 48 kHz, about 691 MB as float64. A file above
# 48 kHz analyzes a shorter span. The analysis steps make a few temporary copies of this size.
MAX_DSP_MONO_FRAMES: Final[int] = 30 * 60 * 48_000
DSP_BLOCK_FRAMES: Final[int] = 65_536
ENVELOPE_BINS: Final[int] = 64

# Rip quality checks. A mean sample beyond the DC limit marks an offset. Samples at or above
# the clip level count as clipped, and the warning needs at least the minimum count, so a few
# legitimate peaks pass. Silence is measured in tenth-of-a-second windows. A window below the
# RMS level counts as silent, and an edge silence of 2 seconds or more raises the warning.
DC_OFFSET_LIMIT: Final[float] = 0.015
CLIP_SAMPLE_LEVEL: Final[float] = 0.998
MIN_CLIPPED_SAMPLES: Final[int] = 12
SILENCE_WINDOWS_PER_SECOND: Final[int] = 10
MIN_SILENCE_WINDOWS: Final[int] = 4
SILENCE_RMS_LEVEL: Final[float] = 0.0018
MAX_EDGE_SILENCE_SEC: Final[float] = 2.0

# Tempo. The onset envelope uses 200 frames per second, a 5 ms resolution. The default range
# covers house, techno, and drum and bass without doubling or halving.
ONSET_FRAMES_PER_SECOND: Final[int] = 200
SECONDS_PER_MINUTE: Final[float] = 60.0
DEFAULT_MIN_BPM: Final[float] = 80.0
DEFAULT_MAX_BPM: Final[float] = 175.0
# An envelope with less energy than this is silence and carries no tempo.
MIN_ENVELOPE_ENERGY: Final[float] = 1e-12

# Key. The chroma reads the first 30 seconds within the 55 Hz to 2 kHz band, where the
# harmonic content sits. A chroma spread below the minimum carries no pitch.
MIN_KEY_SAMPLES: Final[int] = 4096
KEY_WINDOW_SEC: Final[int] = 30
CHROMA_MIN_HZ: Final[float] = 55.0
CHROMA_MAX_HZ: Final[float] = 2000.0
MIN_CHROMA_SPREAD: Final[float] = 1e-9
# Equal temperament reference: MIDI note 69 is A4 at 440 Hz.
A4_MIDI_NOTE: Final[float] = 69.0
A4_FREQUENCY_HZ: Final[float] = 440.0
SEMITONES_PER_OCTAVE: Final[int] = 12
# Correlation lies in [-1, 1]. The starting best score sits below that range.
CORRELATION_FLOOR: Final[float] = -2.0

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

    dc_offset = float(np.mean(mono))
    if abs(dc_offset) > DC_OFFSET_LIMIT:
        warnings.append(
            f"Rip Quality Warning: DC offset detected ({dc_offset:+.3f}); "
            "apply high-pass / DC-removal filter."
        )

    clipped_count = int(np.sum(np.abs(mono) >= CLIP_SAMPLE_LEVEL))
    if clipped_count >= MIN_CLIPPED_SAMPLES:
        warnings.append(
            f"Rip Quality Warning: Digital clipping detected ({clipped_count} clipped samples); "
            "check recording gain."
        )

    window_samples = max(1, sample_rate // SILENCE_WINDOWS_PER_SECOND)
    block_count = mono.size // window_samples
    if block_count >= MIN_SILENCE_WINDOWS:
        blocks = mono[: block_count * window_samples].reshape(block_count, window_samples)
        block_ac_rms = np.sqrt(
            np.mean((blocks - np.mean(blocks, axis=1, keepdims=True)) ** 2, axis=1)
        )
        active_blocks = np.flatnonzero(block_ac_rms > SILENCE_RMS_LEVEL)
        if active_blocks.size > 0:
            lead_sec = float(active_blocks[0] * window_samples) / float(sample_rate)
            trail_sec = float((block_count - 1 - active_blocks[-1]) * window_samples) / float(
                sample_rate
            )
            if lead_sec >= MAX_EDGE_SILENCE_SEC or trail_sec >= MAX_EDGE_SILENCE_SEC:
                warnings.append(
                    f"Rip Quality Warning: Excessive silence detected "
                    f"(leading={lead_sec:.1f}s, trailing={trail_sec:.1f}s); consider trimming."
                )
    return warnings


def estimate_bpm_from_onset_envelope(
    mono: np.ndarray,
    sample_rate: int,
    min_bpm: float = DEFAULT_MIN_BPM,
    max_bpm: float = DEFAULT_MAX_BPM,
) -> float | None:
    """Estimate tempo in BPM using smoothed energy envelope autocorrelation."""
    if mono.size < sample_rate or sample_rate <= 0:
        return None
    hop = max(1, sample_rate // ONSET_FRAMES_PER_SECOND)
    envelope_rate = float(sample_rate) / float(hop)
    frames = mono[: (mono.size // hop) * hop].reshape(-1, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    envelope = rms - float(np.mean(rms))
    envelope_energy = float(np.sum(envelope * envelope))
    if envelope_energy <= MIN_ENVELOPE_ENERGY:
        return None
    min_lag = max(1, round((SECONDS_PER_MINUTE / max_bpm) * envelope_rate))
    max_lag = min(envelope.size - 1, round((SECONDS_PER_MINUTE / min_bpm) * envelope_rate))
    if max_lag <= min_lag:
        return None
    # Only the lags of the tempo range matter. A full autocorrelation costs the square of
    # the envelope length, which reaches 360,000 points for a 30 minute file.
    autocorr = [
        float(np.dot(envelope[: envelope.size - lag], envelope[lag:]))
        for lag in range(min_lag, max_lag + 1)
    ]
    # min_lag is at least 1, so best_lag is always positive.
    best_lag = min_lag + int(np.argmax(autocorr))
    return round((SECONDS_PER_MINUTE * envelope_rate) / float(best_lag), 1)


def estimate_key_from_chroma(
    mono: np.ndarray, sample_rate: int
) -> tuple[PitchClass | None, KeyMode | None, float]:
    """Estimate canonical (PitchClass, KeyMode, strength) via Krumhansl-Kessler chroma profiles."""
    if mono.size < MIN_KEY_SAMPLES or sample_rate <= 0:
        return None, None, 0.0
    max_samples = min(mono.size, sample_rate * KEY_WINDOW_SEC)
    segment = mono[:max_samples] - float(np.mean(mono[:max_samples]))
    spectrum = np.abs(np.fft.rfft(segment * np.hanning(segment.size)))
    frequencies = np.fft.rfftfreq(segment.size, d=1.0 / float(sample_rate))

    chroma = _accumulate_chroma_bins(spectrum, frequencies)
    chroma_std = float(np.std(chroma))
    if chroma_std <= MIN_CHROMA_SPREAD:
        # Silence and flat noise carry no pitch. A key here would be invented.
        return None, None, 0.0
    normalized_chroma = (chroma - float(np.mean(chroma))) / chroma_std
    return _match_key_profiles(normalized_chroma)


def _accumulate_chroma_bins(spectrum: np.ndarray, frequencies: np.ndarray) -> np.ndarray:
    """Fold FFT magnitude bins in [55 Hz, 2000 Hz] into 12 semitone pitch classes."""
    chroma = np.zeros(SEMITONES_PER_OCTAVE, dtype=np.float64)
    mask = (frequencies >= CHROMA_MIN_HZ) & (frequencies <= CHROMA_MAX_HZ)
    valid_frequencies = frequencies[mask]
    valid_magnitudes = spectrum[mask]
    if valid_frequencies.size == 0:
        return chroma
    midi_notes = np.round(
        A4_MIDI_NOTE + SEMITONES_PER_OCTAVE * np.log2(valid_frequencies / A4_FREQUENCY_HZ)
    ).astype(int)
    pitch_classes = np.mod(midi_notes, SEMITONES_PER_OCTAVE)
    for pitch_class_index in range(SEMITONES_PER_OCTAVE):
        chroma[pitch_class_index] = float(
            np.sum(valid_magnitudes[pitch_classes == pitch_class_index])
        )
    return chroma


def _match_key_profiles(
    normalized_chroma: np.ndarray,
) -> tuple[PitchClass, KeyMode, float]:
    """Correlate normalized 12-bin chroma vector against all 24 major and minor profiles."""
    major_profile = np.array(MAJOR_PROFILE, dtype=np.float64)
    major_profile = (major_profile - float(np.mean(major_profile))) / float(np.std(major_profile))
    min_p = np.array(MINOR_PROFILE, dtype=np.float64)
    min_p = (min_p - float(np.mean(min_p))) / float(np.std(min_p))

    best_score = CORRELATION_FLOOR
    best_tonic = PitchClass.A
    best_mode = KeyMode.MINOR
    for shift, tonic in enumerate(PITCH_CLASSES_ORDERED):
        rotated = np.roll(normalized_chroma, -shift)
        major_correlation = float(np.dot(rotated, major_profile) / SEMITONES_PER_OCTAVE)
        minor_correlation = float(np.dot(rotated, min_p) / SEMITONES_PER_OCTAVE)
        if major_correlation > best_score:
            best_score, best_tonic, best_mode = major_correlation, tonic, KeyMode.MAJOR
        if minor_correlation > best_score:
            best_score, best_tonic, best_mode = minor_correlation, tonic, KeyMode.MINOR
    strength = round(max(0.0, min(1.0, (best_score + 1.0) / 2.0)), 3)
    return best_tonic, best_mode, strength


def compute_compact_envelope(mono: np.ndarray, bins: int = ENVELOPE_BINS) -> list[float]:
    """Compute a normalized peak amplitude envelope with the requested bin count."""
    if mono.size == 0 or bins <= 0:
        return [0.0] * max(1, bins)
    abs_mono = np.abs(mono)
    chunks = np.array_split(abs_mono, bins)
    peaks = [float(np.max(chunk)) if chunk.size > 0 else 0.0 for chunk in chunks]
    peak_max = max(peaks) if peaks else 0.0
    if peak_max <= 0.0:
        return [0.0] * bins
    return [round(peak / peak_max, 4) for peak in peaks]


def read_bounded_mono_pcm(
    audio_reference: Path,
    max_bytes: int = MAX_ANALYSIS_FILE_BYTES,
    max_duration_sec: float = MAX_ANALYSIS_DURATION_SEC,
) -> tuple[np.ndarray, int]:
    """Read up to max_duration_sec of audio as mono float64 after checking file size.

    The decode runs in blocks and keeps only the mono mix. The memory in use then follows
    the frame count, not the frame count times the channel count.
    """
    size_bytes = audio_reference.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(
            f"Audio file '{audio_reference.name}' ({size_bytes} bytes) exceeds maximum "
            f"analysis limit ({max_bytes} bytes)."
        )
    info = sf.info(str(audio_reference))
    sample_rate = int(info.samplerate)
    frame_limit = _bounded_dsp_frames(sample_rate, int(info.channels), max_duration_sec)
    if info.frames > 0:
        frames_to_read = min(int(info.frames), frame_limit)
        return _decode_mono_into_buffer(audio_reference, frames_to_read), sample_rate
    return _decode_mono_chunks(audio_reference, frame_limit), sample_rate


def _bounded_dsp_frames(sample_rate: int, channels: int, max_duration_sec: float) -> int:
    """Validate the declared stream shape and return the largest frame count to decode."""
    if channels <= 0 or channels > MAX_DSP_CHANNELS:
        raise ValueError(
            f"Channel count {channels} is outside 1 to {MAX_DSP_CHANNELS}; "
            "convert the file to stereo."
        )
    if sample_rate <= 0 or sample_rate > MAX_DSP_SAMPLE_RATE_HZ:
        raise ValueError(
            f"Sample rate {sample_rate} Hz is outside 1 to {MAX_DSP_SAMPLE_RATE_HZ} Hz; "
            "convert the file to a standard sample rate."
        )
    return min(max(1, round(sample_rate * max_duration_sec)), MAX_DSP_MONO_FRAMES)


def _mono_blocks(audio_reference: Path, frame_limit: int) -> Iterator[np.ndarray]:
    """Yield the mono mix of each decoded block until frame_limit frames have passed."""
    remaining = frame_limit
    for block in sf.blocks(
        str(audio_reference), blocksize=DSP_BLOCK_FRAMES, dtype="float64", always_2d=True
    ):
        take = min(int(block.shape[0]), remaining)
        if take <= 0:
            return
        yield np.mean(block[:take], axis=1)
        remaining -= take


def _decode_mono_into_buffer(audio_reference: Path, frame_count: int) -> np.ndarray:
    """Decode a stream of known length into one buffer, so the mono mix exists only once."""
    mono = np.empty(frame_count, dtype=np.float64)
    filled = 0
    for chunk in _mono_blocks(audio_reference, frame_count):
        mono[filled : filled + chunk.size] = chunk
        filled += chunk.size
    if filled < frame_count:
        # The header declared more frames than the stream holds. An in-place shrink releases
        # the rest without a second copy of the decoded frames.
        mono.resize(filled, refcheck=False)
    return mono


def _count_stream_frames(audio_reference: Path, frame_limit: int) -> int:
    """Count the frames a stream holds, up to frame_limit, without keeping decoded samples."""
    counted = 0
    for block in sf.blocks(
        str(audio_reference), blocksize=DSP_BLOCK_FRAMES, dtype="float32", always_2d=True
    ):
        counted += int(block.shape[0])
        if counted >= frame_limit:
            return frame_limit
    return counted


def _decode_mono_chunks(audio_reference: Path, frame_limit: int) -> np.ndarray:
    """Decode a stream of unknown length into one buffer of its counted length.

    A first pass counts the frames and keeps no samples. The second pass fills one buffer.
    The stream decodes twice, and the mono mix exists only once.
    """
    frame_count = _count_stream_frames(audio_reference, frame_limit)
    return _decode_mono_into_buffer(audio_reference, frame_count)


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
        except (OSError, ValueError, RuntimeError, MemoryError) as exc:
            # A decode that runs out of memory fails this one file. The batch continues.
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
