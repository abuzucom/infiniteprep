"""Focused coverage for track fact success and failure paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import IO, BinaryIO, cast

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import (
    MetadataTagAdapter,
    TechnicalAndTagMetadata,
)
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.library_import import track_files
from infiniteprep.services.library_import.filesystem import (
    FileIdentity,
    OpenedFile,
    open_inside_root,
)
from infiniteprep.services.library_import.track_files import (
    MAX_TRACK_FILE_BYTES,
    read_opened_file_facts,
    read_track_file_facts,
)


class StubAdapter(MetadataTagAdapter):
    """Return fixed metadata and optionally fail at a requested seam."""

    def __init__(
        self,
        *,
        audio_format: AudioFormat = AudioFormat.WAV,
        metadata: TechnicalAndTagMetadata | None = None,
        detect_error: Exception | None = None,
        metadata_error: Exception | None = None,
    ) -> None:
        """Initialize the adapter with the requested results."""
        super().__init__()
        self.audio_format = audio_format
        self.metadata = metadata or make_metadata()
        self.detect_error = detect_error
        self.metadata_error = metadata_error

    def detect_format(self, file_path: Path) -> AudioFormat:
        """Return the configured format or raise the configured error."""
        if self.detect_error is not None:
            raise self.detect_error
        return self.audio_format

    def read_metadata(self, file_path: Path) -> TechnicalAndTagMetadata:
        """Return fixed metadata or raise the configured error."""
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata

    def read_metadata_from_handle(
        self, handle: BinaryIO, file_path: Path
    ) -> TechnicalAndTagMetadata:
        """Return fixed metadata or raise the configured error."""
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata


class FailingReadHandle:
    """Delegate file identity calls but fail every content read."""

    def __init__(self, wrapped: BinaryIO) -> None:
        """Wrap an open binary handle."""
        self.wrapped = wrapped

    def fileno(self) -> int:
        """Return the wrapped handle descriptor."""
        return self.wrapped.fileno()

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek in the wrapped handle."""
        return self.wrapped.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        """Fail with the read error the parser documents."""
        raise OSError("the handle cannot be read")


class InvalidDescriptorHandle:
    """Expose a descriptor that the operating system cannot inspect."""

    def fileno(self) -> int:
        """Return a descriptor that cannot identify an open file."""
        return -1


def make_metadata() -> TechnicalAndTagMetadata:
    """Return metadata with values that the fact builder must preserve."""
    return TechnicalAndTagMetadata(
        format=AudioFormat.WAV,
        duration=12.5,
        sample_rate=44_100,
        channels=2,
        bitrate=320_000,
        standard_tags={"artist": "Artist"},
        raw_custom_tags={"custom": ["value"]},
        warnings=["ordinary warning", "XDJ-RX3 Warning 1"],
    )


def test_track_file_facts_return_the_real_size_hash_and_metadata(tmp_path: Path) -> None:
    """A readable file produces complete facts from the real path."""
    track = tmp_path / "track.wav"
    track.write_bytes(b"audio bytes")

    facts = read_track_file_facts(track, StubAdapter())

    assert facts is not None
    assert facts.file_size == len(b"audio bytes")
    assert facts.content_hash == compute_sha256(track)
    assert facts.audio_format is AudioFormat.WAV
    assert facts.duration == 12.5
    assert facts.original_tags["standard_tags"] == {"artist": "Artist"}
    assert facts.original_tags["technical"]["sample_rate"] == 44_100
    assert facts.original_tags["technical"]["xdj_rx3_warnings"] == ["XDJ-RX3 Warning 1"]


def test_track_file_facts_reject_a_missing_file(tmp_path: Path) -> None:
    """A file that cannot be stat produces no facts."""
    assert read_track_file_facts(tmp_path / "missing.wav", StubAdapter()) is None


def test_track_file_facts_reject_an_unsupported_format(tmp_path: Path) -> None:
    """A file with an unsupported extension produces no facts."""
    track = tmp_path / "track.txt"
    track.write_bytes(b"audio bytes")

    assert read_track_file_facts(track) is None


def test_track_file_facts_reject_a_file_over_the_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file over the read limit is rejected before any content read."""
    track = tmp_path / "track.wav"
    track.write_bytes(b"audio bytes")
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self: SimpleNamespace(st_size=MAX_TRACK_FILE_BYTES + 1),
    )

    assert read_track_file_facts(track, StubAdapter()) is None


def test_track_file_facts_reject_a_failed_hash_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hash read failure produces no facts."""
    track = tmp_path / "track.wav"
    track.write_bytes(b"audio bytes")

    def fail_hash(file_path: Path) -> str:
        """Fail the file hash read."""
        raise OSError("the hash cannot be read")

    monkeypatch.setattr(track_files, "compute_sha256", fail_hash)

    assert read_track_file_facts(track, StubAdapter()) is None


def test_track_file_facts_reject_a_failed_header_read(tmp_path: Path) -> None:
    """A documented header read failure produces no facts."""
    track = tmp_path / "track.wav"
    track.write_bytes(b"audio bytes")
    adapter = StubAdapter(metadata_error=ValueError("the header is damaged"))

    assert read_track_file_facts(track, adapter) is None


def test_opened_file_facts_use_the_open_handle(tmp_path: Path) -> None:
    """An open file produces facts from its handle and its recorded identity."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio bytes")

    opened = open_inside_root(track, root)
    assert opened is not None
    with opened:
        facts = read_opened_file_facts(opened, StubAdapter())

    assert facts is not None
    assert facts.file_size == len(b"audio bytes")
    assert facts.content_hash == compute_sha256(track)
    assert facts.duration == 12.5


def test_opened_file_facts_reject_an_unreadable_stat() -> None:
    """An open handle with an invalid descriptor produces no facts."""
    handle = cast(IO[bytes], InvalidDescriptorHandle())
    opened = OpenedFile(
        path=Path("track.wav"),
        handle=handle,
        identity=FileIdentity(device=1, inode=1),
    )

    assert read_opened_file_facts(opened, StubAdapter()) is None


def test_opened_file_facts_reject_a_failed_hash_read(tmp_path: Path) -> None:
    """A handle read failure produces no facts."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio bytes")
    opened = open_inside_root(track, root)
    assert opened is not None
    failing_handle = cast(IO[bytes], FailingReadHandle(opened.handle))
    failing = OpenedFile(
        path=opened.path,
        handle=failing_handle,
        identity=opened.identity,
    )

    with opened:
        assert read_opened_file_facts(failing, StubAdapter()) is None


def test_opened_file_facts_reject_a_failed_header_read(tmp_path: Path) -> None:
    """A documented handle header failure produces no facts."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio bytes")
    opened = open_inside_root(track, root)
    assert opened is not None
    adapter = StubAdapter(metadata_error=ValueError("the header is damaged"))

    with opened:
        assert read_opened_file_facts(opened, adapter) is None


def test_opened_file_facts_reject_a_changed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that changed during the read produces no facts."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio bytes")
    opened = open_inside_root(track, root)
    assert opened is not None
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self: SimpleNamespace(st_dev=2, st_ino=2),
    )

    with opened:
        assert read_opened_file_facts(opened, StubAdapter()) is None
