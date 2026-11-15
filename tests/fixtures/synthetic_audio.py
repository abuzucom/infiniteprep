"""Seeded synthetic audio for tests, built at test time with no real music and no downloads.

Signals come from closed-form math and numpy's PCG64 generator, so one seed gives one result.
Container bytes follow the published layouts and reuse the byte builders in this package.
Codec payloads are seeded filler that the metadata readers never decode. Opus is the exception:
each Opus packet is one TOC byte, which a decoder treats as an empty frame.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf

from tests.fixtures.mp4_bytes import alac_config, atom, audio_entry, esds, m4a_file, mvhd, trak
from tests.fixtures.ogg_bytes import (
    make_ogg_page,
    make_opus_head_header,
    make_opus_tags_header,
    make_vorbis_comment_header,
    make_vorbis_id_header,
)

DEFAULT_SAMPLE_RATE: Final[int] = 44100
DEFAULT_SEED: Final[int] = 0
SECONDS_PER_MINUTE: Final[float] = 60.0

CLICK_DURATION_SEC: Final[float] = 0.01
CLICK_DECAY_PER_SEC: Final[float] = 400.0
CLICK_FREQ_HZ: Final[float] = 1000.0
CLICK_GAIN: Final[float] = 0.4
ACCENT_FREQ_HZ: Final[float] = 2000.0
ACCENT_GAIN: Final[float] = 0.8
DEFAULT_BEATS_PER_BAR: Final[int] = 4
DEFAULT_TONE_AMPLITUDE: Final[float] = 0.5
DEFAULT_NOISE_AMPLITUDE: Final[float] = 0.25
# MP3 encoders at common bitrates cut content near 16 kHz, which fake-lossless checks look for.
MP3_LIKE_CUTOFF_HZ: Final[float] = 16000.0
DEFAULT_SHELF_ATTENUATION_DB: Final[float] = 80.0
DECIBELS_PER_AMPLITUDE_DECADE: Final[float] = 20.0

# ADTS sampling_frequency_index values from ISO/IEC 14496-3, table 1.18.
ADTS_SAMPLE_RATE_INDEX: Final[dict[int, int]] = {
    96000: 0, 88200: 1, 64000: 2, 48000: 3, 44100: 4, 32000: 5, 24000: 6,
    22050: 7, 16000: 8, 12000: 9, 11025: 10, 8000: 11, 7350: 12,
}  # fmt: skip
ADTS_HEADER_SIZE: Final[int] = 7
# Field shifts count from the least significant bit of the 56-bit header. The fields from
# most to least significant: syncword (12 bits), ID (1), layer (2), protection_absent (1),
# profile (2), sampling index (4), private (1), channel config (3), four copy flags (1 each),
# frame length (13), buffer fullness (11), and raw block count (2).
ADTS_SYNCWORD_SHIFT: Final[int] = 44
ADTS_PROTECTION_ABSENT_SHIFT: Final[int] = 40
ADTS_PROFILE_SHIFT: Final[int] = 38
ADTS_RATE_INDEX_SHIFT: Final[int] = 34
ADTS_CHANNELS_SHIFT: Final[int] = 30
ADTS_FRAME_LENGTH_SHIFT: Final[int] = 13
ADTS_FULLNESS_SHIFT: Final[int] = 2
ADTS_SYNCWORD: Final[int] = 0xFFF
ADTS_AAC_LC_PROFILE: Final[int] = 1
ADTS_MAX_FRAME_LENGTH: Final[int] = 0x1FFF
ADTS_MAX_CHANNEL_CONFIG: Final[int] = 7
# Buffer fullness 0x7FF marks a variable-rate stream.
ADTS_VBR_FULLNESS: Final[int] = 0x7FF
DEFAULT_ADTS_PAYLOAD_SIZE: Final[int] = 256

AAC_FRAME_SAMPLES: Final[int] = 1024
ALAC_FRAME_SAMPLES: Final[int] = 4096
BITS_PER_BYTE: Final[int] = 8
DEFAULT_AAC_BITRATE: Final[int] = 256000
# Lossless audio typically compresses to about 60 percent of the PCM rate.
ALAC_COMPRESSION_RATIO: Final[float] = 0.6
DEFAULT_STEM_NAMES: Final[tuple[str, ...]] = ("Drums", "Bass", "Other", "Vocals")
STEM_COLORS: Final[tuple[str, ...]] = ("#009E73", "#D55E00", "#CC79A7", "#56B4E9")
STEM_MANIFEST_VERSION: Final[int] = 1

OGG_CRC_POLYNOMIAL: Final[int] = 0x04C11DB7
OGG_CRC_SLICE: Final[slice] = slice(22, 26)
OGG_BEGIN_OF_STREAM: Final[int] = 0x02
OGG_END_OF_STREAM: Final[int] = 0x04
# One lacing value per packet below 255 bytes, and a page holds at most 255 lacing values.
OGG_MAX_PACKETS_PER_PAGE: Final[int] = 255
UINT32_LIMIT: Final[int] = 2**32
UINT32_MASK: Final[int] = 0xFFFFFFFF
UINT32_TOP_BIT: Final[int] = 0x80000000
VORBIS_SETUP_PACKET: Final[bytes] = b"\x05vorbis\x01"
VORBIS_PACKET_SAMPLES: Final[int] = 1024
VORBIS_FILLER_PACKET_SIZE: Final[int] = 32
# Bit 0 of a Vorbis packet's first byte is 0 for audio packets and 1 for header packets.
VORBIS_AUDIO_TYPE_MASK: Final[int] = 0xFE
OPUS_CLOCK_RATE: Final[int] = 48000
OPUS_PRE_SKIP: Final[int] = 312
OPUS_PACKET_SAMPLES: Final[int] = 960
# TOC config 31 is CELT fullband with 20 ms frames, frame count code 0.
OPUS_TOC_CELT_FB_20MS: Final[int] = 0xF8
OPUS_TOC_STEREO: Final[int] = 0x04
STEREO_CHANNELS: Final[int] = 2


@dataclass(frozen=True)
class TempoChange:
    """A tempo that applies from at_sec onward."""

    at_sec: float
    bpm: float


def _sample_count(duration_sec: float, sample_rate: int) -> int:
    """Return the sample count for duration_sec, raising ValueError on non-positive input."""
    if sample_rate <= 0 or duration_sec <= 0.0:
        raise ValueError("sample_rate and duration_sec must be positive; check fixture args.")
    return round(duration_sec * sample_rate)


def _rng(seed: int) -> np.random.Generator:
    """Return a PCG64 generator, whose stream is the same on every platform for one seed."""
    return np.random.default_rng(seed)


def _tempo_at(time_sec: float, bpm: float, changes: tuple[TempoChange, ...]) -> float:
    """Return the tempo in effect at time_sec."""
    current = bpm
    for change in changes:
        if change.at_sec <= time_sec:
            current = change.bpm
    return current


def compute_click_onsets(
    *, bpm: float, duration_sec: float, tempo_changes: tuple[TempoChange, ...] = ()
) -> list[float]:
    """Return every beat time in seconds before duration_sec, honoring tempo changes."""
    changes = tuple(sorted(tempo_changes, key=lambda change: change.at_sec))
    if bpm <= 0.0 or any(change.bpm <= 0.0 for change in changes):
        raise ValueError("Every tempo must be positive; check bpm and tempo_changes.")
    onsets: list[float] = []
    time_sec = 0.0
    while time_sec < duration_sec:
        onsets.append(time_sec)
        time_sec += SECONDS_PER_MINUTE / _tempo_at(time_sec, bpm, changes)
    return onsets


def _click(freq_hz: float, gain: float, sample_rate: int) -> np.ndarray:
    """Return one decaying sine click."""
    time_axis = np.arange(int(CLICK_DURATION_SEC * sample_rate)) / sample_rate
    envelope = np.exp(-CLICK_DECAY_PER_SEC * time_axis)
    return gain * envelope * np.sin(2.0 * np.pi * freq_hz * time_axis)


def synthesize_click_track(
    *,
    bpm: float = 120.0,
    duration_sec: float = 4.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    tempo_changes: tuple[TempoChange, ...] = (),
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR,
) -> np.ndarray:
    """Return a mono click track with a louder, higher click on each downbeat."""
    if beats_per_bar <= 0:
        raise ValueError("beats_per_bar must be positive; check the fixture args.")
    signal = np.zeros(_sample_count(duration_sec, sample_rate), dtype=np.float64)
    accent = _click(ACCENT_FREQ_HZ, ACCENT_GAIN, sample_rate)
    beat = _click(CLICK_FREQ_HZ, CLICK_GAIN, sample_rate)
    onsets = compute_click_onsets(bpm=bpm, duration_sec=duration_sec, tempo_changes=tempo_changes)
    for index, onset in enumerate(onsets):
        _mix_at(signal, accent if index % beats_per_bar == 0 else beat, int(onset * sample_rate))
    return signal.astype(np.float32)


def _mix_at(signal: np.ndarray, sound: np.ndarray, start: int) -> None:
    """Add sound into signal from start, cutting it off at the end of signal."""
    span = min(len(sound), len(signal) - start)
    signal[start : start + span] += sound[:span]


def synthesize_tone(
    freq_hz: float,
    *,
    duration_sec: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    amplitude: float = DEFAULT_TONE_AMPLITUDE,
) -> np.ndarray:
    """Return a mono sine tone."""
    return synthesize_chord(
        (freq_hz,), duration_sec=duration_sec, sample_rate=sample_rate, amplitude=amplitude
    )


def synthesize_chord(
    freqs_hz: tuple[float, ...],
    *,
    duration_sec: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    amplitude: float = DEFAULT_TONE_AMPLITUDE,
) -> np.ndarray:
    """Return a mono sum of equal sines whose peak stays within amplitude."""
    if not freqs_hz:
        raise ValueError("A chord needs at least one frequency; pass freqs_hz.")
    time_axis = np.arange(_sample_count(duration_sec, sample_rate)) / sample_rate
    note_gain = amplitude / len(freqs_hz)
    signal = sum(note_gain * np.sin(2.0 * np.pi * freq * time_axis) for freq in freqs_hz)
    return np.asarray(signal, dtype=np.float32)


def synthesize_noise(
    *,
    duration_sec: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    seed: int = DEFAULT_SEED,
    amplitude: float = DEFAULT_NOISE_AMPLITUDE,
) -> np.ndarray:
    """Return mono full-band uniform white noise for seed."""
    count = _sample_count(duration_sec, sample_rate)
    return (amplitude * _rng(seed).uniform(-1.0, 1.0, count)).astype(np.float32)


def synthesize_band_limited_noise(
    *,
    low_hz: float,
    high_hz: float,
    duration_sec: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    seed: int = DEFAULT_SEED,
    amplitude: float = DEFAULT_NOISE_AMPLITUDE,
) -> np.ndarray:
    """Return mono noise for seed with every spectral bin outside low_hz to high_hz removed."""
    if not 0.0 <= low_hz < high_hz <= sample_rate / 2:
        raise ValueError("Band edges must satisfy 0 <= low_hz < high_hz <= Nyquist.")
    white = synthesize_noise(duration_sec=duration_sec, sample_rate=sample_rate, seed=seed)
    band = _keep_band(white, sample_rate, low_hz, high_hz)
    peak = float(np.abs(band).max())
    scale = amplitude / peak if peak > 0.0 else 0.0
    return (band * scale).astype(np.float32)


def _keep_band(signal: np.ndarray, sample_rate: int, low_hz: float, high_hz: float) -> np.ndarray:
    """Return signal in float64 with every spectral bin outside low_hz to high_hz zeroed."""
    spectrum = np.fft.rfft(np.asarray(signal, dtype=np.float64))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / sample_rate)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0.0
    return np.fft.irfft(spectrum, n=len(signal))


def apply_lowpass_shelf(
    signal: np.ndarray,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    cutoff_hz: float = MP3_LIKE_CUTOFF_HZ,
    attenuation_db: float = DEFAULT_SHELF_ATTENUATION_DB,
) -> np.ndarray:
    """Return signal with every bin above cutoff_hz lowered by attenuation_db, like MP3."""
    spectrum = np.fft.rfft(np.asarray(signal, dtype=np.float64))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / sample_rate)
    spectrum[freqs > cutoff_hz] *= 10.0 ** (-attenuation_db / DECIBELS_PER_AMPLITUDE_DECADE)
    return np.fft.irfft(spectrum, n=len(signal)).astype(np.float32)


def write_wav_fixture(
    file_path: Path,
    signal: np.ndarray,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    subtype: str = "PCM_16",
) -> Path:
    """Write signal as a WAV file through soundfile and return file_path."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(file_path), signal, sample_rate, subtype=subtype, format="WAV")
    return file_path


