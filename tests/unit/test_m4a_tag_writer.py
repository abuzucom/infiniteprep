"""Behavioral tests for writing iTunes-style tags to M4A bytes."""

from __future__ import annotations

import struct
from collections.abc import Callable

import pytest

from infiniteprep.adapters.metadata_io.audio_properties import find_m4a_payload
from infiniteprep.adapters.metadata_io.mp4_reader import read_mp4
from infiniteprep.adapters.metadata_io.mp4_writer import Mp4WriteError, write_m4a_tags
from tests.fixtures import synthetic_audio as sa
from tests.fixtures.mp4_bytes import (
    atom,
    audio_entry,
    data_atom,
    esds,
    ftyp,
    item,
    m4a_with_sound_track,
    mvhd,
    trak,
    udta,
)

RATE = 44_100
CHUNK_COUNT = 8
CHUNK_SIZE = 512
BITRATE = 256_000
MEDIA_BYTES = bytes(range(256)) * 64
# The writer refuses a value above this size and a file that nests its atoms too deeply.
MAX_TAG_VALUE_BYTES = 64 * 1024
MAX_ATOM_DEPTH = 32
# The item key that holds each stored field, in the lower case the reader uses.
FIELD_KEYS: dict[str, str] = {
    "artist": "\xa9art",
    "title": "\xa9nam",
    "mix_name": "----:com.apple.itunes:subtitle",
    "remixer": "----:com.apple.itunes:remixer",
    "release_title": "\xa9alb",
    "label": "----:com.apple.itunes:label",
    "catalog_number": "----:com.apple.itunes:catalognumber",
    "track_number": "trkn",
    "disc_number": "disk",
    "vinyl_position": "----:com.apple.itunes:vinyltrack",
    "genre": "\xa9gen",
    "style": "----:com.apple.itunes:style",
    "bpm": "tmpo",
    "key": "----:com.apple.itunes:initialkey",
    "year": "\xa9day",
    "acoustid_id": "----:com.apple.itunes:acoustid id",
    "mb_recording_id": "----:com.apple.itunes:musicbrainz release track id",
    "mb_release_id": "----:com.apple.itunes:musicbrainz album id",
    "discogs_release_id": "----:com.apple.itunes:discogs_release_id",
}
STORED_FIELDS: dict[str, str] = {
    "artist": "Artist",
    "title": "Title",
    "mix_name": "Mix Name",
    "remixer": "Remixer",
    "release_title": "Album",
    "label": "Label",
    "catalog_number": "CAT001",
    "track_number": "3/12",
    "disc_number": "1/2",
    "vinyl_position": "A1",
    "genre": "House",
    "style": "Deep",
    "bpm": "128",
    "key": "8A",
    "year": "2020",
    "acoustid_id": "acoustid-1",
    "mb_recording_id": "mb-track-1",
    "mb_release_id": "mb-album-1",
    "discogs_release_id": "discogs-1",
}


def build_m4a(
    *,
    entry_type: bytes = b"mp4a",
    free_bytes: int = 0,
    leading_atoms: bytes = b"",
    tags: bytes = b"",
    more_tags: bytes = b"",
    with_offsets: bool = True,
) -> bytes:
    """Return an M4A file with the given atoms, a sound track, and a media atom."""
    return m4a_with_sound_track(
        entry_type=entry_type,
        chunk_sizes=[CHUNK_SIZE] * CHUNK_COUNT,
        free_bytes=free_bytes,
        leading_atoms=leading_atoms,
        tags=tags,
        more_tags=more_tags,
        with_offsets=with_offsets,
        rate=RATE,
    )


def _tags_of(data: bytes) -> dict[str, tuple[str, ...]]:
    """Return the items of a file, keyed by their lower case key."""
    return {tag.key.lower(): tag.values for tag in read_mp4(data).tags}


def _chunk_offsets(data: bytes) -> list[int]:
    """Return the chunk offsets held in the chunk offset table."""
    position = data.find(b"stco")
    body = data[position + 4 :]
    count = struct.unpack(">I", body[4:8])[0]
    return [struct.unpack(">I", body[8 + index * 4 : 12 + index * 4])[0] for index in range(count)]


def _item_list_bytes(data: bytes) -> bytes:
    """Return the raw bytes of the item list atom."""
    position = data.find(b"ilst")
    size = struct.unpack(">I", data[position - 4 : position])[0]
    return data[position - 4 : position - 4 + size]


