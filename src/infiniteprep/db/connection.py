"""SQLite database connection and initialization."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import TracebackType


def get_schema_sql() -> str:
    """Load the SQLite schema DDL from schema.sql."""
    schema_path = Path(__file__).with_name("schema.sql")
    return schema_path.read_text(encoding="utf-8")


class DatabaseManager:
    """Manages thread-local SQLite connections with WAL mode, foreign keys, and cleanup."""

    def __init__(self, db_path: Path | str) -> None:
        """Initialize DatabaseManager and execute schema migrations."""
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        self._is_memory = str(db_path) == ":memory:"
        self._local = threading.local()
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection instance for the calling thread."""
        if getattr(self._local, "conn", None) is None:
            if self._is_memory:
                uri = f"file:infiniteprep_{id(self)}?mode=memory&cache=shared"
                self._local.conn = sqlite3.connect(uri, timeout=30.0, uri=True)
            else:
                self._local.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            self._local.conn.row_factory = sqlite3.Row
            if not self._is_memory:
                self._local.conn.execute("PRAGMA journal_mode = WAL;")
            self._local.conn.execute("PRAGMA foreign_keys = ON;")
        return self._local.conn

    def initialize(self) -> None:
        """Apply schema.sql idempotently on the current thread's connection."""
        conn = self.connect()
        conn.executescript(get_schema_sql())
        conn.commit()

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