def _adts_header(rate_index: int, channels: int, frame_length: int) -> bytes:
    """Return a 7-byte MPEG-4 ADTS header for one AAC LC frame without CRC."""
    fields = (
        (ADTS_SYNCWORD, ADTS_SYNCWORD_SHIFT),
        (1, ADTS_PROTECTION_ABSENT_SHIFT),  # No CRC follows the header.
        (ADTS_AAC_LC_PROFILE, ADTS_PROFILE_SHIFT),
        (rate_index, ADTS_RATE_INDEX_SHIFT),
        (channels, ADTS_CHANNELS_SHIFT),
        (frame_length, ADTS_FRAME_LENGTH_SHIFT),
        (ADTS_VBR_FULLNESS, ADTS_FULLNESS_SHIFT),
    )
    value = 0
    for field, shift in fields:
        value |= field << shift
    return value.to_bytes(ADTS_HEADER_SIZE, "big")


def build_adts_bytes(
    *,
    frame_count: int = 43,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = STEREO_CHANNELS,
    payload_size: int = DEFAULT_ADTS_PAYLOAD_SIZE,
    seed: int = DEFAULT_SEED,
) -> bytes:
    """Return an ADTS AAC LC stream of frame_count frames with seeded filler payloads."""
    rate_index = ADTS_SAMPLE_RATE_INDEX.get(sample_rate)
    if rate_index is None:
        raise ValueError(f"ADTS has no index for sample rate {sample_rate}; use a listed rate.")
    if not 1 <= channels <= ADTS_MAX_CHANNEL_CONFIG:
        raise ValueError("ADTS channels must be 1 to 7; check the channels argument.")
    frame_length = ADTS_HEADER_SIZE + payload_size
    if payload_size <= 0 or frame_length > ADTS_MAX_FRAME_LENGTH:
        raise ValueError("ADTS payload_size must be positive and fit a 13-bit frame length.")
    header = _adts_header(rate_index, channels, frame_length)
    rng = _rng(seed)
    return b"".join(header + rng.bytes(payload_size) for _ in range(frame_count))


