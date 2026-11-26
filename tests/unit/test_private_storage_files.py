"""Owner-only library and settings files, durable settings writes, and atomic fpcalc installs."""

from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import stat
import zipfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations.backup import (
    backup_path_for,
    create_verified_backup,
    restore_pre_migration_backup,
)

OWNER_ONLY_DIR: Final[int] = 0o700
OWNER_ONLY_FILE: Final[int] = 0o600
WORLD_READABLE_FILE: Final[int] = 0o644
OLD_BINARY: Final[bytes] = b"#!/bin/sh\necho old fpcalc\n"
NEW_BINARY: Final[bytes] = b"#!/bin/sh\necho new fpcalc\n" * 64
# The interrupted install writes the first half of the binary before it fails.
HALF_PAYLOAD_DIVISOR: Final[int] = 2

posix_only = pytest.mark.skipif(os.name == "nt", reason="Windows ignores POSIX mode bits.")


def mode_of(path: Path) -> int:
    """Return the permission bits of path."""
    return stat.S_IMODE(path.stat().st_mode)


def make_table_library(db_path: Path) -> None:
    """Create a small library file with one table outside the app."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE notes (body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES ('kept')")


@posix_only
def test_new_library_folders_owner_only(tmp_path: Path) -> None:
    """A fresh library, its WAL file, and each folder the app creates allow only the owner."""
    db_path = tmp_path / "created" / "nested" / "library.db"

    manager = DatabaseManager(db_path)
    try:
        manager.connect().execute("CREATE TABLE probe (value INTEGER)")
        wal_path = db_path.with_name(db_path.name + "-wal")
        assert wal_path.exists()
        assert mode_of(wal_path) == OWNER_ONLY_FILE
    finally:
        manager.close()

    assert mode_of(db_path) == OWNER_ONLY_FILE
    assert mode_of(db_path.parent) == OWNER_ONLY_DIR
    assert mode_of(db_path.parent.parent) == OWNER_ONLY_DIR


@posix_only
def test_world_readable_library_tightened(tmp_path: Path) -> None:
    """A library from an earlier version loses its group and other read bits on open."""
    db_path = tmp_path / "library.db"
    make_table_library(db_path)
    db_path.chmod(WORLD_READABLE_FILE)

    DatabaseManager(db_path).close()

    assert mode_of(db_path) == OWNER_ONLY_FILE


@posix_only
def test_backup_restore_are_owner_only(tmp_path: Path) -> None:
    """The pre-migration backup and the library a restore installs allow only the owner."""
    db_path = tmp_path / "library.db"
    make_table_library(db_path)
    db_path.chmod(OWNER_ONLY_FILE)

    with closing(sqlite3.connect(db_path)) as conn:
        backup = create_verified_backup(conn, db_path, source_version=0)
    assert backup == backup_path_for(db_path)
    assert mode_of(backup) == OWNER_ONLY_FILE

    restore_pre_migration_backup(db_path)

    assert mode_of(db_path) == OWNER_ONLY_FILE


@posix_only
def test_settings_file_folder_owner_only(tmp_path: Path) -> None:
    """The settings folder the app creates and the settings file allow only the owner."""
    settings_path = tmp_path / "config" / "settings.json"

    SettingsManager(settings_path).save(AppSettings())

    assert mode_of(settings_path) == OWNER_ONLY_FILE
    assert mode_of(settings_path.parent) == OWNER_ONLY_DIR


def test_settings_reach_disk_before_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The new settings bytes sync before the rename. On POSIX the folder syncs after it."""
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def record_fsync(descriptor: int) -> None:
        events.append("fsync")
        real_fsync(descriptor)

    def record_replace(source: str | Path, target: str | Path) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "replace", record_replace)
    settings_path = tmp_path / "settings.json"

    SettingsManager(settings_path).save(AppSettings())

    expected = ["fsync", "replace"] if os.name == "nt" else ["fsync", "replace", "fsync"]
    assert events == expected
    assert SettingsManager(settings_path).load() == AppSettings()


def zip_with_fpcalc(bootstrap: FpcalcBootstrapService, payload: bytes, folder: Path) -> bytes:
    """Return ZIP bytes that hold payload under the platform fpcalc name."""
    archive = folder / f"fpcalc-{len(payload)}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"chromaprint-fpcalc/{bootstrap.binary_filename()}", payload)
    return archive.read_bytes()


def install(bootstrap: FpcalcBootstrapService, archive: bytes) -> Path:
    """Install archive with its own digest and consent."""
    return bootstrap.install_from_archive_bytes(
        archive_bytes=archive,
        expected_sha256=hashlib.sha256(archive).hexdigest(),
        user_consented=True,
    )


def test_interrupted_install_keeps_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that fails halfway leaves the old fpcalc whole and no partial file behind."""
    bin_dir = tmp_path / "bin"
    bootstrap = FpcalcBootstrapService(install_dir=bin_dir)
    installed = install(bootstrap, zip_with_fpcalc(bootstrap, OLD_BINARY, tmp_path))
    new_archive = zip_with_fpcalc(bootstrap, NEW_BINARY, tmp_path)
    real_write_bytes: Callable[[Path, bytes], int] = Path.write_bytes

    def write_half_then_fail(path: Path, data: bytes) -> int:
        real_write_bytes(path, data[: len(data) // HALF_PAYLOAD_DIVISOR])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", write_half_then_fail)

    with pytest.raises(OSError, match="No space"):
        install(bootstrap, new_archive)

    assert installed.read_bytes() == OLD_BINARY
    assert sorted(path.name for path in bin_dir.iterdir()) == [installed.name]


def test_fpcalc_install_replaces_cleanly(tmp_path: Path) -> None:
    """A finished install puts the new bytes under the fpcalc name and nothing else."""
    bin_dir = tmp_path / "bin"
    bootstrap = FpcalcBootstrapService(install_dir=bin_dir)
    install(bootstrap, zip_with_fpcalc(bootstrap, OLD_BINARY, tmp_path))

    installed = install(bootstrap, zip_with_fpcalc(bootstrap, NEW_BINARY, tmp_path))

    assert installed.read_bytes() == NEW_BINARY
    assert sorted(path.name for path in bin_dir.iterdir()) == [installed.name]
    if os.name != "nt":
        assert installed.stat().st_mode & stat.S_IXUSR
