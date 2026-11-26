"""Store query text and display notation on smart playlists."""

import sqlite3

VERSION = 5
ROW_COUNT_QUERIES: dict[str, str] = {}


def apply(conn: sqlite3.Connection) -> None:
    """Keep existing collections manual when upgrading a library."""
    conn.execute("ALTER TABLE collections ADD COLUMN search_query TEXT")
    conn.execute("ALTER TABLE collections ADD COLUMN search_notation TEXT")
