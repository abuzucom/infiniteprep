"""Synthetic audio fixture factory for deterministic WAV, MP3, FLAC, AIFF, and OGG tests.

soundfile writes the audio. Tags come from the spec-written byte builders in this package,
not from the codec under test, so a codec bug cannot hide behind a fixture built by the
same code.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf

from tests.fixtures.audio_container_bytes import iff_chunk, riff_chunk
from tests.fixtures.flac_bytes import LAST_BLOCK_FLAG, VORBIS_COMMENT, flac_block, vorbis_body
from tests.fixtures.id3v2_bytes import tag_bytes, utf16, v23_frame

DEFAULT_SAMPLE_RATE: Final[int] = 44100
ID3V23_MAJOR: Final[int] = 3
UTF16_ENCODING: Final[bytes] = b"\x01"
UTF16_TERMINATOR: Final[bytes] = b"\x00\x00"
FRONT_COVER_PICTURE_TYPE: Final[int] = 3
TRAKTOR_PRIV_PAYLOAD: Final[bytes] = b"PRT4_BINARY_BEATGRID_AND_CUES_PAYLOAD"
TRAKTOR_FLAC_PAYLOAD: Final[str] = "dHJha3RvcjRfYmluYXJ5X2N1ZV9ncmlkX3BheWxvYWQ="
FIXTURE_VENDOR: Final[str] = "InfinitePrep test fixture"
# Fixture tag keys and the ID3 text frame each one fills.
ID3_TEXT_FRAMES: Final[tuple[tuple[str, str], ...]] = (
    ("artist", "TPE1"),
    ("title", "TIT2"),
    ("mix_name", "TIT3"),
    ("remixer", "TPE4"),
    ("release_title", "TALB"),
    ("label", "TPUB"),
    ("bpm", "TBPM"),
    ("key", "TKEY"),
)
# RIFF and IFF files hold the form size in bytes 4 to 8, after the 4-byte form ID.
FORM_SIZE_SLICE: Final[slice] = slice(4, 8)
FORM_HEADER_SIZE: Final[int] = 8
FLAC_MAGIC_SIZE: Final[int] = 4
FLAC_BLOCK_HEADER_SIZE: Final[int] = 4
FLAC_BLOCK_TYPE_MASK: Final[int] = 0x7F


def synthesize_rhythmic_chord_pcm(
    *,
    duration_sec: float = 4.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bpm: float = 128.0,
    frequencies_hz: tuple[float, ...] = (220.0, 261.63, 329.63),
    dc_offset: float = 0.0,
    clip_signal: bool = False,
    leading_silence_sec: float = 0.0,
) -> np.ndarray:
    """Synthesize a stereo PCM signal with periodic kick pulses and harmonic chord tones."""
    if sample_rate <= 0 or duration_sec <= 0.0:
        raise ValueError("sample_rate and duration_sec must be positive; check fixture args.")
    total_samples = int(duration_sec * sample_rate)
    time_axis = np.arange(total_samples, dtype=np.float64) / float(sample_rate)

    harmonic_sum = np.zeros(total_samples, dtype=np.float64)
    if len(frequencies_hz) > 0:
        scale = 0.25 / float(len(frequencies_hz))
        for freq in frequencies_hz:
            harmonic_sum += scale * np.sin(2.0 * np.pi * freq * time_axis)

    beat_period_sec = 60.0 / bpm if bpm > 0.0 else 0.5
    phase_in_beat = np.mod(time_axis, beat_period_sec)
    kick_env = np.where(phase_in_beat < 0.04, np.exp(-phase_in_beat * 80.0), 0.0)
    kick_wave = 0.55 * kick_env * np.sin(2.0 * np.pi * 65.0 * time_axis)

    mono = harmonic_sum + kick_wave + dc_offset
    if leading_silence_sec > 0.0:
        silent_samples = min(total_samples, int(leading_silence_sec * sample_rate))
        mono[:silent_samples] = 0.0

    mono = np.clip(mono * 4.0, -1.0, 1.0) if clip_signal else np.clip(mono, -0.95, 0.95)
    return np.column_stack([mono, mono]).astype(np.float32)


def create_wav_fixture(
    file_path: Path,
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    subtype: str = "PCM_16",
    bpm: float = 128.0,
    frequencies_hz: tuple[float, ...] = (220.0, 261.63, 329.63),
    tags: dict[str, str] | None = None,
    include_traktor_frame: bool = False,
) -> Path:
    """Create a valid WAV file with optional ID3v2.3 tags and PRIV:TRAKTOR4 binary frame."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = synthesize_rhythmic_chord_pcm(
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        bpm=bpm,
        frequencies_hz=frequencies_hz,
    )
    sf.write(str(file_path), pcm, sample_rate, subtype=subtype, format="WAV")
    if tags or include_traktor_frame:
        tag = _build_id3_fixture_tag(tags or {}, include_traktor_frame)
        _append_form_chunk(file_path, riff_chunk(b"id3 ", tag), "<I")
    return file_path


