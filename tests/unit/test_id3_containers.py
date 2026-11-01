"""Behavioral tests for locating, reading, and replacing ID3v2 tags in MP3, WAV, and AIFF bytes."""

from __future__ import annotations

import struct

import pytest

from infiniteprep.adapters.metadata_io.id3_containers import (
    AudioContainerError,
    mp3_audio_offset,
    read_aiff_tag,
    read_mp3_tag,
    read_wav_tag,
    write_aiff_tag,
    write_mp3_tag,
    write_wav_tag,
)
from infiniteprep.adapters.metadata_io.id3v2 import (
    Id3Tag,
    decode_text_values,
    replace_text_frame,
)
from tests.fixtures.audio_container_bytes import (
    ID3V1_TAG,
    MP3_AUDIO,
    PCM_SAMPLES,
    WAV_FMT_BODY,
    basic_aiff,
    basic_wav,
    iff_chunk,
    riff_chunk,
    wav_file,
)
from tests.fixtures.id3v2_bytes import synchsafe, tag_bytes, v23_frame, v24_frame

TITLE_TAG = tag_bytes(3, v23_frame("TIT2", b"\x00Old Title"), padding=64)


def titled(title: str) -> Id3Tag:
    """Return a new tag holding one TIT2 frame."""
    return replace_text_frame(Id3Tag(), "TIT2", title)


def title_of(tag: Id3Tag | None) -> list[str]:
    """Return the TIT2 values of tag."""
    assert tag is not None
    titles = [frame for frame in tag.frames if frame.frame_id == "TIT2"]
    return [value for frame in titles for value in decode_text_values(frame)]


def riff_chunk_ids(data: bytes) -> list[bytes]:
    """List the chunk identifiers of a little-endian RIFF body in order."""
    ids: list[bytes] = []
    offset = 12
    while offset + 8 <= len(data):
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        ids.append(data[offset : offset + 4])
        offset += 8 + size + (size % 2)
    return ids


def test_mp3_without_tag_reads_none_and_gains_one_before_the_audio() -> None:
    """Writing a tag to a bare MP3 prepends it and keeps every audio byte."""
    assert read_mp3_tag(MP3_AUDIO) is None
    written = write_mp3_tag(MP3_AUDIO, titled("New"))
    assert written[mp3_audio_offset(written) :] == MP3_AUDIO
    assert title_of(read_mp3_tag(written)) == ["New"]


def test_mp3_smaller_tag_reuses_the_old_space() -> None:
    """A tag that fits in the old tag's bytes keeps the file size, so the audio does not move."""
    source = TITLE_TAG + MP3_AUDIO
    written = write_mp3_tag(source, titled("New"))
    assert len(written) == len(source)
    assert mp3_audio_offset(written) == len(TITLE_TAG)
    assert written[len(TITLE_TAG) :] == MP3_AUDIO
    assert title_of(read_mp3_tag(written)) == ["New"]


def test_mp3_larger_tag_moves_the_audio_intact() -> None:
    """A tag that outgrows the old space shifts the audio without changing it."""
    source = TITLE_TAG + MP3_AUDIO
    written = write_mp3_tag(source, titled("A much longer title " * 20))
    assert written[mp3_audio_offset(written) :] == MP3_AUDIO
    assert title_of(read_mp3_tag(written)) == ["A much longer title " * 20]


def test_mp3_keeps_a_trailing_id3v1_tag() -> None:
    """An ID3v1 tag at the end of the file stays byte for byte."""
    written = write_mp3_tag(TITLE_TAG + MP3_AUDIO + ID3V1_TAG, titled("New"))
    assert written.endswith(MP3_AUDIO + ID3V1_TAG)


def test_mp3_v24_footer_counts_toward_the_audio_offset() -> None:
    """A v2.4 tag with a footer ends after the footer, where the audio starts."""
    frames = v24_frame("TIT2", b"\x03Footer\x00")
    footer = b"3DI\x04\x00\x10" + synchsafe(len(frames))
    source = tag_bytes(4, frames, flags=0x10, padding=0) + footer + MP3_AUDIO
    assert mp3_audio_offset(source) == len(source) - len(MP3_AUDIO)
    assert title_of(read_mp3_tag(source)) == ["Footer"]


def test_mp3_stacked_tags_collapse_into_one() -> None:
    """Two leading tags in a row are both replaced, so the audio follows a single tag."""
    second = tag_bytes(3, v23_frame("TIT2", b"\x00Second"))
    written = write_mp3_tag(TITLE_TAG + second + MP3_AUDIO, titled("New"))
    offset = mp3_audio_offset(written)
    assert written[offset:] == MP3_AUDIO
    assert written[:offset].count(b"ID3") == 1


