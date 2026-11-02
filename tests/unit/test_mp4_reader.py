"""Behavioral tests for reading M4A tags and audio properties from MP4 atom bytes."""

from __future__ import annotations

import struct

import pytest

from infiniteprep.adapters.metadata_io.mp4_reader import Mp4FormatError, read_mp4
from tests.fixtures.mp4_bytes import (
    alac_config,
    atom,
    audio_entry,
    data_atom,
    esds,
    freeform,
    item,
    large_atom,
    m4a_file,
    mvhd,
    trak,
    udta,
)

RATE_44K = 44_100
RATE_96K = 96_000
SECONDS = 180
DEFAULT_AAC_BITRATE = 256_000
BITS_PER_BYTE = 8
FALLBACK_SAMPLE_SIZE = 1000
FALLBACK_SAMPLE_COUNT = 100
FALLBACK_SECONDS = 10
# Sizes for the damaged-input cases: an ID3 body with no atoms, a short atom body, an atom
# size past the end of the data, and an atom size smaller than its own 8-byte header.
FOREIGN_BODY_SIZE = 60
SHORT_BODY_SIZE = 8
OVERSIZE_ATOM_SIZE = 500
BELOW_HEADER_SIZE = 4
M4A_FTYP = atom(b"ftyp", b"M4A ")


def aac_track(avg_bitrate: int = DEFAULT_AAC_BITRATE, sizes: list[int] | None = None) -> bytes:
    """Build a sound trak with an mp4a entry for 2-channel 44.1 kHz audio."""
    return trak(b"soun", audio_entry(b"mp4a", 2, RATE_44K, esds(avg_bitrate)), sizes)


def tag_map(data: bytes) -> dict[str, tuple[str, ...]]:
    """Read data and map each tag key to its values."""
    return {tag.key: tag.values for tag in read_mp4(data).tags}


def test_text_number_and_freeform_tags_decode() -> None:
    """UTF-8 text, track and disc pairs, integers, cover art, and freeform items decode."""
    tags = udta(
        item(b"\xa9nam", data_atom(1, "Ché Song".encode())),
        item(b"\xa9ART", data_atom(1, b"Artist")),
        item(b"trkn", data_atom(0, struct.pack(">HHHH", 0, 3, 12, 0))),
        item(b"disk", data_atom(0, struct.pack(">HHH", 0, 1, 2))),
        item(b"tmpo", data_atom(21, struct.pack(">H", 124))),
        item(b"covr", data_atom(13, b"\xff\xd8" + bytes(98))),
        freeform("com.apple.iTunes", "INITIALKEY", "8A"),
    )
    result = tag_map(m4a_file(mvhd(RATE_44K, RATE_44K * SECONDS), aac_track(), tags))
    assert result == {
        "\xa9nam": ("Ché Song",),
        "\xa9ART": ("Artist",),
        "trkn": ("3/12",),
        "disk": ("1/2",),
        "tmpo": ("124",),
        "covr": ("<cover image/jpeg 100 bytes>",),
        "----:com.apple.iTunes:INITIALKEY": ("8A",),
    }


def test_utf16_text_and_repeated_data_atoms_decode() -> None:
    """A UTF-16 data atom decodes, and an item with two data atoms keeps both values."""
    genres = item(b"\xa9gen", data_atom(2, "House".encode("utf-16-be")), data_atom(1, b"Techno"))
    result = tag_map(m4a_file(mvhd(RATE_44K, RATE_44K), aac_track(), udta(genres)))
    assert result["\xa9gen"] == ("House", "Techno")


def test_aac_properties_come_from_mvhd_and_the_sample_entry() -> None:
    """Duration, channels, sample rate, and the esds average bitrate are reported."""
    info = read_mp4(m4a_file(mvhd(RATE_44K, RATE_44K * SECONDS), aac_track(320_000)))
    assert info.duration == pytest.approx(SECONDS)
    assert (info.channels, info.sample_rate, info.bitrate) == (2, RATE_44K, 320_000)
    assert info.codec == "mp4a"
    assert info.encrypted is False


def test_version_1_mvhd_reads_a_64_bit_duration() -> None:
    """A version 1 mvhd with a duration past 32 bits still gives the right seconds."""
    timescale = 1_000_000
    long_seconds = 7_200
    info = read_mp4(m4a_file(mvhd(timescale, timescale * long_seconds, version=1), aac_track()))
    assert info.duration == pytest.approx(long_seconds)


