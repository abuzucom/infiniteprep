"""Exercise collection storage through the repository and real SQLite migrations."""

import sqlite3
from contextlib import closing

import pytest

from infiniteprep.db.collections import validate_import_folder_paths
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import m0001_initial
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import ApprovalOrigin, AudioFormat
from infiniteprep.domain.models import ApprovedField, FieldProposal, FileAsset


def add_track(repo, file_id, *, key="", bpm=""):
    """Add a library track with optional sortable metadata."""
    path = f"/music/{file_id}.mp3"
    repo.upsert_file_asset(
        FileAsset(
            file_id,
            path,
            path,
            1,
            file_id,
            AudioFormat.MP3,
            None,
            {"standard_tags": {"key": key, "bpm": bpm}},
            "batch",
            None,
        )
    )


def test_migrate_populated_library(tmp_path):
    library = tmp_path / "library.sqlite3"
    with closing(sqlite3.connect(library)) as conn, conn:
        m0001_initial.apply(conn)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO source_groups (id, user_label, grouping_basis, original_folder) "
            "VALUES ('group', 'Library', 'folder_hypothesis', '/music')"
        )

    with DatabaseManager(library) as db:
        repo = Repository(db)
        assert repo.get_source_group("group").user_label == "Library"
        assert db.connect().execute("PRAGMA user_version").fetchone()[0] == 3
        assert repo.collections.create_playlist("Warmup").name == "Warmup"


def test_membership_rules_and_casefolded_names():
    with DatabaseManager(":memory:") as db:
        repo = Repository(db)
        add_track(repo, "one")
        add_track(repo, "two")
        collections = repo.collections
        folder = collections.create_folder("Sets")
        playlist = collections.create_playlist("Sets")
        crate = collections.create_crate("Sets")
        with pytest.raises(ValueError, match="name"):
            collections.create_playlist("sets")
        assert collections.create_playlist("sets", folder.id).folder_id == folder.id
        with pytest.raises(ValueError, match="repeat"):
            collections.set_playlist_tracks(playlist.id, ["one", "one"])
        collections.set_playlist_repeats(playlist.id, True)
        collections.set_playlist_tracks(playlist.id, ["two", "one", "two"])
        with pytest.raises(ValueError, match="repeat"):
            collections.set_playlist_repeats(playlist.id, False)
        assert collections.playlist_tracks(playlist.id) == ["two", "one", "two"]
        collections.set_crate_tracks(crate.id, ["two", "one", "two"])
        assert collections.crate_tracks(crate.id) == {"one", "two"}


def test_folder_move_guards_and_keeps_items():
    with DatabaseManager(":memory:") as db:
        repo = Repository(db)
        add_track(repo, "one")
        collections = repo.collections
        branch = collections.create_folder("Branch")
        child = collections.create_folder("Child", branch.id)
        playlist = collections.create_playlist("Set", child.id)
        collections.set_playlist_tracks(playlist.id, ["one"])
        with pytest.raises(ValueError, match="descendant"):
            collections.move_folder(branch.id, child.id)
        anchor = collections.create_folder("Level 1")
        for level in range(2, 8):
            anchor = collections.create_folder(f"Level {level}", anchor.id)
        with pytest.raises(ValueError, match="depth"):
            collections.move_folder(branch.id, anchor.id)
        deepest = collections.create_folder("Level 8", anchor.id)
        assert deepest.parent_id == anchor.id
        with pytest.raises(ValueError, match="depth"):
            collections.create_folder("Level 9", deepest.id)
        renamed = collections.rename_folder(branch.id, "Renamed")
        assert renamed.id == branch.id
        collections.move_folder(branch.id, anchor.parent_id)
        assert collections.list_folders(branch.id) == [child]
        assert collections.list_collections(child.id) == [playlist]
        assert collections.playlist_tracks(playlist.id) == ["one"]


def test_preflight_reports_first_over_limit():
    path = [f"Level {level}" for level in range(1, 9)]
    validate_import_folder_paths([path])
    with pytest.raises(ValueError, match="First excess"):
        validate_import_folder_paths([path, [*path, "First excess", "Second excess"]])


def test_sort_uses_the_library_metadata_precedence():
    with DatabaseManager(":memory:") as db:
        repo = Repository(db)
        add_track(repo, "approved", key="1A", bpm="100")
        add_track(repo, "proposed", key="12B", bpm="110")
        add_track(repo, "tagged", key="2A", bpm="120")
        repo.upsert_field_proposal(FieldProposal("p1", "approved", "key", "1A", "test", "1"))
        repo.upsert_field_proposal(FieldProposal("p2", "proposed", "key", "1B", "test", "2"))
        repo.upsert_approved_field(
            ApprovedField("approved", "key", "3A", ApprovalOrigin.MANUAL_EDIT, "")
        )
        playlist = repo.collections.create_playlist("Set")
        repo.collections.set_playlist_tracks(playlist.id, ["approved", "proposed", "tagged"])
        repo.collections.sort_playlist(playlist.id)
        assert repo.collections.playlist_tracks(playlist.id) == ["proposed", "tagged", "approved"]


def test_sort_and_undo_across_restart(tmp_path):
    library = tmp_path / "library.sqlite3"
    previous = ["blank", "fast", "no-bpm", "slow", "fast", "minor"]
    with DatabaseManager(library) as db:
        repo = Repository(db)
        add_track(repo, "fast", key="1B", bpm="125")
        add_track(repo, "minor", key="1A", bpm="140")
        add_track(repo, "slow", key="2A", bpm="100")
        add_track(repo, "blank", bpm="100")
        add_track(repo, "no-bpm", key="1A")
        collections = repo.collections
        playlist = collections.create_playlist("Set", allow_repeats=True)
        collections.set_playlist_tracks(playlist.id, previous)
        collections.sort_playlist(playlist.id)
        assert collections.playlist_tracks(playlist.id) == [
            "minor",
            "fast",
            "fast",
            "slow",
            "blank",
            "no-bpm",
        ]

    with DatabaseManager(library) as db:
        collections = Repository(db).collections
        assert collections.undo_playlist_sort(playlist.id)
        assert collections.playlist_tracks(playlist.id) == previous
        assert not collections.undo_playlist_sort(playlist.id)
        collections.sort_playlist(playlist.id, bpm_first=True)
        assert collections.playlist_tracks(playlist.id) == [
            "slow",
            "fast",
            "fast",
            "minor",
            "blank",
            "no-bpm",
        ]
        collections.set_playlist_tracks(playlist.id, ["fast"])
        assert not collections.undo_playlist_sort(playlist.id)
