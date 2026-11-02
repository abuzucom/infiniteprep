"""Behavioral tests for MetadataTagAdapter tag writes on the in-house codecs, using real files."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from infiniteprep import __version__
from infiniteprep.adapters.metadata_io import metadata_tag_adapter as adapter_module
from infiniteprep.adapters.metadata_io.flac_vorbis import get_field_values, read_flac_comment
from infiniteprep.adapters.metadata_io.id3_containers import mp3_audio_offset
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.domain.enums import FieldName
from tests.fixtures.audio_factory import (
    TRAKTOR_FLAC_PAYLOAD,
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_wav_fixture,
)
from tests.fixtures.flac_bytes import flac_file
from tests.fixtures.id3v2_bytes import tag_bytes, utf16, v23_frame

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
WRITTEN = {
    FieldName.ARTIST: "Moodymann",
    FieldName.TITLE: "Shades of Jae",
    FieldName.LABEL: "KDJ",
    FieldName.BPM: "118",
    FieldName.CATALOG_NUMBER: "KDJ-24",
}
FULL_DATE = "2020-05-01T12:30"
ID3V23_MAJOR = 3
# A RIFF chunk header is a 4-byte ID, then a 4-byte size.
RIFF_FIELD_WIDTH = 4
RIFF_HEADER_WIDTH = RIFF_FIELD_WIDTH * 2


@pytest.fixture
def adapter() -> MetadataTagAdapter:
    """Return a fresh adapter."""
    return MetadataTagAdapter()


@pytest.mark.parametrize(
    ("factory", "suffix"),
    [
        (create_mp3_fixture, ".mp3"),
        (create_wav_fixture, ".wav"),
        (create_aiff_fixture, ".aiff"),
    ],
)
def test_id3_writes_read_back_and_keep_the_traktor_frame(
    adapter: MetadataTagAdapter, tmp_path: Path, factory: Callable[..., Path], suffix: str
) -> None:
    """MP3, WAV, and AIFF writes read back every field and keep PRIV:TRAKTOR4 and custom TXXX."""
    path = factory(
        tmp_path / f"track{suffix}", tags={"custom_tag": "warmup"}, include_traktor_frame=True
    )
    before = adapter.read_metadata(path)
    adapter._write_tags_by_format(path, dict(WRITTEN))
    after = adapter.read_metadata(path)
    for field_name, value in WRITTEN.items():
        assert after.standard_tags[field_name] == value
    assert after.raw_custom_tags["PRIV:TRAKTOR4"] == before.raw_custom_tags["PRIV:TRAKTOR4"]
    assert after.raw_custom_tags["TXXX:CUSTOM_DJ_NOTE"] == "warmup"
    assert (after.duration, after.sample_rate) == (before.duration, before.sample_rate)


def test_mp3_write_leaves_the_audio_frames_unchanged(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """Every byte after the ID3v2 tag stays the same after a write."""
    path = create_mp3_fixture(tmp_path / "track.mp3", tags={"title": "Old"})
    data = path.read_bytes()
    audio = data[mp3_audio_offset(data) :]
    adapter._write_tags_by_format(path, {FieldName.TITLE: "New"})
    written = path.read_bytes()
    assert written[mp3_audio_offset(written) :] == audio


def test_wav_write_still_adds_list_info(adapter: MetadataTagAdapter, tmp_path: Path) -> None:
    """A WAV write keeps the RIFF LIST-INFO chunk that standalone players read."""
    path = create_wav_fixture(tmp_path / "track.wav")
    adapter._write_tags_by_format(path, {FieldName.TITLE: "Shades of Jae"})
    data = path.read_bytes()
    # A LIST chunk is "LIST", a 4-byte size, then the "INFO" list type.
    info_at = data.index(b"INFO")
    assert data[info_at - RIFF_HEADER_WIDTH : info_at - RIFF_FIELD_WIDTH] == b"LIST"
    assert b"INAM" in data[info_at:]
    assert b"Shades of Jae\x00" in data[info_at:]


def test_full_date_round_trips_through_tyer_tdat_and_time(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """A YEAR with month, day, and time writes as TYER, TDAT, and TIME and reads back whole."""
    path = create_mp3_fixture(tmp_path / "dated.mp3")
    adapter._write_tags_by_format(path, {FieldName.YEAR: FULL_DATE})
    meta = adapter.read_metadata(path)
    assert meta.standard_tags[FieldName.YEAR] == FULL_DATE
    assert "TDAT" not in meta.raw_custom_tags
    assert "TIME" not in meta.raw_custom_tags


@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        ((("TYER", "2019"),), "2019"),
        ((("TYER", "2019"), ("TDAT", "3112")), "2019-12-31"),
        ((("TYER", "2019"), ("TDAT", "3112"), ("TIME", "0745")), "2019-12-31T07:45"),
        ((("TYER", "2019"), ("TDAT", "31x2")), "2019"),
        ((("TYER", "2019"), ("TIME", "0745")), "2019"),
    ],
)
def test_v23_date_frames_rejoin_on_read(
    adapter: MetadataTagAdapter, tmp_path: Path, frames: tuple[tuple[str, str], ...], expected: str
) -> None:
    """TYER joins a valid TDAT, and TIME only after a date. Malformed parts are ignored."""
    body = b"".join(v23_frame(fid, b"\x01" + utf16(val)) for fid, val in frames)
    tag = tag_bytes(ID3V23_MAJOR, body)
    audio = create_mp3_fixture(tmp_path / "frames.mp3").read_bytes()
    path = tmp_path / "v23.mp3"
    path.write_bytes(tag + audio)
    assert adapter.read_metadata(path).standard_tags[FieldName.YEAR] == expected


def test_flac_write_sets_label_and_organization(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """FLAC writes LABEL and ORGANIZATION together and keeps the TRAKTOR4 field."""
    path = create_flac_fixture(tmp_path / "track.flac", include_traktor_comment=True)
    adapter._write_tags_by_format(path, {FieldName.LABEL: "KDJ", FieldName.TITLE: "Shades"})
    comment = read_flac_comment(path.read_bytes())
    assert comment is not None
    assert get_field_values(comment, "LABEL") == ["KDJ"]
    assert get_field_values(comment, "ORGANIZATION") == ["KDJ"]
    assert get_field_values(comment, "TITLE") == ["Shades"]
    assert get_field_values(comment, "TRAKTOR4") == [TRAKTOR_FLAC_PAYLOAD]


def test_new_flac_comment_names_infiniteprep_as_vendor(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """A FLAC file with no Vorbis comment gets one whose vendor is InfinitePrep and its version."""
    path = tmp_path / "bare.flac"
    path.write_bytes(flac_file())
    adapter._write_tags_by_format(path, {FieldName.TITLE: "Shades"})
    comment = read_flac_comment(path.read_bytes())
    assert comment is not None
    assert comment.vendor == f"InfinitePrep {__version__}"


@pytest.mark.parametrize(
    ("factory", "suffix"),
    [
        (create_mp3_fixture, ".mp3"),
        (create_wav_fixture, ".wav"),
        (create_aiff_fixture, ".aiff"),
        (create_flac_fixture, ".flac"),
    ],
)
def test_files_past_the_rewrite_limit_are_refused_unchanged(
    adapter: MetadataTagAdapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[..., Path],
    suffix: str,
) -> None:
    """A file larger than the rewrite limit raises before any read, and its bytes stay."""
    path = factory(tmp_path / f"large{suffix}")
    original = path.read_bytes()
    # A low limit stands in for the 1 GiB cap, so the test needs no 1 GiB file.
    monkeypatch.setattr(adapter_module, "MAX_WAV_REWRITE_BYTES", len(original) - 1)
    with pytest.raises(ValueError, match="tag write limit"):
        adapter._write_tags_by_format(path, {FieldName.TITLE: "Too big"})
    assert path.read_bytes() == original


def test_no_source_module_imports_mutagen() -> None:
    """The app no longer imports the GPL mutagen package anywhere under src/."""
    offenders: list[str] = []
    for source in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name == "mutagen" or name.startswith("mutagen.") for name in names):
                offenders.append(f"{source.relative_to(SOURCE_ROOT)}:{node.lineno}")
    assert offenders == []
