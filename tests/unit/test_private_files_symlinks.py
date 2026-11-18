"""Private file helpers never change or write a file through a symbolic link."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from infiniteprep.db.migrations.backup import copy_database
from infiniteprep.private_files import create_new_private_file, ensure_private_file

WORLD_READABLE_FILE: Final[int] = 0o644
OWNER_ONLY_FILE: Final[int] = 0o600
OUTSIDE_TEXT: Final[bytes] = b"a file outside the app folder\n"
LIBRARY_TEXT: Final[bytes] = b"library bytes"

# Windows ignores POSIX mode bits, and a symbolic link there needs a privilege.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes and links only.")


def mode_of(path: Path) -> int:
    """Return the permission bits of path without following a link."""
    return stat.S_IMODE(path.lstat().st_mode)


def plant_link(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Create a world-readable outside file and a link to it under name."""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(OUTSIDE_TEXT)
    outside.chmod(WORLD_READABLE_FILE)
    link = tmp_path / "app" / name
    link.parent.mkdir()
    link.symlink_to(outside)
    return link, outside


@posix_only
def test_ensure_private_file_leaves_a_link_target_mode_alone(tmp_path: Path) -> None:
    """A link at the library name never passes the chmod on to its target."""
    link, outside = plant_link(tmp_path, "library.db")

    ensure_private_file(link)

    assert mode_of(outside) == WORLD_READABLE_FILE
    assert outside.read_bytes() == OUTSIDE_TEXT


@posix_only
def test_ensure_private_file_still_tightens_a_regular_file(tmp_path: Path) -> None:
    """A regular file at the name loses its group and other bits."""
    library = tmp_path / "library.db"
    library.write_bytes(LIBRARY_TEXT)
    library.chmod(WORLD_READABLE_FILE)

    ensure_private_file(library)

    assert mode_of(library) == OWNER_ONLY_FILE


@posix_only
def test_create_new_private_file_refuses_a_planted_link(tmp_path: Path) -> None:
    """A link at a new file name raises and its target keeps its bytes and mode."""
    link, outside = plant_link(tmp_path, "library.db.restore.tmp")

    with pytest.raises(FileExistsError):
        create_new_private_file(link)

    assert outside.read_bytes() == OUTSIDE_TEXT
    assert mode_of(outside) == WORLD_READABLE_FILE


@posix_only
def test_backup_copy_refuses_a_planted_link(tmp_path: Path) -> None:
    """A link at the backup temporary name fails the copy before SQLite writes to it."""
    link, outside = plant_link(tmp_path, "library.db.pre-migration-backup.tmp")
    library = tmp_path / "app" / "library.db"
    with closing(sqlite3.connect(library)) as conn, conn:
        conn.execute("CREATE TABLE notes (body TEXT)")

    with closing(sqlite3.connect(library)) as source, pytest.raises(FileExistsError):
        copy_database(source, link)

    assert outside.read_bytes() == OUTSIDE_TEXT


def test_create_new_private_file_makes_an_empty_owner_only_file(tmp_path: Path) -> None:
    """A free name receives a new empty file. POSIX gives it mode 0600."""
    target = tmp_path / "staged.db"

    create_new_private_file(target)

    assert target.read_bytes() == b""
    if os.name != "nt":
        assert mode_of(target) == OWNER_ONLY_FILE
