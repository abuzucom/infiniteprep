"""Write iTunes-style tags to M4A bytes.

The layout follows the ISO base media file format and the iTunes metadata atoms. The writer
rebuilds only the moov atom, keeps every item it does not write, and moves the chunk offsets of
every track when the movie changes size. The media data in the mdat atoms is copied byte for
byte.

The atom walk, the offset relocation, and the item layout follow the MIT-licensed writer in
`Borewit/music-metadata`. The free atom that absorbs a size change follows `Zeugma440/atldotnet`.
The NOTICE file credits both.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from infiniteprep.domain.enums import FieldName
from infiniteprep.domain.tag_output import round_bpm_to_integer

ATOM_HEADER_SIZE: Final[int] = 8
LARGE_ATOM_HEADER_SIZE: Final[int] = 16
LARGE_SIZE_MARKER: Final[int] = 1
FULL_ATOM_PREFIX: Final[int] = 4
UINT16_SIZE: Final[int] = 2
UINT32_SIZE: Final[int] = 4
UINT64_SIZE: Final[int] = 8
UINT32_MAX: Final[int] = 0xFFFFFFFF
# A walk reads at most this many atoms, so a damaged file cannot exhaust memory.
MAX_ATOMS: Final[int] = 100_000
# The rebuilt movie atom stays under this size.
MAX_MOVIE_BYTES: Final[int] = 64 * 1024 * 1024
# The chunk offsets settle after a few passes, because a wider offset table changes the movie.
MAX_REBUILD_PASSES: Final[int] = 16
MAX_CHUNK_OFFSETS: Final[int] = 5_000_000
# A track or disc number fits in the 16-bit fields of a trkn or disk atom.
MAX_PAIR_VALUE: Final[int] = 0xFFFF
# A tempo atom holds a signed 16-bit integer, and the app reads a tempo back as signed.
MAX_TEMPO: Final[int] = 0x7FFF
# The stco and co64 tables hold a version, a count, then one offset per chunk.
OFFSET_TABLE_HEADER_SIZE: Final[int] = 8

MOOV: Final[str] = "moov"
MDAT: Final[str] = "mdat"
UDTA: Final[str] = "udta"
META: Final[str] = "meta"
ILST: Final[str] = "ilst"
STSD: Final[str] = "stsd"
STCO: Final[str] = "stco"
CO64: Final[str] = "co64"
FREE: Final[str] = "free"
HANDLER: Final[str] = "hdlr"
MEAN: Final[str] = "mean"
NAME: Final[str] = "name"
DATA: Final[str] = "data"
FREEFORM_KEY: Final[str] = "----"

# The atoms whose children the writer walks.
CONTAINER_TYPES: Final[frozenset[str]] = frozenset(
    {"moov", "trak", "mdia", "minf", "stbl", "edts", "dinf", "udta"}
)
# A fragmented or indexed file stores its media in segments that a movie rebuild cannot move.
FRAGMENTED_TYPES: Final[frozenset[str]] = frozenset({"moof", "sidx", "mfra"})
# A top-level metadata atom holds offsets that a movie rebuild cannot move.
TOP_LEVEL_METADATA_TYPES: Final[frozenset[str]] = frozenset(
    {"meta", "meco", "iloc", "saio", "cmov"}
)
# An offset structure inside the movie points at data outside the file.
UNSUPPORTED_OFFSET_TYPES: Final[frozenset[str]] = frozenset(
    {"mvex", "iloc", "saio", "cmov", "meco"}
)
# A sample entry of one of these types carries encrypted media.
ENCRYPTED_SAMPLE_ENTRIES: Final[frozenset[str]] = frozenset({"drms", "drmi", "enca"})
# A sample entry of one of these types holds child atoms after its fixed fields.
AUDIO_SAMPLE_ENTRIES: Final[frozenset[str]] = frozenset({"mp4a", "alac", "enca", "drms", "drmi"})
# An audio sample entry holds 28 bytes of fields before its child atoms.
SAMPLE_ENTRY_FIELDS: Final[int] = 28
# A protection scheme information box marks common encryption.
PROTECTION_BOX: Final[str] = "sinf"
# The stsd atom holds a version, flags, and an entry count before its sample entries.
STSD_ENTRIES_OFFSET: Final[int] = FULL_ATOM_PREFIX + UINT32_SIZE

# The data atom type indicators. A type of 1 holds UTF-8 text, 0 holds binary data, and 21
# holds a signed integer.
DATA_TYPE_BINARY: Final[int] = 0
DATA_TYPE_TEXT: Final[int] = 1
DATA_TYPE_SIGNED_INT: Final[int] = 21

# The namespace of a freeform item. The iTunes namespace is the one other taggers read.
FREEFORM_NAMESPACE: Final[str] = "com.apple.iTunes"
# The native iTunes atoms that hold one text value.
MP4_TEXT_ATOMS: Final[dict[str, str]] = {
    FieldName.ARTIST.value: "\xa9ART",
    FieldName.TITLE.value: "\xa9nam",
    FieldName.RELEASE_TITLE.value: "\xa9alb",
    FieldName.GENRE.value: "\xa9gen",
    FieldName.YEAR.value: "\xa9day",
}
# The freeform item names that hold the fields with no native atom.
MP4_FREEFORM_ATOMS: Final[dict[str, str]] = {
    FieldName.MIX_NAME.value: "SUBTITLE",
    FieldName.REMIXER.value: "REMIXER",
    FieldName.LABEL.value: "LABEL",
    FieldName.CATALOG_NUMBER.value: "CATALOGNUMBER",
    FieldName.VINYL_POSITION.value: "VINYLTRACK",
    FieldName.STYLE.value: "STYLE",
    FieldName.DISCOGS_RELEASE_ID.value: "DISCOGS_RELEASE_ID",
    FieldName.ACOUSTID_ID.value: "Acoustid Id",
    FieldName.MB_RECORDING_ID.value: "MusicBrainz Release Track Id",
    FieldName.MB_RELEASE_ID.value: "MusicBrainz Album Id",
    FieldName.KEY.value: "initialkey",
}
# The native atoms that hold a number and a total, a tempo, and the legacy genre atom.
MP4_TRACK_ATOM: Final[str] = "trkn"
MP4_DISC_ATOM: Final[str] = "disk"
MP4_TEMPO_ATOM: Final[str] = "tmpo"
MP4_LEGACY_GENRE_ATOM: Final[str] = "gnre"
# The fields the writer stores, in the order they reach the file.
MP4_WRITTEN_FIELDS: Final[tuple[str, ...]] = (
    FieldName.ARTIST.value,
    FieldName.TITLE.value,
    FieldName.MIX_NAME.value,
    FieldName.REMIXER.value,
    FieldName.RELEASE_TITLE.value,
    FieldName.LABEL.value,
    FieldName.CATALOG_NUMBER.value,
    FieldName.TRACK_NUMBER.value,
    FieldName.DISC_NUMBER.value,
    FieldName.VINYL_POSITION.value,
    FieldName.GENRE.value,
    FieldName.STYLE.value,
    FieldName.BPM.value,
    FieldName.KEY.value,
    FieldName.YEAR.value,
    FieldName.ACOUSTID_ID.value,
    FieldName.MB_RECORDING_ID.value,
    FieldName.MB_RELEASE_ID.value,
    FieldName.DISCOGS_RELEASE_ID.value,
)


class Mp4WriteError(ValueError):
    """Raised when an M4A file cannot carry a safe tag write."""


@dataclass(frozen=True)
class _Atom:
    """One atom: its four-character type and the byte range of its body."""

    atom_type: str
    start: int
    body_start: int
    end: int

    @property
    def size(self) -> int:
        """Return the byte length of the atom with its header."""
        return self.end - self.start


@dataclass
class _Budget:
    """A counter that bounds how many atoms one walk may read."""

    remaining: int = MAX_ATOMS

    def spend(self) -> None:
        """Count one atom and refuse a file that holds too many."""
        self.remaining -= 1
        if self.remaining < 0:
            raise Mp4WriteError("the file holds too many atoms to rewrite safely")


@dataclass(frozen=True)
class _File:
    """The top-level atoms of an M4A file and the atoms the writer moves."""

    data: bytes
    atoms: tuple[_Atom, ...]
    moov_index: int
    media_indexes: tuple[int, ...]
    free_index: int | None

    @property
    def moov(self) -> _Atom:
        """Return the movie atom."""
        return self.atoms[self.moov_index]

    @property
    def media(self) -> tuple[_Atom, ...]:
        """Return every atom that holds media data."""
        return tuple(self.atoms[index] for index in self.media_indexes)


@dataclass(frozen=True)
class _Layout:
    """Where each top-level atom lands after the movie atom changes size."""

    sizes: tuple[int, ...]
    media_ranges: tuple[tuple[int, int], ...]
    media_shifts: tuple[int, ...]

    def relocate(self, offset: int) -> int | None:
        """Return the new offset of a media byte, or None when it points outside the media."""
        for (start, end), shift in zip(self.media_ranges, self.media_shifts, strict=True):
            if start <= offset < end:
                return offset + shift
        return None


def write_m4a_tags(data: bytes, tags_to_write: Mapping[str, str]) -> bytes:
    """Return M4A bytes whose item list carries tags_to_write, with the media kept intact."""
    file = _parse_file(data)
    written, dropped = _written_items(tags_to_write)
    updated = _rebuild_movie(file, written, dropped)
    layout = _new_layout(file, len(updated))
    return _assemble(file, updated, layout)


def check_m4a_writable(data: bytes) -> None:
    """Raise Mp4WriteError when an M4A file cannot carry a tag write."""
    write_m4a_tags(data, {})


def _parse_file(data: bytes) -> _File:
    """Read the top-level atoms of an M4A file and refuse the shapes the writer cannot move."""
    atoms = tuple(_children(data, 0, len(data), _Budget()))
    _refuse_unsupported_atoms(atoms)
    moov_indexes = [index for index, atom in enumerate(atoms) if atom.atom_type == MOOV]
    if len(moov_indexes) != 1:
        raise Mp4WriteError("the file must hold exactly one moov atom")
    media_indexes = tuple(index for index, atom in enumerate(atoms) if atom.atom_type == MDAT)
    if not media_indexes:
        raise Mp4WriteError("the file holds no mdat atom, so it carries no media data")
    moov = atoms[moov_indexes[0]]
    media = tuple(atoms[index] for index in media_indexes)
    file = _File(data, atoms, moov_indexes[0], media_indexes, _pick_free(atoms, moov, media))
    _refuse_protected_media(file)
    return file


def _pick_free(atoms: tuple[_Atom, ...], moov: _Atom, media: tuple[_Atom, ...]) -> int | None:
    """Return the index of the free atom that can absorb a size change, or None."""
    free_indexes = [index for index, atom in enumerate(atoms) if atom.atom_type == FREE]
    if not free_indexes:
        return None
    for index in free_indexes:
        atom = atoms[index]
        if moov.start < atom.start < media[0].start:
            return index
    return free_indexes[0]


def _refuse_unsupported_atoms(atoms: Sequence[_Atom]) -> None:
    """Refuse a fragmented file and a file with top-level metadata."""
    for atom in atoms:
        if atom.atom_type in FRAGMENTED_TYPES:
            raise Mp4WriteError("the file is fragmented, so its media offsets cannot be moved")
        if atom.atom_type in TOP_LEVEL_METADATA_TYPES:
            raise Mp4WriteError(
                f"the file holds a top-level {atom.atom_type!r} atom, whose offsets this "
                "writer cannot move"
            )


def _refuse_protected_media(file: _File) -> None:
    """Refuse a file whose audio carries a DRM protection scheme."""
    for entry in _sample_entries(file):
        if entry.atom_type in ENCRYPTED_SAMPLE_ENTRIES or _has_protection_box(file, entry):
            raise Mp4WriteError("the file is DRM protected")


def _has_protection_box(file: _File, entry: _Atom) -> bool:
    """Return whether a sample entry holds a protection scheme information box."""
    if entry.atom_type not in AUDIO_SAMPLE_ENTRIES:
        return False
    start = entry.body_start + SAMPLE_ENTRY_FIELDS
    if entry.end - start < ATOM_HEADER_SIZE:
        return False
    children = _children(file.data, start, entry.end, _Budget())
    return any(child.atom_type == PROTECTION_BOX for child in children)


def _sample_entries(file: _File) -> list[_Atom]:
    """Return every sample entry in the movie."""
    entries: list[_Atom] = []
    for trak in _children(file.data, file.moov.body_start, file.moov.end, _Budget()):
        if trak.atom_type != "trak":
            continue
        stbl = _sample_table(file, trak)
        if stbl is None:
            continue
        stsd = _find_child(file, stbl, STSD)
        if stsd is None:
            continue
        entries.extend(
            _children(file.data, stsd.body_start + STSD_ENTRIES_OFFSET, stsd.end, _Budget())
        )
    return entries


def _sample_table(file: _File, trak: _Atom) -> _Atom | None:
    """Return the sample table of a track, or None when the track holds none."""
    mdia = _find_child(file, trak, "mdia")
    if mdia is None:
        return None
    minf = _find_child(file, mdia, "minf")
    if minf is None:
        return None
    return _find_child(file, minf, "stbl")


def _find_child(file: _File, atom: _Atom, atom_type: str) -> _Atom | None:
    """Return the first child of atom with the given type, or None."""
    children = _children(file.data, atom.body_start, atom.end, _Budget())
    return next((child for child in children if child.atom_type == atom_type), None)


def _rebuild_movie(file: _File, written: Sequence[tuple[str, bytes]], dropped: set[str]) -> bytes:
    """Return the rebuilt movie atom with the chunk offsets moved to their new places."""
    if not _has_offset_table(file):
        raise Mp4WriteError(
            "the file holds no chunk offset table, so its media offsets cannot be moved"
        )
    updated = _rebuild_moov(file, written, dropped, _new_layout(file, file.moov.size))
    for _ in range(MAX_REBUILD_PASSES):
        layout = _new_layout(file, len(updated))
        candidate = _rebuild_moov(file, written, dropped, layout)
        if len(candidate) == len(updated):
            return candidate
        updated = candidate
    raise Mp4WriteError("the chunk offsets did not settle")


def _has_offset_table(file: _File) -> bool:
    """Return whether the movie holds a chunk offset table."""
    pending = [file.moov]
    while pending:
        atom = pending.pop()
        if atom.atom_type in (STCO, CO64):
            return True
        if atom.atom_type in CONTAINER_TYPES:
            pending.extend(_children(file.data, atom.body_start, atom.end, _Budget()))
    return False


def _rebuild_moov(
    file: _File, written: Sequence[tuple[str, bytes]], dropped: set[str], layout: _Layout
) -> bytes:
    """Return the rebuilt movie atom with the written items in place."""
    parts: list[bytes] = []
    udta_parts: list[bytes] | None = None
    meta_found = False
    for child in _children(file.data, file.moov.body_start, file.moov.end, _Budget()):
        if child.atom_type == UDTA:
            udta_parts, child_meta = _rebuild_udta(file, child, written, dropped, layout)
            meta_found = meta_found or child_meta
        elif child.atom_type == META:
            meta_found = True
            parts.append(_rebuild_meta(file, child, written, dropped, layout))
        else:
            parts.append(_rebuild_atom(file, child, layout))
    if udta_parts is None:
        parts.append(_render_atom(UDTA, _render_meta(written, dropped)))
    else:
        if not meta_found:
            udta_parts.append(_render_meta(written, dropped))
        parts.append(_render_atom(UDTA, b"".join(udta_parts)))
    return _render_atom(MOOV, b"".join(parts))


def _rebuild_udta(
    file: _File,
    udta: _Atom,
    written: Sequence[tuple[str, bytes]],
    dropped: set[str],
    layout: _Layout,
) -> tuple[list[bytes], bool]:
    """Return the rebuilt children of a user data atom and whether it holds a meta atom."""
    parts: list[bytes] = []
    meta_found = False
    for child in _children(file.data, udta.body_start, udta.end, _Budget()):
        if child.atom_type == META:
            meta_found = True
            parts.append(_rebuild_meta(file, child, written, dropped, layout))
        else:
            parts.append(_rebuild_atom(file, child, layout))
    return parts, meta_found


def _rebuild_meta(
    file: _File,
    meta: _Atom,
    written: Sequence[tuple[str, bytes]],
    dropped: set[str],
    layout: _Layout,
) -> bytes:
    """Return the rebuilt meta atom with the written items in its item list."""
    start = _meta_children_start(file, meta)
    parts: list[bytes] = []
    ilst_found = False
    for child in _children(file.data, start, meta.end, _Budget()):
        if child.atom_type == ILST:
            ilst_found = True
            item_list = file.data[child.body_start : child.end]
            parts.append(_render_atom(ILST, _render_items(item_list, written, dropped)))
        else:
            parts.append(_rebuild_atom(file, child, layout))
    if not ilst_found:
        parts.append(_render_atom(ILST, _render_items(b"", written, dropped)))
    body = b"".join(parts)
    if start == meta.body_start:
        return _render_atom(META, body)
    return _render_atom(META, bytes(FULL_ATOM_PREFIX) + body)


def _meta_children_start(file: _File, meta: _Atom) -> int:
    """Return where the children of a meta atom start.

    An iTunes meta atom holds a version and flags before its children. A QuickTime meta atom
    starts its children at once.
    """
    marker = file.data[meta.body_start + UINT32_SIZE : meta.body_start + ATOM_HEADER_SIZE]
    if marker == HANDLER.encode("latin-1"):
        return meta.body_start
    return meta.body_start + FULL_ATOM_PREFIX


def _render_meta(written: Sequence[tuple[str, bytes]], dropped: set[str]) -> bytes:
    """Return a new meta atom that holds the written items."""
    handler = _render_atom(
        HANDLER,
        bytes(FULL_ATOM_PREFIX) + bytes(4) + b"mdir" + bytes(12) + b"\x00",
    )
    items = _render_atom(ILST, _render_items(b"", written, dropped))
    body = bytes(FULL_ATOM_PREFIX) + handler + items
    return _render_atom(META, body)


def _rebuild_atom(file: _File, atom: _Atom, layout: _Layout) -> bytes:
    """Return one atom rebuilt, relocated, or copied as it was."""
    if atom.atom_type in CONTAINER_TYPES:
        children = _children(file.data, atom.body_start, atom.end, _Budget())
        parts = [_rebuild_atom(file, child, layout) for child in children]
        return _render_atom(atom.atom_type, b"".join(parts))
    if atom.atom_type in (STCO, CO64):
        return _rebuild_offset_table(file, atom, layout)
    if atom.atom_type in UNSUPPORTED_OFFSET_TYPES:
        raise Mp4WriteError(
            f"the file holds a {atom.atom_type!r} atom, whose offsets this writer cannot move"
        )
    return file.data[atom.start : atom.end]


def _rebuild_offset_table(file: _File, atom: _Atom, layout: _Layout) -> bytes:
    """Return the chunk offset table with every media offset moved to its new place."""
    body = file.data[atom.body_start : atom.end]
    if len(body) < OFFSET_TABLE_HEADER_SIZE:
        raise Mp4WriteError(f"the {atom.atom_type!r} chunk offset table is truncated")
    version, count = struct.unpack(">II", body[:OFFSET_TABLE_HEADER_SIZE])
    width = UINT32_SIZE if atom.atom_type == STCO else UINT64_SIZE
    if version != 0:
        raise Mp4WriteError(f"the {atom.atom_type!r} chunk offset table has an unknown version")
    if count > MAX_CHUNK_OFFSETS or len(body) != OFFSET_TABLE_HEADER_SIZE + count * width:
        raise Mp4WriteError(
            f"the {atom.atom_type!r} chunk offset table does not match its count of {count}"
        )
    offsets = _read_offsets(body, count, width)
    moved: list[int] = []
    for offset in offsets:
        relocated = layout.relocate(offset)
        if relocated is None:
            raise Mp4WriteError(
                "a chunk offset points outside the media data, so the file cannot be written safely"
            )
        moved.append(relocated)
    return _render_offset_table(atom.atom_type, moved)


def _read_offsets(body: bytes, count: int, width: int) -> list[int]:
    """Return the chunk offsets held in a chunk offset table body."""
    offsets: list[int] = []
    for index in range(count):
        position = OFFSET_TABLE_HEADER_SIZE + index * width
        offsets.append(int.from_bytes(body[position : position + width], "big"))
    return offsets


def _render_offset_table(atom_type: str, offsets: Sequence[int]) -> bytes:
    """Return the chunk offset table, widened to 64 bits when an offset needs it."""
    wide = atom_type == CO64 or any(offset > UINT32_MAX for offset in offsets)
    width = UINT64_SIZE if wide else UINT32_SIZE
    parts = [struct.pack(">II", 0, len(offsets))]
    for offset in offsets:
        parts.append(offset.to_bytes(width, "big"))
    return _render_atom(CO64 if wide else STCO, b"".join(parts))


def _new_layout(file: _File, new_moov_size: int) -> _Layout:
    """Return where every top-level atom lands after the movie atom changes size."""
    sizes = _new_sizes(file, new_moov_size)
    starts: list[int] = []
    cursor = 0
    for size in sizes:
        starts.append(cursor)
        cursor += size
    media_ranges = tuple((atom.body_start, atom.end) for atom in file.media)
    media_shifts = tuple(
        new_start - atom.start
        for new_start, atom in zip(starts, file.atoms, strict=True)
        if atom.atom_type == MDAT
    )
    return _Layout(sizes, media_ranges, media_shifts)


def _new_sizes(file: _File, new_moov_size: int) -> tuple[int, ...]:
    """Return the size every top-level atom takes after the write."""
    delta = new_moov_size - file.moov.size
    free_change = _free_change(file, delta)
    sizes: list[int] = []
    for index, atom in enumerate(file.atoms):
        if index == file.moov_index:
            sizes.append(new_moov_size)
        elif index == file.free_index:
            sizes.append(atom.size + free_change)
        else:
            sizes.append(atom.size)
    return tuple(sizes)


def _free_change(file: _File, delta: int) -> int:
    """Return the size change of the free atom that absorbs the movie size change.

    A free atom that shrinks by the amount the movie grew keeps the media data where it is. A
    free atom that grows by the amount the movie shrank does the same.
    """
    if file.free_index is None:
        return 0
    free = file.atoms[file.free_index]
    capacity = free.end - free.body_start
    if delta < 0:
        return -delta
    return -min(delta, capacity)


def _assemble(file: _File, updated_moov: bytes, layout: _Layout) -> bytes:
    """Return the file bytes with the rebuilt movie atom and the resized free atom."""
    parts: list[bytes] = []
    cursor = 0
    for index, atom in enumerate(file.atoms):
        parts.append(file.data[cursor : atom.start])
        if index == file.moov_index:
            parts.append(updated_moov)
        elif index == file.free_index:
            parts.append(_resized_free(file, layout.sizes[index] - atom.size))
        else:
            parts.append(file.data[atom.start : atom.end])
        cursor = atom.end
    parts.append(file.data[cursor:])
    return b"".join(parts)


def _resized_free(file: _File, change: int) -> bytes:
    """Return the free atom with its body resized by change bytes."""
    free = file.atoms[file.free_index]
    body = file.data[free.body_start : free.end]
    resized = body + bytes(change) if change > 0 else body[:change]
    return _render_atom(FREE, resized, header_size=free.body_start - free.start)


def _written_items(tags_to_write: Mapping[str, str]) -> tuple[list[tuple[str, bytes]], set[str]]:
    """Return the items to store and the legacy keys that a written field replaces."""
    written: list[tuple[str, bytes]] = []
    dropped: set[str] = set()
    for field in MP4_WRITTEN_FIELDS:
        value = str(tags_to_write.get(field, "")).strip()
        if not value:
            continue
        item = _render_field(field, value)
        if item is None:
            continue
        key, body = item
        written.append((_canonical_key(key), body))
        if key == MP4_TEXT_ATOMS[FieldName.GENRE.value]:
            dropped.add(MP4_LEGACY_GENRE_ATOM)
    return written, dropped


def _render_field(field: str, value: str) -> tuple[str, bytes] | None:
    """Return the key and the bytes of the item that holds one field value."""
    text_atom = MP4_TEXT_ATOMS.get(field)
    if text_atom is not None:
        return text_atom, _render_text_item(text_atom, value)
    freeform_name = MP4_FREEFORM_ATOMS.get(field)
    if freeform_name is not None:
        key = f"{FREEFORM_KEY}:{FREEFORM_NAMESPACE}:{freeform_name}"
        return key, _render_freeform_item(freeform_name, value)
    if field == FieldName.BPM.value:
        return MP4_TEMPO_ATOM, _render_tempo_item(value)
    if field == FieldName.TRACK_NUMBER.value:
        return MP4_TRACK_ATOM, _render_pair_item(MP4_TRACK_ATOM, value)
    if field == FieldName.DISC_NUMBER.value:
        return MP4_DISC_ATOM, _render_pair_item(MP4_DISC_ATOM, value)
    return None


def _render_text_item(key: str, value: str) -> bytes:
    """Return one item that holds a UTF-8 text value."""
    return _render_atom(key, _render_data(DATA_TYPE_TEXT, value.encode("utf-8")))


def _render_freeform_item(name: str, value: str) -> bytes:
    """Return one freeform item that holds its mean, its name, and a text value."""
    mean = _render_atom(MEAN, bytes(FULL_ATOM_PREFIX) + FREEFORM_NAMESPACE.encode("utf-8"))
    name_atom = _render_atom(NAME, bytes(FULL_ATOM_PREFIX) + name.encode("utf-8"))
    return _render_atom(
        FREEFORM_KEY, mean, name_atom, _render_data(DATA_TYPE_TEXT, value.encode("utf-8"))
    )


def _render_tempo_item(value: str) -> bytes:
    """Return the item that holds the rounded integer tempo."""
    try:
        tempo = round_bpm_to_integer(value)
    except ValueError as exc:
        raise Mp4WriteError(f"the tempo {value!r} is not a number") from exc
    if tempo > MAX_TEMPO:
        raise Mp4WriteError(
            f"the tempo {value} is above the {MAX_TEMPO} limit that an M4A tempo atom holds"
        )
    payload = struct.pack(">H", tempo)
    return _render_atom(MP4_TEMPO_ATOM, _render_data(DATA_TYPE_SIGNED_INT, payload))


def _render_pair_item(key: str, value: str) -> bytes:
    """Return the item that holds a track or disc number and its total."""
    number, total = _parse_number_pair(value)
    if key == MP4_TRACK_ATOM:
        payload = struct.pack(">HHHH", 0, number, total, 0)
    else:
        payload = struct.pack(">HHH", 0, number, total)
    return _render_atom(key, _render_data(DATA_TYPE_BINARY, payload))


def _parse_number_pair(value: str) -> tuple[int, int]:
    """Return the number and the total held in a value such as 3 or 3/12."""
    number_text, separator, total_text = value.strip().partition("/")
    number = _parse_pair_number(number_text)
    if number is None:
        raise Mp4WriteError(
            f"the track or disc number {value!r} is not a number, so write a number, or a "
            "number and a total separated by a slash"
        )
    total = 0
    if separator and total_text.strip():
        parsed_total = _parse_pair_number(total_text.strip())
        if parsed_total is None:
            raise Mp4WriteError(f"the total in the track or disc number {value!r} is not a number")
        total = parsed_total
    return number, total


def _parse_pair_number(text: str) -> int | None:
    """Return the number in text, or None when it is not a number the atom can hold."""
    cleaned = text.strip()
    if not cleaned.isascii() or not cleaned.isdigit():
        return None
    number = int(cleaned)
    return number if number <= MAX_PAIR_VALUE else None


def _render_items(
    existing: bytes, written: Sequence[tuple[str, bytes]], dropped: set[str]
) -> bytes:
    """Return the item list with the written items placed and every other item kept."""
    replacements = dict(written)
    parts: list[bytes] = []
    placed: set[str] = set()
    offset = 0
    while offset < len(existing):
        item = _read_atom(existing, offset, len(existing))
        key = _canonical_key(_item_key(existing, item))
        if key in dropped:
            offset = item.end
            continue
        if key in replacements:
            parts.append(replacements[key])
            placed.add(key)
        else:
            parts.append(existing[item.start : item.end])
        offset = item.end
    for key, body in written:
        if key not in placed:
            parts.append(body)
    return b"".join(parts)


def _item_key(data: bytes, item: _Atom) -> str:
    """Return the key of one item, such as a title atom or a freeform mean and name."""
    if item.atom_type != FREEFORM_KEY:
        return item.atom_type
    mean = _freeform_text(data, item, MEAN)
    name = _freeform_text(data, item, NAME)
    return f"{FREEFORM_KEY}:{mean}:{name}"


def _freeform_text(data: bytes, item: _Atom, atom_type: str) -> str:
    """Return the text of the mean or the name child of a freeform item."""
    for child in _children(data, item.body_start, item.end, _Budget()):
        if child.atom_type == atom_type:
            body = data[child.body_start + FULL_ATOM_PREFIX : child.end]
            return body.decode("utf-8", errors="replace")
    return ""


def _canonical_key(key: str) -> str:
    """Return the key used to match an existing item against a written field."""
    if key.startswith(f"{FREEFORM_KEY}:"):
        return key.lower()
    return key


def _render_data(type_code: int, payload: bytes) -> bytes:
    """Return one data atom with its type indicator and an empty locale."""
    return _render_atom(DATA, struct.pack(">II", type_code, 0) + payload)


def _render_atom(atom_type: str, *parts: bytes, header_size: int = ATOM_HEADER_SIZE) -> bytes:
    """Return one atom with a size, a four-character type, and the given body parts."""
    body = b"".join(parts)
    name = atom_type.encode("latin-1")
    if header_size == LARGE_ATOM_HEADER_SIZE:
        size = LARGE_ATOM_HEADER_SIZE + len(body)
        return struct.pack(">I", LARGE_SIZE_MARKER) + name + struct.pack(">Q", size) + body
    return struct.pack(">I", ATOM_HEADER_SIZE + len(body)) + name + body


def _read_atom(data: bytes, offset: int, end: int) -> _Atom:
    """Read the atom header at offset, handling a 64-bit size and a size of zero."""
    if offset + ATOM_HEADER_SIZE > end:
        raise Mp4WriteError("the file ends inside an atom header")
    size = struct.unpack(">I", data[offset : offset + UINT32_SIZE])[0]
    atom_type = data[offset + UINT32_SIZE : offset + ATOM_HEADER_SIZE].decode("latin-1")
    header = ATOM_HEADER_SIZE
    if size == LARGE_SIZE_MARKER:
        if offset + LARGE_ATOM_HEADER_SIZE > end:
            raise Mp4WriteError(f"the {atom_type!r} atom header is truncated")
        field = data[offset + ATOM_HEADER_SIZE : offset + LARGE_ATOM_HEADER_SIZE]
        size = struct.unpack(">Q", field)[0]
        header = LARGE_ATOM_HEADER_SIZE
    elif size == 0:
        size = end - offset
    if size < header or offset + size > end:
        raise Mp4WriteError(f"the {atom_type!r} atom size is invalid, so the file is damaged")
    return _Atom(atom_type, offset, offset + header, offset + size)


def _children(data: bytes, start: int, end: int, budget: _Budget) -> list[_Atom]:
    """Return the atoms laid out between start and end."""
    atoms: list[_Atom] = []
    offset = start
    while offset < end:
        budget.spend()
        child = _read_atom(data, offset, end)
        atoms.append(child)
        offset = child.end
    return atoms
