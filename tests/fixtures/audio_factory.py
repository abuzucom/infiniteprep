"""Synthetic audio fixture factory for deterministic WAV, MP3, FLAC, AIFF, and OGG tests."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import APIC, ID3, PRIV, TALB, TBPM, TIT2, TIT3, TKEY, TPE1, TPE4, TPUB, TXXX
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

DEFAULT_SAMPLE_RATE: Final[int] = 44100


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
        wav_audio = WAVE(str(file_path))
        if wav_audio.tags is None:
            wav_audio.add_tags()
        assert isinstance(wav_audio.tags, ID3)
        _populate_id3_fixture_tags(wav_audio.tags, tags or {}, include_traktor_frame)
        wav_audio.tags.save(str(file_path), v2_version=3)
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
        flac_audio = FLAC(str(file_path))
        for k, v in (tags or {}).items():
            flac_audio[k.upper()] = [v]
        if include_traktor_comment:
            flac_audio["TRAKTOR4"] = ["dHJha3RvcjRfYmluYXJ5X2N1ZV9ncmlkX3BheWxvYWQ="]
        flac_audio.save()
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
        aiff_audio = AIFF(str(file_path))
        if aiff_audio.tags is None:
            aiff_audio.add_tags()
        assert isinstance(aiff_audio.tags, ID3)
        _populate_id3_fixture_tags(aiff_audio.tags, tags or {}, include_traktor_frame)
        aiff_audio.save(v2_version=3)
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
    file_path.write_bytes(single_frame * num_frames)

    if tags or include_traktor_frame:
        mp3_audio = MP3(str(file_path))
        if mp3_audio.tags is None:
            mp3_audio.add_tags()
        assert isinstance(mp3_audio.tags, ID3)
        _populate_id3_fixture_tags(mp3_audio.tags, tags or {}, include_traktor_frame)
        mp3_audio.tags.save(str(file_path), v2_version=3)
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


def _populate_id3_fixture_tags(id3: ID3, tags: dict[str, str], include_traktor_frame: bool) -> None:
    """Populate ID3 frames for test fixtures using UTF-16 (encoding=1)."""
    if "artist" in tags:
        id3.add(TPE1(encoding=1, text=[tags["artist"]]))
    if "title" in tags:
        id3.add(TIT2(encoding=1, text=[tags["title"]]))
    if "mix_name" in tags:
        id3.add(TIT3(encoding=1, text=[tags["mix_name"]]))
    if "remixer" in tags:
        id3.add(TPE4(encoding=1, text=[tags["remixer"]]))
    if "release_title" in tags:
        id3.add(TALB(encoding=1, text=[tags["release_title"]]))
    if "label" in tags:
        id3.add(TPUB(encoding=1, text=[tags["label"]]))
    if "bpm" in tags:
        id3.add(TBPM(encoding=1, text=[tags["bpm"]]))
    if "key" in tags:
        id3.add(TKEY(encoding=1, text=[tags["key"]]))
    if "custom_tag" in tags:
        id3.add(TXXX(encoding=1, desc="CUSTOM_DJ_NOTE", text=[tags["custom_tag"]]))
    if include_traktor_frame:
        id3.add(PRIV(owner="TRAKTOR4", data=b"PRT4_BINARY_BEATGRID_AND_CUES_PAYLOAD"))
    if "artwork_bytes" in tags:
        id3.add(
            APIC(
                encoding=1,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=tags["artwork_bytes"].encode("latin1"),
            )
        )
