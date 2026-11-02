"""Behavioral tests for MutagenAdapter.read_metadata on the in-house codecs, using real files."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.mutagen_adapter import (
    EXTENSION_TO_FORMAT,
    MAPPED_PARSERS,
    MutagenAdapter,
)
from infiniteprep.domain.enums import AudioFormat
from tests.fixtures.audio_container_bytes import aiff_file, iff_chunk
from tests.fixtures.audio_factory import (
    TRAKTOR_FLAC_PAYLOAD,
    TRAKTOR_PRIV_PAYLOAD,
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_ogg_fixture,
    create_wav_fixture,
)
from tests.fixtures.id3v2_bytes import tag_bytes, utf16, v23_frame
from tests.fixtures.mp4_bytes import audio_entry, data_atom, esds, item, m4a_file, mvhd, trak, udta

TAGS = {"artist": "Kerri Chandler", "title": "Rain", "bpm": "124", "key": "8A", "label": "Madhouse"}
EXPECTED_STANDARD = {
    "artist": "Kerri Chandler",
    "title": "Rain",
    "bpm": "124",
    "key": "8A",
    "label": "Madhouse",
}
RATE_44K = 44_100
FIXTURE_SECONDS = 2.0
MP3_FRAMES = 120
MP3_FRAME_BYTES = 417
MP3_BITRATE = 128_000
BITS_PER_BYTE = 8
ARTWORK = "\xff\xd8 fake jpeg bytes"


@pytest.fixture
def adapter() -> MutagenAdapter:
    """Return a fresh adapter."""
    return MutagenAdapter()


def test_mp3_tags_timing_and_traktor_frame_read_without_mutagen(
    adapter: MutagenAdapter, tmp_path: Path
) -> None:
    """An MP3 reads its mapped fields, CBR timing, and a PRIV:TRAKTOR4 summary with a digest."""
    path = create_mp3_fixture(tmp_path / "track.mp3", tags=TAGS, include_traktor_frame=True)
    meta = adapter.read_metadata(path)
    assert meta.format == AudioFormat.MP3
    assert meta.standard_tags == EXPECTED_STANDARD
    expected = MP3_FRAMES * MP3_FRAME_BYTES * BITS_PER_BYTE / MP3_BITRATE
    assert meta.duration == pytest.approx(expected)
    assert (meta.sample_rate, meta.channels, meta.bitrate) == (RATE_44K, 2, MP3_BITRATE)
    body = b"TRAKTOR4\x00" + TRAKTOR_PRIV_PAYLOAD
    digest = hashlib.sha256(body).hexdigest()
    assert meta.raw_custom_tags["PRIV:TRAKTOR4"] == (
        f"<PRIV TRAKTOR4, {len(body)} bytes, sha256 {digest}>"
    )
    assert meta.warnings == []


def test_mp3_comment_and_custom_frames_keep_readable_keys(
    adapter: MutagenAdapter, tmp_path: Path
) -> None:
    """COMM decodes its text under COMM:<description>:<language>, and unknown TXXX stays raw."""
    comment = v23_frame("COMM", b"\x01eng" + utf16("") + b"\x00\x00" + utf16("Opener"))
    note = b"\x01" + utf16("CUSTOM_DJ_NOTE") + b"\x00\x00" + utf16("warmup")
    custom = v23_frame("TXXX", note)
    frames = create_mp3_fixture(tmp_path / "frames.mp3").read_bytes()
    path = tmp_path / "notes.mp3"
    path.write_bytes(tag_bytes(3, comment + custom) + frames)
    meta = adapter.read_metadata(path)
    assert meta.raw_custom_tags["COMM::eng"] == ["Opener"]
    assert meta.raw_custom_tags["TXXX:CUSTOM_DJ_NOTE"] == "warmup"
    assert meta.standard_tags == {}


def test_mp3_picture_summary_names_its_mime_type(adapter: MutagenAdapter, tmp_path: Path) -> None:
    """A cover frame appears under APIC:<description> with its MIME type and byte length."""
    path = create_mp3_fixture(tmp_path / "cover.mp3", tags={"artwork_bytes": ARTWORK})
    summary = adapter.read_metadata(path).raw_custom_tags["APIC:Cover"]
    assert summary.startswith("<APIC image/jpeg, ")
    assert " bytes, sha256 " in summary


@pytest.mark.parametrize(
    ("factory", "fmt"),
    [(create_wav_fixture, AudioFormat.WAV), (create_aiff_fixture, AudioFormat.AIFF)],
)
def test_pcm_containers_read_properties_and_id3_chunk(
    adapter: MutagenAdapter, tmp_path: Path, factory: Callable[..., Path], fmt: AudioFormat
) -> None:
    """WAV and AIFF read rate, channels, depth, duration, and the tags in their ID3 chunk."""
    suffix = ".wav" if fmt == AudioFormat.WAV else ".aiff"
    path = factory(tmp_path / f"track{suffix}", tags=TAGS, include_traktor_frame=True)
    meta = adapter.read_metadata(path)
    assert meta.format == fmt
    assert meta.standard_tags == EXPECTED_STANDARD
    assert meta.duration == pytest.approx(FIXTURE_SECONDS)
    assert (meta.sample_rate, meta.channels) == (RATE_44K, 2)
    assert meta.raw_custom_tags["_bits_per_sample"] == 16
    assert meta.raw_custom_tags["PRIV:TRAKTOR4"].startswith("<PRIV TRAKTOR4, ")


def test_flac_reads_streaminfo_and_vorbis_comment(adapter: MutagenAdapter, tmp_path: Path) -> None:
    """FLAC reads its STREAMINFO and maps Vorbis fields. Unmapped fields stay raw, lowercased."""
    path = create_flac_fixture(tmp_path / "track.flac", tags=TAGS, include_traktor_comment=True)
    meta = adapter.read_metadata(path)
    assert meta.format == AudioFormat.FLAC
    assert meta.duration == pytest.approx(FIXTURE_SECONDS)
    assert (meta.sample_rate, meta.channels) == (RATE_44K, 2)
    assert meta.raw_custom_tags["_bits_per_sample"] == 16
    assert meta.standard_tags["artist"] == "Kerri Chandler"
    assert meta.standard_tags["title"] == "Rain"
    assert meta.raw_custom_tags["traktor4"] == [TRAKTOR_FLAC_PAYLOAD]


def test_m4a_reads_properties_and_keeps_items_raw(adapter: MutagenAdapter, tmp_path: Path) -> None:
    """M4A stays read-only: properties come from moov and every ilst item stays raw."""
    track = trak(b"soun", audio_entry(b"mp4a", 2, RATE_44K, esds(256_000)))
    tags = udta(item(b"\xa9nam", data_atom(1, b"Rain")))
    path = tmp_path / "track.m4a"
    path.write_bytes(m4a_file(mvhd(RATE_44K, RATE_44K * 3), track, tags))
    meta = adapter.read_metadata(path)
    assert meta.duration == pytest.approx(3.0)
    assert (meta.sample_rate, meta.channels, meta.bitrate) == (RATE_44K, 2, 256_000)
    assert meta.raw_custom_tags["\xa9nam"] == "['Rain']"
    assert meta.standard_tags == {}


def test_ogg_reads_properties_and_keeps_the_xdj_warning(
    adapter: MutagenAdapter, tmp_path: Path
) -> None:
    """Ogg Vorbis reads its stream properties and still warns that the XDJ-RX3 cannot play it."""
    path = create_ogg_fixture(tmp_path / "track.ogg")
    meta = adapter.read_metadata(path)
    assert meta.format == AudioFormat.OGG
    assert meta.duration == pytest.approx(FIXTURE_SECONDS, abs=0.05)
    assert (meta.sample_rate, meta.channels) == (RATE_44K, 2)
    assert any("OGG" in warning for warning in meta.warnings)


HUGE_RATE_COMM = struct.pack(">hIh", 2, 1024, 16) + struct.pack(">HQ", 0x7FFE, 1 << 63)


def test_every_detectable_format_has_a_reader(adapter: MutagenAdapter, tmp_path: Path) -> None:
    """Each extension detect_format maps reaches a parser, so no lookup can raise KeyError."""
    for extension in EXTENSION_TO_FORMAT:
        path = tmp_path / f"empty{extension}"
        path.write_bytes(b"")
        meta = adapter.read_metadata(path)
        assert meta.format == EXTENSION_TO_FORMAT[extension]
        assert meta.warnings[0].startswith("Damaged or unreadable audio container")
    assert set(EXTENSION_TO_FORMAT.values()) == {*MAPPED_PARSERS, AudioFormat.OGG}


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("empty.mp3", b""),
        ("noise.mp3", b"not audio" * 100),
        ("short.wav", b"RIFF\x04\x00"),
        ("huge-rate.aiff", aiff_file(iff_chunk(b"COMM", HUGE_RATE_COMM))),
        ("broken.ogg", b"OggS" + bytes(10)),
        ("broken.m4a", b"\x00\x00\x00\x08ftyp" + bytes(16)),
    ],
)
def test_empty_or_damaged_files_report_a_warning(
    adapter: MutagenAdapter, tmp_path: Path, name: str, content: bytes
) -> None:
    """A file that does not parse returns no properties and one damage warning."""
    path = tmp_path / name
    path.write_bytes(content)
    meta = adapter.read_metadata(path)
    assert (meta.duration, meta.sample_rate, meta.channels, meta.bitrate) == (None,) * 4
    assert len(meta.warnings) == 1
    assert meta.warnings[0].startswith("Damaged or unreadable audio container")
