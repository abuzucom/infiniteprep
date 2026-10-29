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
    """Manages SQLite connections with WAL mode, foreign keys, and deterministic close."""

    def __init__(self, db_path: Path | str) -> None:
        """Initialize DatabaseManager and execute schema migrations."""
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        self._is_memory = str(db_path) == ":memory:"
        self._lock = threading.RLock()
        self._shared_conn: sqlite3.Connection | None = None
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Return the managed SQLite connection instance, initializing it if needed."""
        with self._lock:
            if self._shared_conn is None:
                target = ":memory:" if self._is_memory else str(self.db_path)
                self._shared_conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
                self._shared_conn.row_factory = sqlite3.Row
                if not self._is_memory:
                    self._shared_conn.execute("PRAGMA journal_mode = WAL;")
                self._shared_conn.execute("PRAGMA foreign_keys = ON;")
            return self._shared_conn

    def initialize(self) -> None:
        """Apply schema.sql idempotently on the managed connection."""
        with self._lock:
            conn = self.connect()
            conn.executescript(get_schema_sql())
            conn.commit()

    def close(self) -> None:
        """Close the managed SQLite connection and release file handles."""
        with self._lock:
            if self._shared_conn is not None:
                self._shared_conn.close()
                self._shared_conn = None

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
