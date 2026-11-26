"""Owner-only staging folders, refused library links, and a private data folder."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import pytest

from infiniteprep import storage_paths
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import backup as backup_module
from infiniteprep.db.migrations.backup import (
    create_verified_backup,
    restore_pre_migration_backup,
)
from infiniteprep.private_files import STAGING_DIR_PREFIX, private_staging_dir
from infiniteprep.startup_storage import DATABASE_FILE_NAME, OpenedStorage, open_storage
from infiniteprep.storage_paths import FallbackAnswer, StorageRegistry
from tests.fixtures.storage_fallback import redirect_user_dirs_by_name

OWNER_ONLY_DIR: Final[int] = 0o700
WORLD_READABLE_FILE: Final[int] = 0o644
OUTSIDE_TEXT: Final[bytes] = b"a file outside the app folder\n"
EXE_NAME: Final[str] = "InfinitePrep.exe"

# Windows ignores POSIX mode bits, and a symbolic link there needs a privilege.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes and links only.")


def mode_of(path: Path) -> int:
    """Return the permission bits of path without following a link."""
    return stat.S_IMODE(path.lstat().st_mode)


def staging_leftovers(folder: Path) -> list[Path]:
    """Return every staging folder left in folder."""
    return [path for path in folder.iterdir() if path.name.startswith(STAGING_DIR_PREFIX)]


def make_table_library(db_path: Path) -> None:
    """Create a small library file with one table outside the app."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE notes (body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES ('kept')")


@posix_only
def test_staging_dir_owner_only_removed(tmp_path: Path) -> None:
    """The staging folder allows only the owner and leaves nothing behind."""
    with private_staging_dir(tmp_path) as staging:
        assert staging.parent == tmp_path
        assert mode_of(staging) == OWNER_ONLY_DIR
        (staging / "copy.db").write_bytes(b"staged")
        (staging / "copy.db-journal").write_bytes(b"journal")

    assert staging_leftovers(tmp_path) == []


def test_staging_dir_removed_after_error(tmp_path: Path) -> None:
    """An error inside the block still removes the folder and its files."""
    with pytest.raises(RuntimeError, match="copy failed"), private_staging_dir(tmp_path) as staging:
        (staging / "copy.db").write_bytes(b"partial")
        raise RuntimeError("copy failed")

    assert staging_leftovers(tmp_path) == []


@posix_only
def test_linked_library_path_is_refused(tmp_path: Path) -> None:
    """A link at the library name fails the open before SQLite touches the target."""
    outside = tmp_path / "outside.db"
    outside.write_bytes(OUTSIDE_TEXT)
    outside.chmod(WORLD_READABLE_FILE)
    library = tmp_path / "app" / "library.db"
    library.parent.mkdir()
    library.symlink_to(outside)

    with pytest.raises(OSError, match="symbolic link"):
        DatabaseManager(library)

    assert outside.read_bytes() == OUTSIDE_TEXT
    assert mode_of(outside) == WORLD_READABLE_FILE


@posix_only
def test_migration_backup_owner_only_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The backup copy and its record write happen inside a private staging folder."""
    db_path = tmp_path / "library.db"
    make_table_library(db_path)
    seen_parents: list[tuple[Path, int]] = []
    real_copy = backup_module.copy_database

    def record_copy(source: sqlite3.Connection, target: Path) -> None:
        seen_parents.append((target.parent, mode_of(target.parent)))
        real_copy(source, target)

    monkeypatch.setattr(backup_module, "copy_database", record_copy)

    with closing(sqlite3.connect(db_path)) as conn:
        backup = create_verified_backup(conn, db_path, source_version=0)

    [(parent, parent_mode)] = seen_parents
    assert parent.parent == tmp_path
    assert parent.name.startswith(STAGING_DIR_PREFIX)
    assert parent_mode == OWNER_ONLY_DIR
    assert backup.is_file()
    assert staging_leftovers(tmp_path) == []


@posix_only
def test_restore_copy_owner_only_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The staged restore copy lives in a private staging folder until the swap."""
    db_path = tmp_path / "library.db"
    make_table_library(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        create_verified_backup(conn, db_path, source_version=0)
    seen_parents: list[tuple[Path, int]] = []
    real_copyfile = shutil.copyfile

    def record_copyfile(source: str | Path, target: str | Path) -> object:
        target_path = Path(target)
        seen_parents.append((target_path.parent, mode_of(target_path.parent)))
        return real_copyfile(source, target)

    monkeypatch.setattr(backup_module.shutil, "copyfile", record_copyfile)

    restore_pre_migration_backup(db_path)

    [(parent, parent_mode)] = seen_parents
    assert parent.parent == tmp_path
    assert parent.name.startswith(STAGING_DIR_PREFIX)
    assert parent_mode == OWNER_ONLY_DIR
    assert staging_leftovers(tmp_path) == []


@pytest.fixture
def startup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolate the per-user folders and the registry, and record shown errors."""
    redirect_user_dirs_by_name(tmp_path, monkeypatch)
    monkeypatch.setattr(storage_paths, "STORAGE_REGISTRY", StorageRegistry())
    app_folder = tmp_path / "InfinitePrep"
    app_folder.mkdir()
    (app_folder / EXE_NAME).write_bytes(b"")
    return {"app": app_folder, "errors": []}


@posix_only
def test_startup_creates_private_data_folder(startup_env: dict[str, Any]) -> None:
    """A first start creates the per-user data folder with mode 0700."""
    opened = open_storage(
        startup_env["app"] / EXE_NAME,
        frozen=False,
        confirm_fallback=lambda _folder: FallbackAnswer(use_per_user=False, remember=False),
        show_error=startup_env["errors"].append,
    )

    assert isinstance(opened, OpenedStorage)
    try:
        assert mode_of(opened.locations.data_dir) == OWNER_ONLY_DIR
        assert (opened.locations.data_dir / DATABASE_FILE_NAME).is_file()
    finally:
        opened.repository.db.close()
