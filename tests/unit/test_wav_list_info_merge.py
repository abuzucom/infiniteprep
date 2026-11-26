"""A WAV tag write keeps the LIST-INFO fields the write does not name."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    synchronize_wav_list_info_chunk,
)
from infiniteprep.domain.enums import FieldName
from tests.fixtures.audio_container_bytes import riff_chunk
from tests.fixtures.audio_factory import create_wav_fixture

RIFF_HEADER_SIZE: int = 12
CHUNK_HEADER_SIZE: int = 8
FOURCC_SIZE: int = 4
FORM_SIZE_SLICE: slice = slice(4, 8)


def info_subchunk(fourcc: bytes, text: str) -> bytes:
    """Build one null-terminated LIST-INFO subchunk."""
    return riff_chunk(fourcc, text.encode("utf-8") + b"\x00")


def append_list_info(path: Path, subchunks: bytes) -> None:
    """Append a LIST-INFO chunk to a WAV file and correct the RIFF size."""
    data = bytearray(path.read_bytes()) + riff_chunk(b"LIST", b"INFO" + subchunks)
    data[FORM_SIZE_SLICE] = struct.pack("<I", len(data) - CHUNK_HEADER_SIZE)
    path.write_bytes(bytes(data))


def walk_chunks(data: bytes, offset: int, end: int) -> list[tuple[bytes, bytes]]:
    """Return the (id, payload) pairs of the RIFF chunks between two offsets."""
    chunks: list[tuple[bytes, bytes]] = []
    while offset + CHUNK_HEADER_SIZE <= end:
        chunk_id = data[offset : offset + FOURCC_SIZE]
        size = struct.unpack("<I", data[offset + FOURCC_SIZE : offset + CHUNK_HEADER_SIZE])[0]
        body_start = offset + CHUNK_HEADER_SIZE
        chunks.append((chunk_id, data[body_start : body_start + size]))
        offset = body_start + size + (size % 2)
    return chunks


def read_list_info_payloads(path: Path) -> dict[bytes, bytes]:
    """Return the declared payload bytes of each LIST-INFO subchunk of a WAV file."""
    data = path.read_bytes()
    payloads: dict[bytes, bytes] = {}
    for chunk_id, payload in walk_chunks(data, RIFF_HEADER_SIZE, len(data)):
        if chunk_id != b"LIST" or payload[:FOURCC_SIZE] != b"INFO":
            continue
        for sub_id, sub_payload in walk_chunks(payload, FOURCC_SIZE, len(payload)):
            payloads[sub_id] = sub_payload
    return payloads


def read_list_info(path: Path) -> dict[bytes, str]:
    """Return the LIST-INFO fields of a WAV file as text keyed by subchunk id."""
    return {
        sub_id: payload.rstrip(b"\x00").decode("utf-8")
        for sub_id, payload in read_list_info_payloads(path).items()
    }


class WavListInfoMergeTests(unittest.TestCase):
    """The LIST-INFO rewrite replaces mapped fields and keeps every other field."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.wav = create_wav_fixture(Path(temp_dir.name) / "track.wav", duration_sec=0.5)
        append_list_info(
            self.wav,
            info_subchunk(b"INAM", "Old Title")
            + info_subchunk(b"IART", "Old Artist")
            + info_subchunk(b"ICMT", "Ripped from vinyl")
            + info_subchunk(b"ISFT", "Recorder 1.0"),
        )

    def test_unnamed_fields_survive_field_write(self) -> None:
        """A genre write keeps every other stored INFO field."""
        synchronize_wav_list_info_chunk(self.wav, {FieldName.GENRE.value: "House"})

        fields = read_list_info(self.wav)

        self.assertEqual(fields[b"IGNR"], "House")
        self.assertEqual(fields[b"INAM"], "Old Title")
        self.assertEqual(fields[b"IART"], "Old Artist")
        self.assertEqual(fields[b"ICMT"], "Ripped from vinyl")
        self.assertEqual(fields[b"ISFT"], "Recorder 1.0")

    def test_named_field_replaces_stored_value(self) -> None:
        """A title write replaces the stored title and keeps the comment."""
        synchronize_wav_list_info_chunk(self.wav, {FieldName.TITLE.value: "New Title"})

        fields = read_list_info(self.wav)

        self.assertEqual(fields[b"INAM"], "New Title")
        self.assertEqual(fields[b"ICMT"], "Ripped from vinyl")

    def test_write_leaves_one_info_chunk(self) -> None:
        """The write leaves exactly one LIST INFO chunk in the file."""
        synchronize_wav_list_info_chunk(self.wav, {FieldName.GENRE.value: "House"})

        data = self.wav.read_bytes()
        list_info = [
            payload
            for chunk_id, payload in walk_chunks(data, RIFF_HEADER_SIZE, len(data))
            if chunk_id == b"LIST" and payload[:FOURCC_SIZE] == b"INFO"
        ]

        self.assertEqual(len(list_info), 1)

    def test_second_info_chunk_fields_survive(self) -> None:
        """Fields from a second LIST INFO chunk carry into the merged chunk."""
        append_list_info(self.wav, info_subchunk(b"IENG", "Mastering Engineer"))

        synchronize_wav_list_info_chunk(self.wav, {FieldName.GENRE.value: "House"})

        fields = read_list_info(self.wav)
        self.assertEqual(fields[b"IENG"], "Mastering Engineer")
        self.assertEqual(fields[b"ICMT"], "Ripped from vinyl")
        self.assertEqual(fields[b"IGNR"], "House")

    def test_odd_payload_keeps_exact_bytes(self) -> None:
        """An odd-sized field payload reads back with its exact bytes after the write."""
        odd_payload = b"Recorder 1.0\x00"
        self.assertEqual(len(odd_payload) % 2, 1)

        synchronize_wav_list_info_chunk(self.wav, {FieldName.GENRE.value: "House"})

        self.assertEqual(read_list_info_payloads(self.wav)[b"ISFT"], odd_payload)


if __name__ == "__main__":
    unittest.main()
