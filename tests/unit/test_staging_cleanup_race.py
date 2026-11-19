"""Staging cleanup never deletes through a link swapped in after its check. Link loops stop."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from typing import Final

import pytest

from infiniteprep import private_files
from infiniteprep.private_files import STAGING_DIR_PREFIX, private_staging_dir

OUTSIDE_TEXT: Final[bytes] = b"outside data"
CLEANUP_FAILURE_TEXT: Final[str] = "Could not remove the staging folder"

# A symbolic link on Windows needs a privilege, and dir_fd cleanup is POSIX only.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX links and dir_fd only.")


@posix_only
def test_cleanup_spares_a_link_swapped_in_after_the_link_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A link that passes the link check still never leads the cleanup into its target."""
    outside = tmp_path / "outside"
    outside.mkdir()
    kept_file = outside / "kept.txt"
    kept_file.write_bytes(OUTSIDE_TEXT)
    moved_away = tmp_path / "moved-away"
    real_is_symlink = Path.is_symlink

    def hide_staging_links(path: Path) -> bool:
        # The swap lands between the link check and the listing, as a race would.
        if path.name.startswith(STAGING_DIR_PREFIX):
            return False
        return real_is_symlink(path)

    with caplog.at_level(logging.WARNING), private_staging_dir(tmp_path) as staging:
        (staging / "copy.db").write_bytes(b"staged")
        staging.rename(moved_away)
        staging.symlink_to(outside, target_is_directory=True)
        monkeypatch.setattr(Path, "is_symlink", hide_staging_links)

    assert kept_file.read_bytes() == OUTSIDE_TEXT
    # The no-follow open refuses the link, so the cleanup logs and leaves every name in place.
    assert any(
        CLEANUP_FAILURE_TEXT in record.getMessage() and staging.name in record.getMessage()
        for record in caplog.records
    )
    # Path.is_symlink stays patched here, so os.path.islink reads the real link state.
    assert os.path.islink(staging)
    assert [path.name for path in moved_away.iterdir()] == ["copy.db"]


@posix_only
def test_cleanup_removes_every_staged_file_and_the_folder(tmp_path: Path) -> None:
    """The descriptor cleanup removes each staged file and then the folder."""
    with private_staging_dir(tmp_path) as staging:
        (staging / "copy.db").write_bytes(b"staged")
        (staging / "copy.db-journal").write_bytes(b"journal")

    assert not staging.exists()
    assert list(tmp_path.iterdir()) == []


@posix_only
def test_link_loop_in_the_library_path_raises_eloop(tmp_path: Path) -> None:
    """A link cycle stops the folder walk with ELOOP after the hop limit."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)

    with pytest.raises(OSError) as caught:
        private_files._traversed_folders(first / "library")

    assert caught.value.errno == errno.ELOOP