def _frame_sizes(
    total_samples: int, frame_samples: int, bitrate: int, sample_rate: int
) -> list[int]:
    """Return one equal byte size per codec frame for bitrate."""
    frame_bytes = max(1, bitrate * frame_samples // (BITS_PER_BYTE * sample_rate))
    return [frame_bytes] * math.ceil(total_samples / frame_samples)


def _single_track_m4a(
    entry: bytes, total: int, sample_rate: int, sizes: list[int], seed: int
) -> bytes:
    """Return an M4A holding one sound track for entry and a seeded filler mdat."""
    track = trak(b"soun", entry, sizes)
    return m4a_file(mvhd(sample_rate, total), track, mdat=_rng(seed).bytes(sum(sizes)))


def build_m4a_aac_bytes(
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = STEREO_CHANNELS,
    bit_depth: int = 16,
    bitrate: int = DEFAULT_AAC_BITRATE,
    seed: int = DEFAULT_SEED,
) -> bytes:
    """Return an M4A with one AAC sound track and a seeded filler mdat."""
    total = _sample_count(duration_sec, sample_rate)
    sizes = _frame_sizes(total, AAC_FRAME_SAMPLES, bitrate, sample_rate)
    entry = audio_entry(b"mp4a", channels, sample_rate, esds(bitrate), sample_size=bit_depth)
    return _single_track_m4a(entry, total, sample_rate, sizes, seed)


def build_m4a_alac_bytes(
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = STEREO_CHANNELS,
    bit_depth: int = 16,
    seed: int = DEFAULT_SEED,
) -> bytes:
    """Return an M4A with one ALAC sound track and a seeded filler mdat."""
    total = _sample_count(duration_sec, sample_rate)
    bitrate = int(sample_rate * channels * bit_depth * ALAC_COMPRESSION_RATIO)
    sizes = _frame_sizes(total, ALAC_FRAME_SAMPLES, bitrate, sample_rate)
    config = alac_config(sample_rate, channels, bit_depth, bitrate)
    entry = audio_entry(b"alac", channels, sample_rate, config, sample_size=bit_depth)
    return _single_track_m4a(entry, total, sample_rate, sizes, seed)


def build_stem_mp4_bytes(
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    stem_names: tuple[str, ...] = DEFAULT_STEM_NAMES,
    seed: int = DEFAULT_SEED,
) -> bytes:
    """Return a STEM.MP4: a mixdown track, one AAC track per stem, and a udta stem manifest."""
    total = _sample_count(duration_sec, sample_rate)
    sizes = _frame_sizes(total, AAC_FRAME_SAMPLES, DEFAULT_AAC_BITRATE, sample_rate)
    entry = audio_entry(b"mp4a", STEREO_CHANNELS, sample_rate, esds(DEFAULT_AAC_BITRATE))
    tracks = [trak(b"soun", entry, sizes) for _ in range(len(stem_names) + 1)]
    payload = _rng(seed).bytes(sum(sizes) * len(tracks))
    manifest = _stem_manifest_udta(stem_names)
    return m4a_file(mvhd(sample_rate, total), *tracks, manifest, mdat=payload)


def _stem_manifest_udta(stem_names: tuple[str, ...]) -> bytes:
    """Return a udta atom holding the stem JSON manifest that names and colors each stem."""
    stems = [
        {"name": name, "color": STEM_COLORS[index % len(STEM_COLORS)]}
        for index, name in enumerate(stem_names)
    ]
    manifest = {"version": STEM_MANIFEST_VERSION, "stems": stems}
    body = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    return atom(b"udta", atom(b"stem", body))


def _build_ogg_crc_table() -> tuple[int, ...]:
    """Return the CRC-32 lookup table for the Ogg polynomial, unreflected."""
    table: list[int] = []
    for index in range(256):
        crc = index << 24
        for _ in range(BITS_PER_BYTE):
            shifted = (crc << 1) & UINT32_MASK
            crc = shifted ^ OGG_CRC_POLYNOMIAL if crc & UINT32_TOP_BIT else shifted
        table.append(crc)
    return tuple(table)


OGG_CRC_TABLE: Final[tuple[int, ...]] = _build_ogg_crc_table()


def _ogg_crc(page: bytes) -> int:
    """Return the Ogg page checksum of page, whose CRC field holds zeros."""
    crc = 0
    for byte in page:
        crc = ((crc << BITS_PER_BYTE) & UINT32_MASK) ^ OGG_CRC_TABLE[(crc >> 24) ^ byte]
    return crc


def _ogg_page(serial: int, sequence: int, granule: int, packets: list[bytes], flags: int) -> bytes:
    """Return one Ogg page with its CRC filled in."""
    page = bytearray(make_ogg_page(serial, sequence, granule, packets, header_type=flags))
    page[OGG_CRC_SLICE] = _ogg_crc(bytes(page)).to_bytes(4, "little")
    return bytes(page)


@dataclass(frozen=True)
class _OggAudio:
    """Audio packets of one Ogg stream and the granule clock that places them."""

    packets: list[bytes]
    samples_per_packet: int
    granule_offset: int
    total_samples: int


def _ogg_page_plan(
    header_pages: list[list[bytes]], audio: _OggAudio
) -> list[tuple[list[bytes], int]]:
    """Return (packets, granule) per page: header pages at granule 0, then audio pages."""
    pages: list[tuple[list[bytes], int]] = [(packets, 0) for packets in header_pages]
    for start in range(0, len(audio.packets), OGG_MAX_PACKETS_PER_PAGE):
        chunk = audio.packets[start : start + OGG_MAX_PACKETS_PER_PAGE]
        done = min((start + len(chunk)) * audio.samples_per_packet, audio.total_samples)
        pages.append((chunk, audio.granule_offset + done))
    return pages


def _ogg_flags(sequence: int, last: int) -> int:
    """Return the BOS flag for the first page and the EOS flag for the last page."""
    begin = OGG_BEGIN_OF_STREAM if sequence == 0 else 0
    return begin | (OGG_END_OF_STREAM if sequence == last else 0)


def _ogg_stream(serial: int, header_pages: list[list[bytes]], audio: _OggAudio) -> bytes:
    """Return the rendered pages of one logical stream."""
    plan = _ogg_page_plan(header_pages, audio)
    last = len(plan) - 1
    return b"".join(
        _ogg_page(serial, sequence, granule, packets, _ogg_flags(sequence, last))
        for sequence, (packets, granule) in enumerate(plan)
    )


def _vorbis_filler_packets(rng: np.random.Generator, count: int) -> list[bytes]:
    """Return count seeded filler packets whose type bit marks them as audio packets."""
    packets = []
    for _ in range(count):
        filler = bytearray(rng.bytes(VORBIS_FILLER_PACKET_SIZE))
        filler[0] &= VORBIS_AUDIO_TYPE_MASK
        packets.append(bytes(filler))
    return packets


def build_ogg_vorbis_bytes(
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = STEREO_CHANNELS,
    seed: int = DEFAULT_SEED,
    fields: tuple[tuple[str, str], ...] = (),
) -> bytes:
    """Return an Ogg Vorbis stream with valid page CRCs and seeded filler audio packets."""
    total = _sample_count(duration_sec, sample_rate)
    rng = _rng(seed)
    serial = int(rng.integers(0, UINT32_LIMIT))
    packets = _vorbis_filler_packets(rng, math.ceil(total / VORBIS_PACKET_SAMPLES))
    headers = [
        [make_vorbis_id_header(channels, sample_rate)],
        [make_vorbis_comment_header("InfinitePrep fixture", list(fields)), VORBIS_SETUP_PACKET],
    ]
    return _ogg_stream(serial, headers, _OggAudio(packets, VORBIS_PACKET_SAMPLES, 0, total))


def build_ogg_opus_bytes(
    *,
    duration_sec: float = 2.0,
    channels: int = STEREO_CHANNELS,
    seed: int = DEFAULT_SEED,
    fields: tuple[tuple[str, str], ...] = (),
) -> bytes:
    """Return an Ogg Opus stream of empty 20 ms CELT frames with valid page CRCs."""
    if channels not in (1, STEREO_CHANNELS):
        raise ValueError("Opus mapping family 0 carries 1 or 2 channels; check channels.")
    total = _sample_count(duration_sec, OPUS_CLOCK_RATE)
    serial = int(_rng(seed).integers(0, UINT32_LIMIT))
    toc = OPUS_TOC_CELT_FB_20MS | (OPUS_TOC_STEREO if channels == STEREO_CHANNELS else 0)
    packets = [bytes((toc,))] * math.ceil(total / OPUS_PACKET_SAMPLES)
    headers = [
        [make_opus_head_header(channels, OPUS_PRE_SKIP, OPUS_CLOCK_RATE)],
        [make_opus_tags_header("InfinitePrep fixture", list(fields))],
    ]
    audio = _OggAudio(packets, OPUS_PACKET_SAMPLES, OPUS_PRE_SKIP, total)
    return _ogg_stream(serial, headers, audio)