def test_wav_without_tag_gains_an_id3_chunk_and_a_correct_riff_size() -> None:
    """Writing to a WAV with no tag appends an id3 chunk and updates the RIFF size."""
    source = basic_wav()
    assert read_wav_tag(source) is None
    written = write_wav_tag(source, titled("New"))
    assert struct.unpack("<I", written[4:8])[0] == len(written) - 8
    assert riff_chunk_ids(written) == [b"fmt ", b"data", b"id3 "]
    assert written.startswith(source[:4]) and source[12:] in written
    assert title_of(read_wav_tag(written)) == ["New"]


@pytest.mark.parametrize("chunk_id", [b"id3 ", b"ID3 "])
def test_wav_replaces_an_existing_id3_chunk_in_place(chunk_id: bytes) -> None:
    """Either id3 chunk spelling is read and replaced where it was, and other chunks stay."""
    list_chunk = riff_chunk(b"LIST", b"INFOINAM\x05\x00\x00\x00Odd!\x00")
    source = basic_wav(riff_chunk(chunk_id, TITLE_TAG), list_chunk)
    assert title_of(read_wav_tag(source)) == ["Old Title"]
    written = write_wav_tag(source, titled("New"))
    assert riff_chunk_ids(written) == [b"fmt ", b"data", chunk_id, b"LIST"]
    assert written.endswith(list_chunk)
    assert title_of(read_wav_tag(written)) == ["New"]


def test_wav_duplicate_id3_chunks_become_one() -> None:
    """A WAV with two id3 chunks keeps only the first position after a write."""
    source = basic_wav(riff_chunk(b"id3 ", TITLE_TAG), riff_chunk(b"ID3 ", TITLE_TAG))
    written = write_wav_tag(source, titled("New"))
    assert riff_chunk_ids(written) == [b"fmt ", b"data", b"id3 "]


def test_wav_id3_chunk_before_data_keeps_the_samples_in_place() -> None:
    """A smaller tag in an id3 chunk ahead of the data chunk keeps the sample offset unchanged."""
    source = wav_file(
        riff_chunk(b"fmt ", WAV_FMT_BODY),
        riff_chunk(b"id3 ", TITLE_TAG),
        riff_chunk(b"data", PCM_SAMPLES),
    )
    written = write_wav_tag(source, titled("New"))
    assert written.index(PCM_SAMPLES) == source.index(PCM_SAMPLES)
    assert len(written) == len(source)


@pytest.mark.parametrize("form_type", [b"AIFF", b"AIFC"])
def test_aiff_reads_and_replaces_the_id3_chunk(form_type: bytes) -> None:
    """AIFF and AIFC files read and replace a big-endian ID3 chunk and update the FORM size."""
    source = basic_aiff(iff_chunk(b"ID3 ", TITLE_TAG), form_type=form_type)
    assert title_of(read_aiff_tag(source)) == ["Old Title"]
    written = write_aiff_tag(source, titled("New"))
    assert struct.unpack(">I", written[4:8])[0] == len(written) - 8
    assert written[8:12] == form_type
    assert PCM_SAMPLES in written
    assert title_of(read_aiff_tag(written)) == ["New"]


def test_aiff_without_tag_gains_an_id3_chunk() -> None:
    """Writing to an AIFF with no tag appends an ID3 chunk after the existing chunks."""
    source = basic_aiff()
    assert read_aiff_tag(source) is None
    written = write_aiff_tag(source, titled("New"))
    assert written[: len(source)][12:] == source[12:]
    assert title_of(read_aiff_tag(written)) == ["New"]


@pytest.mark.parametrize(
    "data",
    [
        b"RIFF\x04\x00\x00\x00AVI ",
        b"FORM\x04\x00\x00\x00WAVE",
        wav_file(
            riff_chunk(b"fmt ", WAV_FMT_BODY),
            b"data" + struct.pack("<I", 4096) + PCM_SAMPLES,
        ),
    ],
)
def test_wrong_container_or_overrunning_chunk_raises(data: bytes) -> None:
    """A RIFF that is not WAVE, a FORM that is not AIFF, or a chunk past the end raises."""
    reader = read_aiff_tag if data.startswith(b"FORM") else read_wav_tag
    with pytest.raises(AudioContainerError):
        reader(data)


def test_container_error_is_a_value_error() -> None:
    """Callers that catch ValueError also catch container damage."""
    assert issubclass(AudioContainerError, ValueError)
