"""Manage named collections and folder relationships in the library database."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from infiniteprep.domain.enums import KeyDisplayNotation
from infiniteprep.domain.library_query import parse_library_query
from infiniteprep.domain.models import CollectionFolder, CollectionKind, TrackCollection
from infiniteprep.domain.playlist_order import order_tracks
from infiniteprep.ui.library_rows import search_playlist

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from infiniteprep.db.repositories import Repository

MAX_FOLDER_DEPTH: Final[int] = 8
MISSING_SAVED_SEARCH_TEXT: Final[str] = "That saved search no longer exists. Choose another one."


@dataclass(frozen=True)
class SavedSearch:
    """Hold the collection ID, the name, and the original query of one saved search."""

    id: str
    name: str
    query: str


def validate_import_folder_paths(paths: Iterable[Sequence[str]]) -> None:
    """Preflight source paths before an importer saves any collections."""
    for path in paths:
        if len(path) > MAX_FOLDER_DEPTH:
            folder = path[MAX_FOLDER_DEPTH]
            raise ValueError(
                f"Imported folder exceeds the {MAX_FOLDER_DEPTH}-level depth limit: {folder!r}."
            )


def _normalize_name(name: str) -> str:
    name = name.strip()
    if not name or "\x00" in name:
        raise ValueError("Enter a collection or folder name without null characters.")
    return name


def _folder_from_row(row: sqlite3.Row) -> CollectionFolder:
    return CollectionFolder(row["id"], row["name"], row["parent_id"])


def _collection_from_row(row: sqlite3.Row) -> TrackCollection:
    return TrackCollection(
        row["id"],
        row["name"],
        CollectionKind(row["kind"]),
        row["folder_id"],
        bool(row["allow_repeats"]),
    )


def _write_named_record(conn: sqlite3.Connection, sql: str, values: tuple) -> None:
    try:
        conn.execute(sql, values)
    except sqlite3.IntegrityError as error:
        if error.sqlite_errorcode != sqlite3.SQLITE_CONSTRAINT_UNIQUE:
            raise
        raise ValueError(
            "Choose a different name. This folder already contains that name for this kind."
        ) from error


class CollectionRepository:
    """Persist folder changes, membership changes, and sort operations atomically."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create_folder(self, name: str, parent_id: str | None = None) -> CollectionFolder:
        """Create one folder under an existing parent or at the library root."""
        folder = CollectionFolder(uuid4().hex, _normalize_name(name), parent_id)
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            if self._folder_depth(parent_id) >= MAX_FOLDER_DEPTH:
                raise ValueError(
                    f"Folder depth exceeds {MAX_FOLDER_DEPTH}. Choose a shallower parent."
                )
            _write_named_record(
                conn,
                "INSERT INTO collection_folders (id, name, name_key, parent_id) "
                "VALUES (?, ?, ?, ?)",
                (folder.id, folder.name, folder.name.casefold(), parent_id),
            )
        return folder

    def list_folders(self, parent_id: str | None = None) -> list[CollectionFolder]:
        """List direct child folders in name order."""
        rows = self._repository.db.connect().execute(
            "SELECT * FROM collection_folders WHERE parent_id IS ? ORDER BY name_key", (parent_id,)
        )
        return [_folder_from_row(row) for row in rows]

    def rename_folder(self, folder_id: str, name: str) -> CollectionFolder:
        """Rename a folder without changing its identity or children."""
        name = _normalize_name(name)
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            folder = self._get_folder(folder_id)
            _write_named_record(
                conn,
                "UPDATE collection_folders SET name = ?, name_key = ? WHERE id = ?",
                (name, name.casefold(), folder_id),
            )
        return CollectionFolder(folder_id, name, folder.parent_id)

    def move_folder(self, folder_id: str, parent_id: str | None) -> CollectionFolder:
        """Move a folder and its descendants within the depth limit."""
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            folder = self._get_folder(folder_id)
            depth = self._folder_depth(parent_id, moving_id=folder_id)
            if depth + self._subtree_depth(folder_id) > MAX_FOLDER_DEPTH:
                raise ValueError(
                    f"Folder depth exceeds {MAX_FOLDER_DEPTH}. Choose a shallower parent."
                )
            _write_named_record(
                conn,
                "UPDATE collection_folders SET parent_id = ? WHERE id = ?",
                (parent_id, folder_id),
            )
        return CollectionFolder(folder_id, folder.name, parent_id)

    def _get_folder(self, folder_id: str) -> CollectionFolder:
        row = (
            self._repository.db.connect()
            .execute("SELECT * FROM collection_folders WHERE id = ?", (folder_id,))
            .fetchone()
        )
        if row is None:
            raise ValueError("Folder does not exist. Choose an existing folder.")
        return _folder_from_row(row)

    def _folder_depth(self, folder_id: str | None, *, moving_id: str | None = None) -> int:
        for depth in range(MAX_FOLDER_DEPTH + 1):
            if folder_id is None:
                return depth
            if folder_id == moving_id:
                raise ValueError("Choose a parent outside the folder and its descendants.")
            folder_id = self._get_folder(folder_id).parent_id
        raise ValueError("The existing folder tree exceeds the depth limit. Choose another parent.")

    def _subtree_depth(self, folder_id: str) -> int:
        row = (
            self._repository.db.connect()
            .execute(
                """WITH RECURSIVE subtree(id, depth) AS (
                SELECT ?, 1
                UNION ALL
                SELECT folder.id, subtree.depth + 1 FROM subtree
                JOIN collection_folders AS folder ON folder.parent_id = subtree.id
                WHERE subtree.depth < ?
            ) SELECT MAX(depth) FROM subtree""",
                (folder_id, MAX_FOLDER_DEPTH),
            )
            .fetchone()
        )
        return int(row[0])

    def create_playlist(
        self, name: str, folder_id: str | None = None, *, allow_repeats: bool = False
    ) -> TrackCollection:
        """Create an empty ordered playlist with repeats off by default."""
        return self._create_collection(name, CollectionKind.PLAYLIST, folder_id, allow_repeats)

    def save_search(
        self, name: str, query: str, notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT
    ) -> TrackCollection:
        """Save a valid search as a named root playlist in one transaction."""
        if not query.strip():
            raise ValueError("Enter a search before saving.")
        parse_library_query(query)
        name = _normalize_name(name)
        playlist = TrackCollection(uuid4().hex, name, CollectionKind.PLAYLIST)
        with self._repository.batch(immediate=True):
            _write_named_record(
                self._repository.db.connect(),
                "INSERT INTO collections (id, name, name_key, kind, allow_repeats, "
                "search_query, search_notation) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (playlist.id, name, name.casefold(), playlist.kind, query, notation.value),
            )
        return playlist

    def saved_searches(self) -> list[tuple[str, str]]:
        """List saved search names and original queries in library order."""
        rows = self._repository.db.connect().execute(
            "SELECT name, search_query FROM collections "
            "WHERE search_query IS NOT NULL ORDER BY name_key"
        )
        return [(row["name"], row["search_query"]) for row in rows]

    def saved_search_entries(self) -> list[SavedSearch]:
        """List each saved search with its ID in library order."""
        rows = self._repository.db.connect().execute(
            "SELECT id, name, search_query FROM collections "
            "WHERE search_query IS NOT NULL ORDER BY name_key"
        )
        return [SavedSearch(row["id"], row["name"], row["search_query"]) for row in rows]

    def rename_saved_search(self, search_id: str, name: str) -> None:
        """Rename a saved search and keep its query."""
        name = _normalize_name(name)
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            self._require_saved_search(search_id)
            _write_named_record(
                conn,
                "UPDATE collections SET name = ?, name_key = ? WHERE id = ?",
                (name, name.casefold(), search_id),
            )

    def delete_saved_search(self, search_id: str) -> None:
        """Delete a saved search. An ordinary playlist or crate stays in place."""
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            self._require_saved_search(search_id)
            conn.execute(
                "DELETE FROM collections WHERE id = ? AND search_query IS NOT NULL", (search_id,)
            )

    def _require_saved_search(self, search_id: str) -> None:
        row = (
            self._repository.db.connect()
            .execute(
                "SELECT 1 FROM collections WHERE id = ? AND search_query IS NOT NULL",
                (search_id,),
            )
            .fetchone()
        )
        if row is None:
            raise ValueError(MISSING_SAVED_SEARCH_TEXT)

    def create_crate(self, name: str, folder_id: str | None = None) -> TrackCollection:
        """Create an empty unordered crate."""
        return self._create_collection(name, CollectionKind.CRATE, folder_id, False)

    def _create_collection(
        self, name: str, kind: CollectionKind, folder_id: str | None, allow_repeats: bool
    ) -> TrackCollection:
        collection = TrackCollection(
            uuid4().hex, _normalize_name(name), kind, folder_id, allow_repeats
        )
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            if folder_id is not None:
                self._get_folder(folder_id)
            _write_named_record(
                conn,
                "INSERT INTO collections (id, name, name_key, kind, folder_id, allow_repeats) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    collection.id,
                    collection.name,
                    collection.name.casefold(),
                    kind,
                    folder_id,
                    allow_repeats,
                ),
            )
        return collection

    def list_collections(self, folder_id: str | None = None) -> list[TrackCollection]:
        """List direct child collections in name order."""
        rows = self._repository.db.connect().execute(
            "SELECT * FROM collections WHERE folder_id IS ? ORDER BY name_key, kind", (folder_id,)
        )
        return [_collection_from_row(row) for row in rows]

    def _require_kind(self, collection_id: str, kind: CollectionKind) -> sqlite3.Row:
        row = (
            self._repository.db.connect()
            .execute("SELECT * FROM collections WHERE id = ? AND kind = ?", (collection_id, kind))
            .fetchone()
        )
        if row is None:
            raise ValueError(f"Choose an existing {kind}.")
        return row

    def playlist_tracks(self, playlist_id: str) -> list[str]:
        """Return file IDs in saved order, including repeated occurrences."""
        row = self._require_kind(playlist_id, CollectionKind.PLAYLIST)
        if row["search_query"] is not None:
            query = parse_library_query(row["search_query"])
            return search_playlist(
                self._repository, query, KeyDisplayNotation(row["search_notation"])
            )
        return self._read_playlist_tracks(playlist_id)

    def _require_manual_playlist(self, playlist_id: str) -> sqlite3.Row:
        row = self._require_kind(playlist_id, CollectionKind.PLAYLIST)
        if row["search_query"] is not None:
            raise ValueError("This playlist follows a saved search. Choose a manual playlist.")
        return row

    def _read_playlist_tracks(self, playlist_id: str) -> list[str]:
        rows = self._repository.db.connect().execute(
            "SELECT file_id FROM playlist_tracks WHERE collection_id = ? ORDER BY position",
            (playlist_id,),
        )
        return [row["file_id"] for row in rows]

    def _write_playlist_tracks(self, playlist_id: str, file_ids: Sequence[str]) -> None:
        conn = self._repository.db.connect()
        conn.execute("DELETE FROM playlist_tracks WHERE collection_id = ?", (playlist_id,))
        conn.executemany(
            "INSERT INTO playlist_tracks (collection_id, position, file_id) VALUES (?, ?, ?)",
            ((playlist_id, position, file_id) for position, file_id in enumerate(file_ids)),
        )

    def set_playlist_tracks(self, playlist_id: str, file_ids: Sequence[str]) -> None:
        """Save playlist membership and manual order. Clear stale sort undo."""
        tracks = tuple(file_ids)
        with self._repository.batch(immediate=True):
            row = self._require_manual_playlist(playlist_id)
            if not row["allow_repeats"] and len(set(tracks)) != len(tracks):
                raise ValueError("Remove repeated tracks or enable repeats for this playlist.")
            self._write_playlist_tracks(playlist_id, tracks)
            self._write_sort_undo(playlist_id, None)

    def set_playlist_repeats(self, playlist_id: str, allow_repeats: bool) -> None:
        """Change the repeat setting after verifying it fits the saved membership."""
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            self._require_manual_playlist(playlist_id)
            tracks = self._read_playlist_tracks(playlist_id)
            if not allow_repeats and len(set(tracks)) != len(tracks):
                raise ValueError("Remove repeated tracks before turning repeats off.")
            conn.execute(
                "UPDATE collections SET allow_repeats = ? WHERE id = ?",
                (allow_repeats, playlist_id),
            )

    def crate_tracks(self, crate_id: str) -> set[str]:
        """Return unique file IDs in a crate."""
        self._require_kind(crate_id, CollectionKind.CRATE)
        rows = self._repository.db.connect().execute(
            "SELECT file_id FROM crate_tracks WHERE collection_id = ?", (crate_id,)
        )
        return {row["file_id"] for row in rows}

    def set_crate_tracks(self, crate_id: str, file_ids: Iterable[str]) -> None:
        """Save crate membership with one occurrence per file ID."""
        tracks = set(file_ids)
        conn = self._repository.db.connect()
        with self._repository.batch(immediate=True):
            self._require_kind(crate_id, CollectionKind.CRATE)
            conn.execute("DELETE FROM crate_tracks WHERE collection_id = ?", (crate_id,))
            conn.executemany(
                "INSERT INTO crate_tracks (collection_id, file_id) VALUES (?, ?)",
                ((crate_id, file_id) for file_id in tracks),
            )

    def sort_playlist(self, playlist_id: str, *, bpm_first: bool = False) -> None:
        """Save a stable metadata order and its one-step undo snapshot."""
        with self._repository.batch(immediate=True):
            self._require_manual_playlist(playlist_id)
            previous = self._read_playlist_tracks(playlist_id)
            ordered = order_tracks(self._repository, previous, bpm_first=bpm_first)
            if ordered != previous:
                self._write_playlist_tracks(playlist_id, ordered)
                self._write_sort_undo(playlist_id, json.dumps(previous))

    def undo_playlist_sort(self, playlist_id: str) -> bool:
        """Restore the prior order once if it still describes current membership."""
        with self._repository.batch(immediate=True):
            row = self._require_manual_playlist(playlist_id)
            snapshot = row["sort_undo"]
            if snapshot is None:
                return False
            self._write_sort_undo(playlist_id, None)
            previous = json.loads(snapshot)
            if Counter(previous) != Counter(self._read_playlist_tracks(playlist_id)):
                return False
            self._write_playlist_tracks(playlist_id, previous)
            return True

    def _write_sort_undo(self, playlist_id: str, snapshot: str | None) -> None:
        self._repository.db.connect().execute(
            "UPDATE collections SET sort_undo = ? WHERE id = ?", (snapshot, playlist_id)
        )