def build_nested_m4a(depth: int) -> bytes:
    """Return an M4A file whose movie nests user data atoms deeply."""
    nested = b""
    for _ in range(depth):
        nested = atom(b"udta", nested)
    entry = audio_entry(b"mp4a", 2, RATE, esds(BITRATE))
    sizes = [CHUNK_SIZE] * CHUNK_COUNT
    movie = mvhd(RATE, RATE * 2)
    placeholder = trak(b"soun", entry, sizes, [0] * CHUNK_COUNT)
    start = len(ftyp()) + len(atom(b"moov", movie + placeholder + nested)) + 8
    offsets: list[int] = []
    cursor = start
    for size in sizes:
        offsets.append(cursor)
        cursor += size
    track = trak(b"soun", entry, sizes, offsets)
    return ftyp() + atom(b"moov", movie + track + nested) + atom(b"mdat", MEDIA_BYTES)


@pytest.mark.parametrize(
    ("name", "builder"),
    [("aac", sa.build_m4a_aac_bytes), ("alac", sa.build_m4a_alac_bytes)],
)
def test_stored_fields_round_trip(name: str, builder: Callable[..., bytes]) -> None:
    """Every stored field reads back from the file after a write."""
    written = write_m4a_tags(builder(chunk_offsets=True), STORED_FIELDS)
    tags = _tags_of(written)
    for field, key in FIELD_KEYS.items():
        assert tags[key] == (STORED_FIELDS[field],), field


def test_media_payload_stays_unchanged() -> None:
    """The media data keeps its bytes when the tags change."""
    data = build_m4a()
    written = write_m4a_tags(data, {"artist": "Artist", "title": "Title"})
    start, end = find_m4a_payload(data)[0]
    new_start, new_end = find_m4a_payload(written)[0]
    assert written[new_start:new_end] == data[start:end]


def test_chunk_offsets_follow_the_media() -> None:
    """The chunk offsets move with the media data when the movie grows."""
    data = build_m4a()
    written = write_m4a_tags(data, {"artist": "Artist", "title": "Title"})
    shift = len(written) - len(data)
    assert shift > 0
    assert _chunk_offsets(written) == [offset + shift for offset in _chunk_offsets(data)]


def test_free_atom_absorbs_size_change() -> None:
    """A free atom takes the size change, so the media data stays where it is."""
    data = build_m4a(free_bytes=512)
    written = write_m4a_tags(data, {"artist": "Artist", "title": "Title"})
    assert find_m4a_payload(written) == find_m4a_payload(data)
    assert len(written) == len(data)
    assert _chunk_offsets(written) == _chunk_offsets(data)


def test_small_free_atom_moves_media() -> None:
    """A free atom with too little room absorbs part of the change, and the media moves."""
    data = build_m4a(free_bytes=8)
    written = write_m4a_tags(data, {"artist": "Artist", "title": "Title"})
    shift = len(written) - len(data)
    assert shift > 0
    assert _chunk_offsets(written) == [offset + shift for offset in _chunk_offsets(data)]
    start, end = find_m4a_payload(data)[0]
    new_start, new_end = find_m4a_payload(written)[0]
    assert new_start == start + shift
    assert written[new_start:new_end] == data[start:end]


def test_quicktime_meta_round_trips() -> None:
    """A QuickTime meta atom keeps its layout and its handler through a write."""
    quicktime = udta(item(b"\xa9nam", data_atom(1, b"QuickTime")), full_meta=False)
    data = build_m4a(tags=quicktime)
    written = write_m4a_tags(data, {"artist": "Artist"})
    tags = _tags_of(written)
    assert tags["\xa9nam"] == ("QuickTime",)
    assert tags["\xa9art"] == ("Artist",)
    assert written.count(b"mdir") == data.count(b"mdir")


def test_deep_nesting_is_refused() -> None:
    """A file that nests its atoms too deeply is refused instead of raising a recursion error."""
    with pytest.raises(Mp4WriteError, match="deeply"):
        write_m4a_tags(build_nested_m4a(MAX_ATOM_DEPTH + 8), {"artist": "Artist"})


def test_oversized_value_is_refused() -> None:
    """A value above the size limit is refused instead of reaching the atom construction."""
    oversized = "A" * (MAX_TAG_VALUE_BYTES + 1)
    with pytest.raises(Mp4WriteError, match="byte limit"):
        write_m4a_tags(build_m4a(), {"artist": oversized})


def test_unwritten_items_survive_the_write() -> None:
    """An item the app does not write keeps its bytes."""
    cover = item(b"covr", data_atom(13, b"\xff\xd8" + bytes(98)))
    data = build_m4a(tags=udta(cover))
    written = write_m4a_tags(data, {"artist": "Artist"})
    assert _item_list_bytes(written).count(cover) == 1


