"""Behavioral tests for reading audio stream properties from MP3, WAV, AIFF, and FLAC bytes."""

from __future__ import annotations

import struct

import pytest

from infiniteprep.adapters.metadata_io.audio_properties import (
    AudioPropertiesError,
    read_aiff_stream,
    read_flac_stream,
    read_mp3_properties,
    read_wav_stream,
)
from tests.fixtures.audio_container_bytes import (
    ID3V1_TAG,
    PCM_SAMPLES,
    WAV_FMT_BODY,
    basic_aiff,
    iff_chunk,
    riff_chunk,
    wav_file,
)
from tests.fixtures.flac_bytes import VORBIS_COMMENT, flac_block, vorbis_body
from tests.fixtures.id3v2_bytes import tag_bytes, utf16, v23_frame

RATE_44K = 44_100
RATE_22K = 22_050
BITS_PER_BYTE = 8
# MPEG-1 Layer III, 128 kbps, 44.1 kHz, no padding. Frames of this header are 417 bytes.
MPEG1_STEREO_HEADER = b"\xff\xfb\x90\x64"
MPEG1_MONO_HEADER = b"\xff\xfb\x90\xc4"
MPEG1_FRAME_BYTES = 417
MPEG1_BITRATE = 128_000
MPEG1_SAMPLES_PER_FRAME = 1152
# MPEG-2 Layer III, 64 kbps, 22.05 kHz, no padding. Frames of this header are 208 bytes.
MPEG2_HEADER = b"\xff\xf3\x80\x64"
MPEG2_FRAME_BYTES = 208
MPEG2_BITRATE = 64_000
# Side information ends 32 bytes after an MPEG-1 stereo frame header.
XING_OFFSET = 4 + 32
VBRI_OFFSET = 4 + 32
FRAME_COUNT = 120
VBR_FRAMES = 1000
VBR_BYTES = 400_000
FLAC_TOTAL_SAMPLES = 441_000
FLAC_BITS = 24
FLAC_SECONDS = 10


def mpeg_frames(header: bytes, frame_bytes: int, count: int = FRAME_COUNT) -> bytes:
    """Build count MPEG frames of one header, with zeroed payloads."""
    return (header + bytes(frame_bytes - len(header))) * count


def frame_with_marker(marker: bytes, at: int) -> bytes:
    """Build one MPEG-1 stereo frame with marker bytes placed at offset at."""
    frame = bytearray(mpeg_frames(MPEG1_STEREO_HEADER, MPEG1_FRAME_BYTES, 1))
    frame[at : at + len(marker)] = marker
    return bytes(frame)


def streaminfo(sample_rate: int, channels: int, bits: int, total: int) -> bytes:
    """Build a 34-byte FLAC STREAMINFO body with the given stream values."""
    packed = sample_rate << 44 | (channels - 1) << 41 | (bits - 1) << 36 | total
    return struct.pack(">HH", 4096, 4096) + bytes(6) + struct.pack(">Q", packed) + bytes(16)


def test_cbr_mp3_duration_comes_from_audio_bytes_over_bitrate() -> None:
    """An MP3 without a VBR header uses audio bytes x 8 over the first frame bitrate."""
    data = mpeg_frames(MPEG1_STEREO_HEADER, MPEG1_FRAME_BYTES)
    props = read_mp3_properties(data)
    expected = FRAME_COUNT * MPEG1_FRAME_BYTES * BITS_PER_BYTE / MPEG1_BITRATE
    assert props.duration == pytest.approx(expected)
    assert (props.sample_rate, props.channels, props.bitrate) == (RATE_44K, 2, MPEG1_BITRATE)
    assert props.bits_per_sample is None


def test_leading_id3_and_trailing_id3v1_are_not_counted_as_audio() -> None:
    """The ID3v2 tag before the frames and the ID3v1 tag after them do not lengthen the track."""
    audio = mpeg_frames(MPEG1_STEREO_HEADER, MPEG1_FRAME_BYTES)
    tag = tag_bytes(3, v23_frame("TIT2", b"\x01" + utf16("Song")))
    props = read_mp3_properties(tag + audio + ID3V1_TAG)
    assert props.duration == pytest.approx(read_mp3_properties(audio).duration)


