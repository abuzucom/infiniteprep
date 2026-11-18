"""SQLite database connection and initialization."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from types import TracebackType

from infiniteprep.db.migrations.m0001_initial import read_schema_sql
from infiniteprep.db.migrations.runner import migrate_database
from infiniteprep.private_files import (
    create_private_dirs,
    ensure_private_file,
    refuse_symlink,
    warn_if_folder_is_shared,
)

# Seconds a connection waits for another connection's write lock before it fails.
BUSY_TIMEOUT_SECONDS = 30.0


def get_schema_sql() -> str:
    """Load the SQLite schema DDL from schema.sql."""
    return read_schema_sql()


class DatabaseManager:
    """Manages thread-local SQLite connections with WAL mode, foreign keys, and cleanup."""

    def __init__(self, db_path: Path | str) -> None:
        """Initialize DatabaseManager and execute schema migrations."""
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        self._is_memory = str(db_path) == ":memory:"
        self._local = threading.local()
        # A unique name keeps each in-memory database private. id(self) can repeat after
        # garbage collection while an old connection still holds that shared-cache database.
        self._memory_name = f"infiniteprep_{uuid.uuid4().hex}"
        if not self._is_memory:
            create_private_dirs(self.db_path.parent)
            # The link check below and the SQLite open use the name separately. Only a folder
            # that other accounts cannot change closes that gap. A shared folder logs a warning.
            warn_if_folder_is_shared(self.db_path.parent)
            # SQLite follows a link at the library name. A link is refused before any open.
            refuse_symlink(self.db_path)
            # SQLite gives the -wal and -shm files the mode of the library file.
            ensure_private_file(self.db_path)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection instance for the calling thread."""
        if getattr(self._local, "conn", None) is None:
            if self._is_memory:
                uri = f"file:{self._memory_name}?mode=memory&cache=shared"
                self._local.conn = sqlite3.connect(uri, timeout=BUSY_TIMEOUT_SECONDS, uri=True)
            else:
                self._local.conn = sqlite3.connect(str(self.db_path), timeout=BUSY_TIMEOUT_SECONDS)
            try:
                self._local.conn.row_factory = sqlite3.Row
                if not self._is_memory:
                    self._local.conn.execute("PRAGMA journal_mode = WAL;")
                self._local.conn.execute("PRAGMA foreign_keys = ON;")
            except sqlite3.Error:
                # A file that is not a database fails here. The handle closes now, so a
                # restore can replace the file while the error dialog shows.
                self.close()
                raise
        return self._local.conn

    def initialize(self) -> None:
        """Bring the library to the latest schema version on this thread's connection (D349).

        A refused or failed open closes the connection. A restore can then rename the file.
        """
        conn = self.connect()
        try:
            migrate_database(conn, None if self._is_memory else self.db_path)
        except (sqlite3.Error, OSError):
            self.close()
            raise

    def close(self) -> None:
        """Close the current thread's SQLite connection and release file handles."""
        if getattr(self._local, "conn", None) is not None:
            self._local.conn.close()
            self._local.conn = None

    def __enter__(self) -> DatabaseManager:
        """Support context-manager entry for deterministic connection release."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the managed connection when exiting a context-manager block."""
        _ = (exc_type, exc_val, exc_tb)
        self.close()