def test_second_write_replaces_the_value() -> None:
    """A second write replaces the value of a field it writes."""
    data = build_m4a()
    first = write_m4a_tags(data, {"artist": "First"})
    second = write_m4a_tags(first, {"artist": "Second"})
    assert _tags_of(second)["\xa9art"] == ("Second",)


def test_case_variant_item_is_replaced() -> None:
    """An item that differs only in case names the same field, so the writer replaces it."""
    lower = udta(item(b"\xa9art", data_atom(1, b"Lower")))
    written = write_m4a_tags(build_m4a(tags=lower), {"artist": "New"})
    artists = [tag for tag in read_mp4(written).tags if tag.key.lower() == "\xa9art"]
    assert [(tag.key, tag.values) for tag in artists] == [("\xa9ART", ("New",))]


def test_second_udta_survives_the_write() -> None:
    """A movie with two user data atoms keeps both of them and the written items."""
    first_udta = udta(item(b"\xa9cmt", data_atom(1, b"Note")))
    second_udta = udta(item(b"\xa9too", data_atom(1, b"Tool")))
    written = write_m4a_tags(
        build_m4a(tags=first_udta, more_tags=second_udta), {"artist": "Artist"}
    )
    tags = _tags_of(written)
    assert written.count(b"udta") == 2
    assert b"\xa9too" in written
    assert b"Tool" in written
    assert tags["\xa9cmt"] == ("Note",)
    assert tags["\xa9art"] == ("Artist",)


def test_legacy_genre_item_is_removed() -> None:
    """Writing the genre removes the legacy genre item."""
    legacy = item(b"gnre", data_atom(0, struct.pack(">H", 18)))
    data = build_m4a(tags=udta(legacy))
    written = write_m4a_tags(data, {"genre": "House"})
    tags = _tags_of(written)
    assert "gnre" not in tags
    assert tags["\xa9gen"] == ("House",)


def test_missing_item_list_is_created() -> None:
    """A file with no item list gains one that holds the written fields."""
    written = write_m4a_tags(build_m4a(), {"artist": "Artist"})
    assert _tags_of(written)["\xa9art"] == ("Artist",)


def test_freeform_namespace_is_itunes() -> None:
    """A freeform item names the iTunes namespace and the field name."""
    written = write_m4a_tags(build_m4a(), {"label": "Label"})
    assert "----:com.apple.iTunes:LABEL" in {tag.key for tag in read_mp4(written).tags}


def test_protected_file_is_refused() -> None:
    """A file whose audio carries a DRM scheme is refused."""
    with pytest.raises(Mp4WriteError, match="DRM"):
        write_m4a_tags(build_m4a(entry_type=b"drms"), {"artist": "Artist"})


def test_fragmented_file_is_refused() -> None:
    """A fragmented file is refused."""
    data = build_m4a(leading_atoms=atom(b"moof", bytes(16)))
    with pytest.raises(Mp4WriteError, match="fragmented"):
        write_m4a_tags(data, {"artist": "Artist"})


def test_file_without_offsets_is_refused() -> None:
    """A file with no chunk offset table is refused."""
    with pytest.raises(Mp4WriteError, match="chunk offset table"):
        write_m4a_tags(build_m4a(with_offsets=False), {"artist": "Artist"})


def test_decimal_tempo_rounds_half_up() -> None:
    """A decimal tempo is rounded half up into the tempo atom."""
    written = write_m4a_tags(build_m4a(), {"bpm": "128.50"})
    assert _tags_of(written)["tmpo"] == ("129",)


def test_text_track_number_is_refused() -> None:
    """A track number that is not a number is refused."""
    with pytest.raises(Mp4WriteError, match="not a number"):
        write_m4a_tags(build_m4a(), {"track_number": "A1"})


def test_empty_total_is_refused() -> None:
    """A track number that names no total is refused instead of losing the total."""
    with pytest.raises(Mp4WriteError, match="not a number"):
        write_m4a_tags(build_m4a(), {"track_number": "3/"})


def test_raised_digit_number_is_refused() -> None:
    """A track number that holds a raised digit is refused instead of raising."""
    with pytest.raises(Mp4WriteError, match="not a number"):
        write_m4a_tags(build_m4a(), {"track_number": "\u00b2"})


def test_tempo_above_atom_limit_refused() -> None:
    """A tempo above the limit of the tempo atom is refused."""
    with pytest.raises(Mp4WriteError, match="limit"):
        write_m4a_tags(build_m4a(), {"bpm": "40000"})
