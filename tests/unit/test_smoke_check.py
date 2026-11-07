"""Behavioral tests for the hidden --smoke-check flag that CI runs on built bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infiniteprep import __version__
from infiniteprep.main import SMOKE_CHECK_FLAG, SMOKE_CHECK_USAGE_ERROR, main


def test_smoke_check_writes_the_version(qapp: Any, tmp_path: Path) -> None:
    """The flag loads the runtime libraries and Qt, writes the version, and returns 0.

    The qapp fixture supplies the QApplication the check reuses.
    """
    result_file = tmp_path / "smoke-version.txt"

    exit_code = main([SMOKE_CHECK_FLAG, str(result_file)])

    assert exit_code == 0
    assert result_file.read_text(encoding="utf-8") == __version__


def test_smoke_check_needs_exactly_one_path(tmp_path: Path) -> None:
    """The flag with no path, or with two, returns the usage error and writes no file."""
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"

    assert main([SMOKE_CHECK_FLAG]) == SMOKE_CHECK_USAGE_ERROR
    assert main([SMOKE_CHECK_FLAG, str(first), str(second)]) == SMOKE_CHECK_USAGE_ERROR
    assert not first.exists()
    assert not second.exists()


def test_smoke_check_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    """An existing file keeps its content, and the flag returns the usage error."""
    existing = tmp_path / "keep.txt"
    existing.write_text("original", encoding="utf-8")

    assert main([SMOKE_CHECK_FLAG, str(existing)]) == SMOKE_CHECK_USAGE_ERROR
    assert existing.read_text(encoding="utf-8") == "original"


def test_smoke_check_refuses_a_directory(tmp_path: Path) -> None:
    """A directory path returns the usage error and stays an empty directory."""
    folder = tmp_path / "folder"
    folder.mkdir()

    assert main([SMOKE_CHECK_FLAG, str(folder)]) == SMOKE_CHECK_USAGE_ERROR
    assert list(folder.iterdir()) == []