def create_flac_fixture(
    file_path: Path,
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    subtype: str = "PCM_16",
    bpm: float = 128.0,
    frequencies_hz: tuple[float, ...] = (220.0, 261.63, 329.63),
    tags: dict[str, str] | None = None,
    include_traktor_comment: bool = False,
) -> Path:
    """Create a valid FLAC file with optional Vorbis comments and TRAKTOR4 field."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = synthesize_rhythmic_chord_pcm(
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        bpm=bpm,
        frequencies_hz=frequencies_hz,
    )
    sf.write(str(file_path), pcm, sample_rate, subtype=subtype, format="FLAC")
    if tags or include_traktor_comment:
        entries = [f"{key.upper()}={value}" for key, value in (tags or {}).items()]
        if include_traktor_comment:
            entries.append(f"TRAKTOR4={TRAKTOR_FLAC_PAYLOAD}")
        comment = vorbis_body(FIXTURE_VENDOR, *entries)
        file_path.write_bytes(_replace_flac_comment(file_path.read_bytes(), comment))
    return file_path


def create_aiff_fixture(
    file_path: Path,
    *,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    subtype: str = "PCM_16",
    tags: dict[str, str] | None = None,
    include_traktor_frame: bool = False,
) -> Path:
    """Create a valid AIFF file with optional ID3v2.3 tags and PRIV:TRAKTOR4 frame."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = synthesize_rhythmic_chord_pcm(duration_sec=duration_sec, sample_rate=sample_rate)
    sf.write(str(file_path), pcm, sample_rate, subtype=subtype, format="AIFF")
    if tags or include_traktor_frame:
        tag = _build_id3_fixture_tag(tags or {}, include_traktor_frame)
        _append_form_chunk(file_path, iff_chunk(b"ID3 ", tag), ">I")
    return file_path


def create_mp3_fixture(
    file_path: Path,
    num_frames: int = 120,
    tags: dict[str, str] | None = None,
    include_traktor_frame: bool = False,
) -> Path:
    """Create a valid MPEG-1 Layer 3 (128kbps, 44.1kHz stereo) file with optional ID3 tags."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # Valid MPEG1 Layer 3 128kbps 44100Hz Stereo frame (417 bytes per frame)
    frame_header = bytes([0xFF, 0xFB, 0x90, 0x64])
    frame_body = bytes(413)
    single_frame = frame_header + frame_body
    audio = single_frame * num_frames
    if tags or include_traktor_frame:
        audio = _build_id3_fixture_tag(tags or {}, include_traktor_frame) + audio
    file_path.write_bytes(audio)
    return file_path


def create_ogg_fixture(
    file_path: Path,
    duration_sec: float = 2.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> Path:
    """Create a valid OGG Vorbis file for read-only and XDJ-RX3 warning tests."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = synthesize_rhythmic_chord_pcm(duration_sec=duration_sec, sample_rate=sample_rate)
    sf.write(str(file_path), pcm, sample_rate, format="OGG", subtype="VORBIS")
    return file_path


def _build_id3_fixture_tag(tags: dict[str, str], include_traktor_frame: bool) -> bytes:
    """Build an ID3v2.3 tag with UTF-16 text frames, TXXX, PRIV:TRAKTOR4, and APIC."""
    frames = [
        v23_frame(frame_id, UTF16_ENCODING + utf16(tags[key]))
        for key, frame_id in ID3_TEXT_FRAMES
        if key in tags
    ]
    if "custom_tag" in tags:
        body = utf16("CUSTOM_DJ_NOTE") + UTF16_TERMINATOR + utf16(tags["custom_tag"])
        frames.append(v23_frame("TXXX", UTF16_ENCODING + body))
    if include_traktor_frame:
        frames.append(v23_frame("PRIV", b"TRAKTOR4\x00" + TRAKTOR_PRIV_PAYLOAD))
    if "artwork_bytes" in tags:
        picture = b"image/jpeg\x00" + bytes((FRONT_COVER_PICTURE_TYPE,))
        picture += utf16("Cover") + UTF16_TERMINATOR + tags["artwork_bytes"].encode("latin1")
        frames.append(v23_frame("APIC", UTF16_ENCODING + picture))
    return tag_bytes(ID3V23_MAJOR, b"".join(frames))


def _append_form_chunk(file_path: Path, chunk: bytes, size_format: str) -> None:
    """Append chunk to a RIFF or IFF file and rewrite the form size in size_format order."""
    data = bytearray(file_path.read_bytes()) + chunk
    data[FORM_SIZE_SLICE] = struct.pack(size_format, len(data) - FORM_HEADER_SIZE)
    file_path.write_bytes(bytes(data))


def _replace_flac_comment(data: bytes, comment: bytes) -> bytes:
    """Return FLAC data with every Vorbis comment block replaced by one comment block.

    The new block goes right after STREAMINFO. Only the final block keeps the last flag.
    """
    offset = FLAC_MAGIC_SIZE
    blocks: list[tuple[int, bytes]] = []
    while True:
        header = data[offset]
        length = int.from_bytes(data[offset + 1 : offset + FLAC_BLOCK_HEADER_SIZE], "big")
        body = data[offset + FLAC_BLOCK_HEADER_SIZE : offset + FLAC_BLOCK_HEADER_SIZE + length]
        offset += FLAC_BLOCK_HEADER_SIZE + length
        if header & FLAC_BLOCK_TYPE_MASK != VORBIS_COMMENT:
            blocks.append((header & FLAC_BLOCK_TYPE_MASK, body))
        if header & LAST_BLOCK_FLAG:
            break
    blocks.insert(1, (VORBIS_COMMENT, comment))
    rendered = [
        flac_block(block_type, body, last=index == len(blocks) - 1)
        for index, (block_type, body) in enumerate(blocks)
    ]
    return data[:FLAC_MAGIC_SIZE] + b"".join(rendered) + data[offset:]
