"""Create storage for collection folders, playlists, crates, and sort undo."""

import sqlite3
from typing import Final

VERSION: Final[int] = 2
ROW_COUNT_QUERIES: Final[dict[str, str]] = {
    "collection_folders": "SELECT COUNT(*) FROM collection_folders",
    "collections": "SELECT COUNT(*) FROM collections",
    "playlist_tracks": "SELECT COUNT(*) FROM playlist_tracks",
    "crate_tracks": "SELECT COUNT(*) FROM crate_tracks",
}

STATEMENTS: Final = (
    """CREATE TABLE collection_folders (
        id TEXT PRIMARY KEY NOT NULL,
        parent_id TEXT REFERENCES collection_folders(id),
        name TEXT NOT NULL,
        name_key TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX collection_folder_name
        ON collection_folders(COALESCE(parent_id, ''), name_key)""",
    "CREATE INDEX collection_folder_parent ON collection_folders(parent_id)",
    """CREATE TABLE collections (
        id TEXT PRIMARY KEY NOT NULL,
        folder_id TEXT REFERENCES collection_folders(id),
        name TEXT NOT NULL,
        name_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('playlist', 'crate')),
        allow_repeats INTEGER NOT NULL DEFAULT 0 CHECK (allow_repeats IN (0, 1)),
        sort_undo TEXT
    )""",
    """CREATE UNIQUE INDEX collection_name
        ON collections(COALESCE(folder_id, ''), kind, name_key)""",
    "CREATE INDEX collection_folder ON collections(folder_id)",
    """CREATE TABLE playlist_tracks (
        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK (position >= 0),
        file_id TEXT NOT NULL REFERENCES file_assets(id) ON DELETE CASCADE,
        PRIMARY KEY (collection_id, position)
    )""",
    "CREATE INDEX playlist_tracks_file ON playlist_tracks(file_id)",
    """CREATE TABLE crate_tracks (
        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        file_id TEXT NOT NULL REFERENCES file_assets(id) ON DELETE CASCADE,
        PRIMARY KEY (collection_id, file_id)
    )""",
    "CREATE INDEX crate_tracks_file ON crate_tracks(file_id)",
)


def apply(conn: sqlite3.Connection) -> None:
    """Run the collection DDL inside the migration runner's transaction."""
    for statement in STATEMENTS:
        conn.execute(statement)
