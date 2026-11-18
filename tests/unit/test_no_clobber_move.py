"""Tests for the atomic move that refuses to replace an existing file."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from infiniteprep.adapters.metadata_io import no_clobber_move
from infiniteprep.adapters.metadata_io.no_clobber_move import move_to_new_path

SOURCE_BYTES = b"tagged audio"
EXISTING_BYTES = b"another program saved this file"
# The kernel no-replace rename on Linux and macOS, and the hard link path every POSIX
# system offers. Windows always takes MoveFileEx, so both cases cover it the same way.
MOVE_PATHS = ("native", "hard_link")


@pytest.fixture(params=MOVE_PATHS)
def move_path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Select the native rename or force the hard link fallback."""
    if request.param == "hard_link":
        monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: None)
    return str(request.param)


def make_source(tmp_path: Path) -> Path:
    """Write the file to move."""
    source = tmp_path / "track.wav"
    source.write_bytes(SOURCE_BYTES)
    return source


def test_move_to_a_free_name_moves_the_file(tmp_path: Path, move_path: str) -> None:
    """The file arrives under the new name and leaves nothing else behind."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"

    move_to_new_path(source, destination)

    assert destination.read_bytes() == SOURCE_BYTES
    assert [path.name for path in tmp_path.iterdir()] == ["renamed.wav"]


def test_move_onto_an_existing_file_keeps_both_files(tmp_path: Path, move_path: str) -> None:
    """The existing file stays unchanged, the source keeps its name, and no extra file appears."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"
    destination.write_bytes(EXISTING_BYTES)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        move_to_new_path(source, destination)

    assert destination.read_bytes() == EXISTING_BYTES
    assert source.read_bytes() == SOURCE_BYTES
    assert sorted(path.name for path in tmp_path.iterdir()) == ["renamed.wav", "track.wav"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows moves with MoveFileEx only.")
def test_file_system_without_hard_links_keeps_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a safe move, the file keeps its old name and nothing replaces the target."""
    source = make_source(tmp_path)
    destination = tmp_path / "renamed.wav"
    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: None)

    def refuse_link(*_args: object) -> None:
        raise OSError(errno.EPERM, os.strerror(errno.EPERM))

    monkeypatch.setattr(no_clobber_move.os, "link", refuse_link)

    with pytest.raises(OSError, match="no move that refuses an existing file"):
        move_to_new_path(source, destination)

    assert source.read_bytes() == SOURCE_BYTES
    assert not destination.exists()
