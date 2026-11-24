"""Behavioral tests for the metadata read from an open file handle (D333).

The import framework opens a file once and reads every fact from that
handle. A file that is replaced while it is read must not reach the app.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.domain.enums import AudioFormat
from tests.fixtures.audio_factory import (
    create_flac_fixture,
    create_mp3_fixture,
    create_ogg_fixture,
    create_wav_fixture,
)

TAGS = {"artist": "Kerri Chandler", "title": "Rain"}


@pytest.fixture
def adapter() -> MetadataTagAdapter:
    """Return a fresh adapter."""
    return MetadataTagAdapter()


def test_a_wav_reads_its_tags_from_an_open_handle(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """A WAV reads the same fields from a handle as from a path."""
    path = create_wav_fixture(tmp_path / "track.wav", tags=TAGS)
    with path.open("rb") as handle:
        meta = adapter.read_metadata_from_handle(handle, path)

    assert meta.format == AudioFormat.WAV
    assert meta.standard_tags == TAGS


def test_an_mp3_reads_its_tags_from_an_open_handle(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """An MP3 reads the same fields from a handle as from a path."""
    path = create_mp3_fixture(tmp_path / "track.mp3", tags=TAGS)
    with path.open("rb") as handle:
        meta = adapter.read_metadata_from_handle(handle, path)

    assert meta.format == AudioFormat.MP3
    assert meta.standard_tags == TAGS


def test_a_flac_reads_its_tags_from_an_open_handle(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """A FLAC reads the same fields from a handle as from a path."""
    path = create_flac_fixture(tmp_path / "track.flac", tags=TAGS)
    with path.open("rb") as handle:
        meta = adapter.read_metadata_from_handle(handle, path)

    assert meta.format == AudioFormat.FLAC
    assert meta.standard_tags == TAGS


def test_an_ogg_reads_its_properties_from_an_open_handle(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """An Ogg reads the same properties from a handle as from a path."""
    path = create_ogg_fixture(tmp_path / "track.ogg")
    with path.open("rb") as handle:
        meta = adapter.read_metadata_from_handle(handle, path)
    from_path = adapter.read_metadata(path)

    assert meta.format == AudioFormat.OGG
    assert meta.duration == from_path.duration
    assert meta.sample_rate == from_path.sample_rate
    assert meta.channels == from_path.channels


def test_an_unsupported_extension_reports_the_same_warning_from_a_handle(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """A file the app cannot read reports the unsupported extension."""
    path = tmp_path / "track.xyz"
    path.write_bytes(b"not audio")
    with path.open("rb") as handle:
        meta = adapter.read_metadata_from_handle(handle, path)

    assert meta.format == AudioFormat.UNSUPPORTED
    assert any("Unsupported file extension" in warning for warning in meta.warnings)


def test_a_damaged_file_reports_the_same_warning_from_a_handle(
    adapter: MetadataTagAdapter, tmp_path: Path
) -> None:
    """A file the parser cannot read reports a damaged container."""
    path = tmp_path / "track.wav"
    path.write_bytes(b"not a wav file at all")
    with path.open("rb") as handle:
        meta = adapter.read_metadata_from_handle(handle, path)

    assert meta.format == AudioFormat.WAV
    assert any("Damaged or unreadable" in warning for warning in meta.warnings)
