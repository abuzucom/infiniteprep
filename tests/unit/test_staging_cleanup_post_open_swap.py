"""A link swapped in after the cleanup opens its folder descriptor leaves the target whole."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

from infiniteprep import private_files
from infiniteprep.private_files import private_staging_dir

OUTSIDE_TEXT: Final[bytes] = b"outside data"
STAGED_NAMES: Final[tuple[str, ...]] = ("copy.db", "copy.db-journal")

# A symbolic link on Windows needs a privilege, and dir_fd cleanup is POSIX only.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX links and dir_fd only.")


@posix_only
def test_cleanup_after_a_post_open_swap_clears_only_the_real_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The descriptor keeps the original folder after the name turns into a link."""
    outside = tmp_path / "outside"
    outside.mkdir()
    kept_file = outside / "copy.db"
    kept_file.write_bytes(OUTSIDE_TEXT)
    moved_away = tmp_path / "moved-away"
    real_listdir = os.listdir
    swapped: list[Path] = []

    def swap_then_list(target: int | str | Path = ".") -> list[str]:
        # The swap lands after the descriptor open and before the listing, as a race would.
        if isinstance(target, int) and not swapped:
            staging.rename(moved_away)
            staging.symlink_to(outside, target_is_directory=True)
            swapped.append(staging)
        return real_listdir(target)

    with private_staging_dir(tmp_path) as staging:
        for name in STAGED_NAMES:
            (staging / name).write_bytes(b"staged")
        monkeypatch.setattr(private_files.os, "listdir", swap_then_list)

    assert swapped == [staging]
    assert kept_file.read_bytes() == OUTSIDE_TEXT
    assert list(moved_away.iterdir()) == []
