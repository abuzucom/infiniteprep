"""SQLite database connection and initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def get_schema_sql() -> str:
    schema_path = Path(__file__).with_name("schema.sql")
    return schema_path.read_text(encoding="utf-8")


class DatabaseManager:
    """Manages SQLite connections with WAL mode and foreign keys enforced."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        self._is_memory = str(db_path) == ":memory:"
        self._shared_conn: sqlite3.Connection | None = None
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        if self._is_memory:
            if self._shared_conn is None:
                self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._shared_conn.row_factory = sqlite3.Row
                self._shared_conn.execute("PRAGMA foreign_keys = ON;")
            return self._shared_conn

        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(get_schema_sql())
            conn.commit()
        finally:
            if not self._is_memory:
                conn.close()

    def close(self) -> None:
        if self._shared_conn is not None:
            self._shared_conn.close()
            self._shared_conn = None
