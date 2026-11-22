"""Tests for the atomic move that refuses to replace an existing file."""

from __future__ import annotations

import ctypes
import errno
import sys
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io import no_clobber_move
from infiniteprep.adapters.metadata_io.no_clobber_move import move_to_new_path

SOURCE_BYTES = b"tagged audio"
EXISTING_BYTES = b"another program saved this file"
# The C library returns this value from a failed rename call.
FAILED_CALL_RESULT = -1
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="Windows moves with MoveFileEx.")


def make_source(tmp_path: Path) -> Path:
    """Write the file to move."""
    source = tmp_path / "track.wav"
    source.write_bytes(SOURCE_BYTES)
    return source


def assert_source_kept(tmp_path: Path, source: Path, destination: Path) -> None:
    """The source keeps its name and bytes, and nothing appears at the destination."""
    assert source.read_bytes() == SOURCE_BYTES
    assert not destination.exists()
    assert [path.name for path in tmp_path.iterdir()] == ["track.wav"]


def test_move_to_a_free_name_moves_the_file(tmp_path: Path) -> None:
    """The file arrives under the new name and leaves nothing else behind."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"

    move_to_new_path(source, destination)

    assert destination.read_bytes() == SOURCE_BYTES
    assert [path.name for path in tmp_path.iterdir()] == ["renamed.wav"]


def test_move_onto_an_existing_file_keeps_both_files(tmp_path: Path) -> None:
    """The existing file stays unchanged, the source keeps its name, and no extra file appears."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"
    destination.write_bytes(EXISTING_BYTES)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        move_to_new_path(source, destination)

    assert destination.read_bytes() == EXISTING_BYTES
    assert source.read_bytes() == SOURCE_BYTES
    assert sorted(path.name for path in tmp_path.iterdir()) == ["renamed.wav", "track.wav"]


@POSIX_ONLY
def test_no_atomic_rename_keeps_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a no-replace rename call, the move is refused and the file keeps its name."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"
    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: None)

    with pytest.raises(OSError, match="no move that refuses an existing file"):
        move_to_new_path(source, destination)

    assert_source_kept(tmp_path, source, destination)


@POSIX_ONLY
def test_rejected_atomic_rename_keeps_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file system that answers EINVAL to the no-replace flag refuses the move."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"

    def reject_flag(_source: bytes, _destination: bytes) -> int:
        ctypes.set_errno(errno.EINVAL)
        return FAILED_CALL_RESULT

    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: reject_flag)

    with pytest.raises(OSError, match="no move that refuses an existing file"):
        move_to_new_path(source, destination)

    assert_source_kept(tmp_path, source, destination)
