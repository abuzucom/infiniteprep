"""Staging on modeless media, resolved library parents, and untrusted folders above the library."""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import pytest

from infiniteprep import private_files
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import backup as backup_module
from infiniteprep.db.migrations.backup import backup_path_for, create_verified_backup
from infiniteprep.private_files import STAGING_DIR_PREFIX, private_staging_dir

SHARED_FOLDER: Final[int] = 0o777
STICKY_SHARED_FOLDER: Final[int] = 0o1777
OWNER_ONLY_FOLDER: Final[int] = 0o700
SHARED_STAGING_TEXT: Final[str] = "Other accounts can change files in the staging folder"
UNTRUSTED_ANCESTOR_TEXT: Final[str] = "Other accounts can change the folder"
LIBRARY_NAME: Final[str] = "library.db"

# Windows ignores POSIX mode bits, and a symbolic link there needs a privilege.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes and links only.")


def staging_leftovers(folder: Path) -> list[Path]:
    """Return every staging folder left in folder."""
    return [path for path in folder.iterdir() if path.name.startswith(STAGING_DIR_PREFIX)]


def make_table_library(db_path: Path) -> None:
    """Create a small library file with one table outside the app."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE notes (body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES ('kept')")


def patch_staging_mkdir(
    monkeypatch: pytest.MonkeyPatch, after_create: Callable[[Path], None]
) -> None:
    """Run after_create on each staging folder right after the real mkdir creates it."""
    real_mkdir = Path.mkdir

    def mkdir_then_adjust(path: Path, *args: Any, **kwargs: Any) -> None:
        real_mkdir(path, *args, **kwargs)
        if path.name.startswith(STAGING_DIR_PREFIX):
            after_create(path)

    monkeypatch.setattr(Path, "mkdir", mkdir_then_adjust)


def show_shared_mode(path: Path) -> None:
    """Give the folder mode 0777, as FAT and exFAT media report for every folder."""
    path.chmod(SHARED_FOLDER)


def warning_texts(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the text of each captured warning."""
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


@posix_only
def test_staging_on_modeless_media_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A staging folder that keeps shared modes logs a warning and still works."""
    patch_staging_mkdir(monkeypatch, show_shared_mode)

    with caplog.at_level(logging.WARNING), private_staging_dir(tmp_path) as staging:
        (staging / "copy.db").write_bytes(b"staged")

    assert any(SHARED_STAGING_TEXT in text for text in warning_texts(caplog))
    assert staging_leftovers(tmp_path) == []


@posix_only
def test_staging_with_a_foreign_owner_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A staging folder that reports another owner, as a FAT mount can, still works."""
    real_uid = os.getuid()
    monkeypatch.setattr(private_files.os, "getuid", lambda: real_uid + 1)

    with caplog.at_level(logging.WARNING), private_staging_dir(tmp_path) as staging:
        (staging / "copy.db").write_bytes(b"staged")

    assert any(SHARED_STAGING_TEXT in text for text in warning_texts(caplog))
    assert staging_leftovers(tmp_path) == []


@posix_only
def test_staging_replaced_by_a_link_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link swapped in for the new staging folder fails. The cleanup spares its target."""
    outside = tmp_path / "outside"
    outside.mkdir()
    kept_file = outside / "kept.txt"
    kept_file.write_bytes(b"outside data")

    def swap_for_link(path: Path) -> None:
        path.rmdir()
        path.symlink_to(outside, target_is_directory=True)

    patch_staging_mkdir(monkeypatch, swap_for_link)

    with pytest.raises(PermissionError), private_staging_dir(tmp_path):
        pytest.fail("The staging block must not run.")

    assert kept_file.read_bytes() == b"outside data"
    assert staging_leftovers(tmp_path) == []


@posix_only
def test_migration_backup_on_modeless_media_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A portable library on FAT or exFAT media still gets its pre-migration backup."""
    db_path = tmp_path / LIBRARY_NAME
    make_table_library(db_path)
    patch_staging_mkdir(monkeypatch, show_shared_mode)

    with closing(sqlite3.connect(db_path)) as conn:
        backup = create_verified_backup(conn, db_path, source_version=0)

    assert backup.is_file()
    assert staging_leftovers(tmp_path) == []


@posix_only
def test_library_behind_a_linked_folder_opens_through_the_real_path(tmp_path: Path) -> None:
    """SQLite receives the resolved folder. No link remains in the path it opens."""
    real_folder = tmp_path / "real"
    real_folder.mkdir()
    linked_folder = tmp_path / "linked"
    linked_folder.symlink_to(real_folder, target_is_directory=True)

    manager = DatabaseManager(linked_folder / LIBRARY_NAME)
    try:
        rows = manager.connect().execute("PRAGMA database_list").fetchall()
    finally:
        manager.close()

    main_file = next(row[2] for row in rows if row[1] == "main")
    assert Path(main_file) == (real_folder / LIBRARY_NAME).resolve()
    assert (real_folder / LIBRARY_NAME).is_file()


@posix_only
def test_backup_behind_a_linked_folder_stages_in_the_real_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backup copy goes through the resolved folder of the library."""
    real_folder = tmp_path / "real"
    real_folder.mkdir()
    make_table_library(real_folder / LIBRARY_NAME)
    linked_folder = tmp_path / "linked"
    linked_folder.symlink_to(real_folder, target_is_directory=True)
    targets: list[Path] = []
    real_copy = backup_module.copy_database

    def record_copy(source: sqlite3.Connection, target: Path) -> None:
        targets.append(target)
        real_copy(source, target)

    monkeypatch.setattr(backup_module, "copy_database", record_copy)

    with closing(sqlite3.connect(real_folder / LIBRARY_NAME)) as conn:
        create_verified_backup(conn, linked_folder / LIBRARY_NAME, source_version=0)

    [target] = targets
    assert target.parent.parent == real_folder.resolve()
    assert backup_path_for(real_folder / LIBRARY_NAME).is_file()


@posix_only
def test_shared_folder_above_the_library_logs_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A folder above the library that others can write without the sticky bit warns."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(SHARED_FOLDER)
    library_folder = shared / "library"
    library_folder.mkdir(mode=OWNER_ONLY_FOLDER)

    with caplog.at_level(logging.WARNING):
        DatabaseManager(library_folder / LIBRARY_NAME).close()

    assert any(UNTRUSTED_ANCESTOR_TEXT in text for text in warning_texts(caplog))


@posix_only
def test_sticky_shared_folder_above_the_library_logs_no_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A sticky world-writable folder such as /tmp counts as safe above the library."""
    sticky = tmp_path / "sticky"
    sticky.mkdir()
    sticky.chmod(STICKY_SHARED_FOLDER)
    library_folder = sticky / "library"
    library_folder.mkdir(mode=OWNER_ONLY_FOLDER)
    if not stat.S_IMODE(sticky.stat().st_mode) & stat.S_ISVTX:
        pytest.skip("The filesystem drops the sticky bit.")

    with caplog.at_level(logging.WARNING):
        DatabaseManager(library_folder / LIBRARY_NAME).close()

    assert not any(UNTRUSTED_ANCESTOR_TEXT in text for text in warning_texts(caplog))
