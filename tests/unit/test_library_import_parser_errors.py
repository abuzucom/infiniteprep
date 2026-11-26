"""Tests for the parser errors the import framework handles on its own."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.services.library_import.track_files import read_track_file_facts


class BrokenAdapter(MetadataTagAdapter):
    """An adapter whose parser raises an error the framework does not document."""

    def detect_format(self, file_path: Path) -> AudioFormat:
        """Raise a defect that no parser error covers."""
        raise RuntimeError("the adapter is broken")


class DamagedAdapter(MetadataTagAdapter):
    """An adapter whose parser raises a documented parser error."""

    def detect_format(self, file_path: Path) -> AudioFormat:
        """Raise the error the audio parser documents."""
        raise ValueError("the container is damaged")


def test_adapter_defect_reaches_caller(tmp_path: Path) -> None:
    """An error the parser does not document stops the import instead of hiding it."""
    track = tmp_path / "track.wav"
    track.write_bytes(b"RIFF")

    with pytest.raises(RuntimeError, match="the adapter is broken"):
        read_track_file_facts(track, tag_adapter=BrokenAdapter())


def test_documented_parser_error_leaves_unresolved(tmp_path: Path) -> None:
    """A parser error the framework documents leaves the track unresolved."""
    track = tmp_path / "track.wav"
    track.write_bytes(b"RIFF")

    assert read_track_file_facts(track, tag_adapter=DamagedAdapter()) is None