def test_alac_properties_come_from_the_alac_config() -> None:
    """A 96 kHz ALAC entry reports its rate, channels, and bit depth from the alac atom."""
    entry = audio_entry(b"alac", 2, 0, alac_config(RATE_96K, 2, 24, 4_000_000), sample_size=24)
    info = read_mp4(m4a_file(mvhd(RATE_96K, RATE_96K * SECONDS), trak(b"soun", entry)))
    assert (info.codec, info.sample_rate, info.channels, info.bit_depth) == (
        "alac",
        RATE_96K,
        2,
        24,
    )
    assert info.bitrate == 4_000_000


def test_bitrate_falls_back_to_sample_sizes_over_duration() -> None:
    """With no esds average bitrate, bitrate is the sample size total over the duration."""
    sizes = [FALLBACK_SAMPLE_SIZE] * FALLBACK_SAMPLE_COUNT
    info = read_mp4(m4a_file(mvhd(RATE_44K, RATE_44K * FALLBACK_SECONDS), aac_track(0, sizes)))
    assert info.bitrate == sum(sizes) * BITS_PER_BYTE // FALLBACK_SECONDS


@pytest.mark.parametrize("entry_type", [b"drms", b"drmi", b"enca"])
def test_protected_sample_entry_marks_the_file_encrypted(entry_type: bytes) -> None:
    """FairPlay and common-encryption sample entries set encrypted."""
    entry = audio_entry(entry_type, 2, RATE_44K)
    info = read_mp4(m4a_file(mvhd(RATE_44K, RATE_44K), trak(b"soun", entry)))
    assert info.encrypted is True


def test_first_sound_track_wins_among_many_tracks() -> None:
    """Video and text tracks are skipped, with no limit on the number of tracks."""
    others = [trak(b"vide"), trak(b"text")] * 6
    stems = [trak(b"soun", audio_entry(b"mp4a", channels, RATE_44K)) for channels in (6, 2, 2)]
    info = read_mp4(m4a_file(mvhd(RATE_44K, RATE_44K), *others, *stems))
    assert info.channels == 6


def test_large_and_open_ended_atoms_are_walked() -> None:
    """A 64-bit largesize atom and a final size-0 atom do not stop the reader."""
    ftyp = atom(b"ftyp", b"M4A " + bytes(4))
    moov = atom(b"moov", mvhd(RATE_44K, RATE_44K * 2) + aac_track())
    data = ftyp + large_atom(b"free", bytes(32)) + moov + struct.pack(">I", 0) + b"mdat" + bytes(64)
    assert read_mp4(data).duration == pytest.approx(2)


def test_meta_without_version_and_flags_still_yields_tags() -> None:
    """A QuickTime-style meta atom with no version and flags still exposes its ilst."""
    tags = udta(item(b"\xa9nam", data_atom(1, b"QT")), full_meta=False)
    assert tag_map(m4a_file(mvhd(RATE_44K, RATE_44K), aac_track(), tags)) == {"\xa9nam": ("QT",)}


def test_file_without_ilst_has_no_tags() -> None:
    """A file with no udta reads with an empty tag list."""
    assert read_mp4(m4a_file(mvhd(RATE_44K, RATE_44K), aac_track())).tags == ()


@pytest.mark.parametrize(
    "data",
    [
        b"ID3\x03" + bytes(FOREIGN_BODY_SIZE),
        M4A_FTYP + atom(b"mdat", bytes(SHORT_BODY_SIZE)),
        M4A_FTYP + struct.pack(">I", OVERSIZE_ATOM_SIZE) + b"moov" + bytes(SHORT_BODY_SIZE),
        M4A_FTYP + struct.pack(">I", BELOW_HEADER_SIZE) + b"moov",
    ],
)
def test_foreign_or_damaged_data_raises(data: bytes) -> None:
    """No moov, an atom past the end, or an atom smaller than its header raises."""
    with pytest.raises(Mp4FormatError):
        read_mp4(data)


def test_mp4_format_error_is_a_value_error() -> None:
    """Callers that catch ValueError also catch MP4 damage."""
    assert issubclass(Mp4FormatError, ValueError)
