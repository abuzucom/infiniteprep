"""Behavioral test: the Data write probe never touches an existing file."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.storage_paths import (
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    FallbackAnswer,
    StorageMode,
    resolve_locations,
)
from tests.fixtures.storage_fallback import redirect_user_dirs_by_name

EXE_NAME = "InfinitePrep.exe"
# The name the probe once used. A file with it must survive the probe.
OLD_PROBE_NAME = ".write-probe"
KEPT_CONTENT = b"user file"


def _never_asked(folder: Path) -> FallbackAnswer:
    """Fail the test if the read-only prompt appears for a writable folder."""
    raise AssertionError(f"Prompt shown for writable folder {folder}")


def test_probe_leaves_existing_files_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Portable resolution keeps every file in Data unchanged and adds none."""
    redirect_user_dirs_by_name(tmp_path, monkeypatch)
    app_folder = tmp_path / "InfinitePrep"
    data = app_folder / PORTABLE_DATA_DIR_NAME
    data.mkdir(parents=True)
    (app_folder / EXE_NAME).write_bytes(b"")
    (app_folder / PORTABLE_MARKER_NAME).write_bytes(b"")
    (data / OLD_PROBE_NAME).write_bytes(KEPT_CONTENT)

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=_never_asked)

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert [path.name for path in data.iterdir()] == [OLD_PROBE_NAME]
    assert (data / OLD_PROBE_NAME).read_bytes() == KEPT_CONTENT