def test_mono_mpeg1_header_reports_one_channel() -> None:
    """Channel mode 3 in the frame header means one channel."""
    props = read_mp3_properties(mpeg_frames(MPEG1_MONO_HEADER, MPEG1_FRAME_BYTES))
    assert props.channels == 1


def test_mpeg2_layer3_uses_its_own_rate_and_bitrate_tables() -> None:
    """An MPEG-2 Layer III header reads 22.05 kHz and 64 kbps from the MPEG-2 tables."""
    props = read_mp3_properties(mpeg_frames(MPEG2_HEADER, MPEG2_FRAME_BYTES))
    assert (props.sample_rate, props.bitrate) == (RATE_22K, MPEG2_BITRATE)
    expected = FRAME_COUNT * MPEG2_FRAME_BYTES * BITS_PER_BYTE / MPEG2_BITRATE
    assert props.duration == pytest.approx(expected)


@pytest.mark.parametrize("marker", [b"Xing", b"Info"])
def test_xing_or_info_frame_count_sets_duration_and_bitrate(marker: bytes) -> None:
    """A Xing or Info header with frame and byte counts gives exact VBR timing."""
    header = marker + struct.pack(">III", 3, VBR_FRAMES, VBR_BYTES)
    audio = mpeg_frames(MPEG1_STEREO_HEADER, MPEG1_FRAME_BYTES)
    props = read_mp3_properties(frame_with_marker(header, XING_OFFSET) + audio)
    seconds = VBR_FRAMES * MPEG1_SAMPLES_PER_FRAME / RATE_44K
    assert props.duration == pytest.approx(seconds)
    assert props.bitrate == int(VBR_BYTES * BITS_PER_BYTE / seconds)


def test_vbri_frame_count_sets_duration() -> None:
    """A Fraunhofer VBRI header 32 bytes after the frame header gives the frame count."""
    header = b"VBRI" + struct.pack(">HHHII", 1, 0, 75, VBR_BYTES, VBR_FRAMES)
    audio = mpeg_frames(MPEG1_STEREO_HEADER, MPEG1_FRAME_BYTES)
    props = read_mp3_properties(frame_with_marker(header, VBRI_OFFSET) + audio)
    assert props.duration == pytest.approx(VBR_FRAMES * MPEG1_SAMPLES_PER_FRAME / RATE_44K)


def test_false_sync_before_the_audio_is_skipped() -> None:
    """A stray 0xFF byte that does not start a frame chain is not taken as the first frame."""
    audio = mpeg_frames(MPEG1_STEREO_HEADER, MPEG1_FRAME_BYTES)
    props = read_mp3_properties(b"\x00\xff\xfb\x00\x00" + audio)
    assert props.bitrate == MPEG1_BITRATE


@pytest.mark.parametrize("data", [b"", bytes(4096), b"not an mp3 file at all" * 50])
def test_data_without_mpeg_frames_raises(data: bytes) -> None:
    """No valid frame header in the searched range raises AudioPropertiesError."""
    with pytest.raises(AudioPropertiesError):
        read_mp3_properties(data)


def test_wav_properties_come_from_fmt_and_data_chunks() -> None:
    """Rate, channels, and bit depth come from fmt. Duration is data bytes over block align."""
    data = wav_file(riff_chunk(b"fmt ", WAV_FMT_BODY), riff_chunk(b"data", PCM_SAMPLES))
    stream = read_wav_stream(data)
    frames = len(PCM_SAMPLES) // 4
    assert stream.properties.duration == pytest.approx(frames / RATE_44K)
    assert stream.properties.bitrate == 2 * 16 * RATE_44K
    assert (stream.properties.channels, stream.properties.bits_per_sample) == (2, 16)
    assert stream.tag_body is None


def test_wav_id3_chunk_body_is_returned_without_the_audio() -> None:
    """The id3 chunk body comes back as the tag bytes, whatever its position."""
    tag = tag_bytes(3, v23_frame("TIT2", b"\x01" + utf16("Song")))
    for chunk_id in (b"id3 ", b"ID3 "):
        data = wav_file(
            riff_chunk(b"fmt ", WAV_FMT_BODY),
            riff_chunk(b"data", PCM_SAMPLES),
            riff_chunk(chunk_id, tag),
        )
        assert read_wav_stream(data).tag_body == tag


