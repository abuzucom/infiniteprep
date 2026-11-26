"""Exercise key parsing and written tag formats with generated audio."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, KeyMode, parse_key_string
from infiniteprep.services.import_service import ImportService
from tests.fixtures.audio_factory import create_flac_fixture, create_wav_fixture
from tests.fixtures.mp4_bytes import audio_entry, data_atom, freeform, item, m4a_file, trak, udta
from tests.fixtures.ogg_bytes import make_synthetic_vorbis_ogg

MINOR_KEYS = ("Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#")
MAJOR_KEYS = ("B", "F#", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E")


@pytest.mark.parametrize("wheel_position", range(1, 13))
@pytest.mark.parametrize("mode", list(KeyMode))
def test_read_supported_notations_every_key(wheel_position: int, mode: KeyMode) -> None:
    """Each wheel position and musical spelling resolves to the same canonical key."""
    is_minor = mode == KeyMode.MINOR
    tonic = (MINOR_KEYS if is_minor else MAJOR_KEYS)[wheel_position - 1]
    camelot_suffix = "A" if is_minor else "B"
    open_suffix = "m" if is_minor else "d"
    open_position = (wheel_position + 4) % 12 + 1
    standard = tonic + ("m" if is_minor else "")
    expected = parse_key_string(standard)
    assert expected is not None and expected[1] == mode
    for spelling in (
        f"{wheel_position}{camelot_suffix}",
        f"{wheel_position:02}{camelot_suffix}",
        f"{open_position}{open_suffix}",
        f"{open_position:02}{open_suffix}",
        f"{tonic} {'min' if is_minor else 'maj'}",
        f"{tonic} {mode.value}",
        f"{tonic}{mode.value}",
    ):
        assert parse_key_string(spelling) == expected


@pytest.mark.parametrize("raw", ["00A", "008A", "13m", "A miniature", "B majestic", "F# min junk"])
def test_malformed_keys_lack_canonical_value(raw: str) -> None:
    """A valid prefix does not make the rest of a malformed key meaningful."""
    assert parse_key_string(raw) is None


def test_unreadable_key_imports_with_warning(tmp_path: Path) -> None:
    """Keep the original tag in the library and surface the parse failure."""
    repo = Repository(DatabaseManager(":memory:"))
    path = create_flac_fixture(tmp_path / "track.flac", tags={"initialkey": "A miniature"})

    [asset] = ImportService(repo).import_paths([path]).imported_files

    assert asset.original_tags["standard_tags"][FieldName.KEY] == "A miniature"
    assert any(
        "Key tag could not be parsed" in warning
        for warning in asset.original_tags["technical"]["warnings"]
    )


def test_read_m4a_key_integer_tempo(tmp_path: Path) -> None:
    """Map M4A key and tempo into standard fields while retaining the atom names."""
    path = tmp_path / "track.m4a"
    key_atom = "----:com.apple.iTunes:initialkey"
    sound = trak(b"soun", audio_entry(b"mp4a", 2, 44_100))
    tags = udta(
        freeform("com.apple.iTunes", "initialkey", "08A"),
        item(b"tmpo", data_atom(21, b"\x00\x80")),
    )
    path.write_bytes(m4a_file(sound, tags))

    metadata = MetadataTagAdapter().read_metadata(path)

    assert metadata.standard_tags == {FieldName.KEY: "08A", FieldName.BPM: "128"}
    assert key_atom in metadata.raw_custom_tags
    assert "tmpo" in metadata.raw_custom_tags


def test_read_ogg_key_and_tempo(tmp_path: Path) -> None:
    """Use Vorbis field names for key and tempo in an Ogg file."""
    path = tmp_path / "track.ogg"
    path.write_bytes(make_synthetic_vorbis_ogg(fields=[("INITIALKEY", "F# min"), ("BPM", "128.5")]))

    metadata = MetadataTagAdapter().read_metadata(path)

    assert metadata.standard_tags == {FieldName.KEY: "F# min", FieldName.BPM: "128.5"}


def test_transcode_formats_output_key_bpm(tmp_path: Path) -> None:
    """Apply the tag preferences when copying metadata to a transcode."""
    repo = Repository(DatabaseManager(":memory:"))
    source = create_wav_fixture(tmp_path / "track.wav", tags={"key": "Bbm", "bpm": "128.5"})
    before = source.read_bytes()
    [asset] = ImportService(repo).import_paths([source]).imported_files
    transcoder = SafeTranscoderAdapter(repo, backup_dir=tmp_path / "backups")

    result = transcoder.transcode_file(
        asset.id,
        mode=TranscodeMode.SIBLING_EXPORT,
        preset=TranscodePreset.FORCE_WAV_44K_16B,
        export_dir=tmp_path / "export",
    )

    written = MetadataTagAdapter().read_metadata(result.output_path).standard_tags
    assert written[FieldName.KEY] == "3A"
    assert written[FieldName.BPM] == "129"
    assert source.read_bytes() == before