def test_wav_data_chunk_longer_than_the_file_is_clamped() -> None:
    """A data chunk size past the end, as in a truncated recording, counts only real bytes."""
    payload = b"WAVE" + riff_chunk(b"fmt ", WAV_FMT_BODY)
    payload += b"data" + struct.pack("<I", 0xFFFFFFF0) + PCM_SAMPLES
    stream = read_wav_stream(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    assert stream.properties.duration == pytest.approx(len(PCM_SAMPLES) // 4 / RATE_44K)


@pytest.mark.parametrize(
    "data",
    [
        b"RIFX" + bytes(40),
        wav_file(riff_chunk(b"data", PCM_SAMPLES)),
        wav_file(riff_chunk(b"fmt ", bytes(8))),
    ],
)
def test_wav_without_a_usable_fmt_chunk_raises(data: bytes) -> None:
    """A non-RIFF file, a missing fmt chunk, or a short fmt chunk raises."""
    with pytest.raises(AudioPropertiesError):
        read_wav_stream(data)


@pytest.mark.parametrize("form_type", [b"AIFF", b"AIFC"])
def test_aiff_properties_come_from_the_comm_chunk(form_type: bytes) -> None:
    """COMM gives channels, frame count, bit depth, and the 80-bit extended sample rate."""
    tag = tag_bytes(3, v23_frame("TIT2", b"\x01" + utf16("Song")))
    stream = read_aiff_stream(basic_aiff(iff_chunk(b"ID3 ", tag), form_type=form_type))
    props = stream.properties
    assert props.sample_rate == RATE_44K
    assert props.duration == pytest.approx(1024 / RATE_44K)
    assert (props.channels, props.bits_per_sample, props.bitrate) == (2, 16, 2 * 16 * RATE_44K)
    assert stream.tag_body == tag


def test_aiff_without_comm_raises() -> None:
    """An AIFF form with no COMM chunk raises AudioPropertiesError."""
    data = b"FORM" + struct.pack(">I", 4) + b"AIFF"
    with pytest.raises(AudioPropertiesError):
        read_aiff_stream(data)


def test_flac_properties_come_from_streaminfo() -> None:
    """STREAMINFO gives the rate, channels, bit depth, and sample count of the stream."""
    info = streaminfo(RATE_44K, 2, FLAC_BITS, FLAC_TOTAL_SAMPLES)
    audio = bytes(range(256)) * 40
    data = b"fLaC" + flac_block(0, info, last=True) + audio
    props = read_flac_stream(data).properties
    assert props.duration == pytest.approx(FLAC_SECONDS)
    assert (props.sample_rate, props.channels, props.bits_per_sample) == (RATE_44K, 2, FLAC_BITS)
    assert props.bitrate == len(audio) * BITS_PER_BYTE // FLAC_SECONDS


def test_flac_comment_body_is_returned_and_a_leading_id3_tag_is_skipped() -> None:
    """The first Vorbis comment body comes back, and a leading ID3v2 tag does not stop the read."""
    info = streaminfo(RATE_44K, 2, 16, FLAC_TOTAL_SAMPLES)
    comment = vorbis_body("vendor", "TITLE=Song")
    id3 = tag_bytes(3, v23_frame("TIT2", b"\x01" + utf16("Song")))
    data = id3 + b"fLaC" + flac_block(0, info) + flac_block(VORBIS_COMMENT, comment, last=True)
    stream = read_flac_stream(data + bytes(64))
    assert stream.tag_body == comment
    assert stream.properties.duration == pytest.approx(FLAC_SECONDS)


@pytest.mark.parametrize(
    "data",
    [
        b"OggS" + bytes(60),
        b"fLaC" + flac_block(4, b"x" * 10, last=True),
        b"fLaC" + b"\x80\x00\x00\x22" + bytes(10),
    ],
)
def test_flac_without_a_valid_streaminfo_raises(data: bytes) -> None:
    """A missing magic, a first block other than STREAMINFO, or a truncated block raises."""
    with pytest.raises(AudioPropertiesError):
        read_flac_stream(data)


def test_audio_properties_error_is_a_value_error() -> None:
    """Callers that catch ValueError also catch stream damage."""
    assert issubclass(AudioPropertiesError, ValueError)
