"""Behavioral tests for the library import merge and its one-transaction commit (D333)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    ApprovalOrigin,
    AudioFormat,
    ProposalReviewStatus,
    ReviewStatus,
    TaskStatus,
)
from infiniteprep.domain.models import ApprovedField, FileAsset
from infiniteprep.services.library_import.merge import (
    LIBRARY_PROVIDER,
    MergeContext,
    merge_library,
)
from infiniteprep.services.library_import.models import (
    ImportedPendingValue,
    ImportedTrack,
    ImportedValue,
    SourceLibrary,
)
from infiniteprep.services.library_import.plan import ImportPlan, build_source_group

SOURCE_PATH = "/music/collection.nml"
TRACK_PATH = "/music/track.wav"


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    """Return a repository on a file database."""
    return Repository(DatabaseManager(tmp_path / "library.sqlite3"))


def make_library(tracks: tuple[ImportedTrack, ...]) -> SourceLibrary:
    """Return a source library holding the given tracks."""
    return SourceLibrary(
        source_format="traktor",
        source_path=SOURCE_PATH,
        version="3.5.1",
        tracks=tracks,
    )


def test_merge_stores_a_new_value_as_an_approved_field() -> None:
    """A value with no stored counterpart becomes an approved field."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "Song"),)),)
    )
    outcome = merge_library(MergeContext(library=library, file_ids={TRACK_PATH: "file-1"}))

    assert len(outcome.approved_fields) == 1
    approved = outcome.approved_fields[0]
    assert approved.file_id == "file-1"
    assert approved.field_name == "title"
    assert approved.approved_value == "Song"
    assert outcome.proposals == []
    assert outcome.conflicts == []


def test_merge_reports_a_conflict_and_keeps_the_stored_value() -> None:
    """A value that differs from the stored one becomes a proposal and a conflict."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "New"),)),)
    )
    context = MergeContext(
        library=library,
        file_ids={TRACK_PATH: "file-1"},
        stored_values={TRACK_PATH: {"title": "Old"}},
    )
    outcome = merge_library(context)

    assert outcome.approved_fields == []
    assert len(outcome.proposals) == 1
    assert outcome.proposals[0].proposed_value == "New"
    assert outcome.proposals[0].review_status is ProposalReviewStatus.PENDING
    assert outcome.proposals[0].provider == LIBRARY_PROVIDER
    assert len(outcome.conflicts) == 1
    assert outcome.conflicts[0].stored_value == "Old"
    assert outcome.conflicts[0].imported_value == "New"


def test_merge_skips_a_value_that_matches_the_stored_one() -> None:
    """A value equal to the stored one raises neither a proposal nor a conflict."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "Same"),)),)
    )
    context = MergeContext(
        library=library,
        file_ids={TRACK_PATH: "file-1"},
        stored_values={TRACK_PATH: {"title": "Same"}},
    )
    outcome = merge_library(context)

    assert outcome.approved_fields == []
    assert outcome.proposals == []
    assert outcome.conflicts == []


def test_merge_holds_an_unsupported_field_as_pending() -> None:
    """A field with no storage yet stays pending instead of being approved."""
    library = make_library(
        (
            ImportedTrack(
                source_path=TRACK_PATH,
                values=(
                    ImportedValue("title", "Song"),
                    ImportedValue("energy", "9"),
                ),
            ),
        )
    )
    outcome = merge_library(MergeContext(library=library, file_ids={TRACK_PATH: "file-1"}))

    approved_names = [item.field_name for item in outcome.approved_fields]
    assert approved_names == ["title"]
    assert outcome.tracks[0].pending_fields == ["energy"]
    assert outcome.proposals == []


def test_merge_holds_a_value_without_a_file_as_pending() -> None:
    """A track with no file row keeps its values pending."""
    library = make_library(
        (
            ImportedTrack(
                source_path=TRACK_PATH,
                values=(ImportedValue("title", "Song"),),
                pending=(ImportedPendingValue("cues", {"count": 2}),),
            ),
        )
    )
    outcome = merge_library(MergeContext(library=library))

    assert outcome.approved_fields == []
    assert outcome.tracks[0].pending_fields == ["title", "cues"]


def test_commit_writes_every_pending_change_in_one_transaction(
    repository: Repository,
) -> None:
    """The commit stores the library row, the file row, and the approved value."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "Song"),)),)
    )
    outcome = merge_library(MergeContext(library=library, file_ids={TRACK_PATH: "file-1"}))
    plan = ImportPlan(library=library)
    plan.apply_merge(outcome)
    plan.assets.append(_asset(plan.library_id, TRACK_PATH))
    plan.source_groups.append(build_source_group(library, "/music", plan.source_group_id))

    plan.commit(repository)

    stored = repository.get_file_by_path(TRACK_PATH)
    assert stored is not None
    assert stored.review_status is ReviewStatus.PROPOSED
    approved = repository.get_approved_field(stored.id, "title")
    assert approved is not None
    assert approved.approved_value == "Song"


def test_commit_keeps_a_value_that_changed_after_the_merge(
    repository: Repository,
) -> None:
    """A value another process changed while the import waited survives the commit."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "Song"),)),)
    )
    asset = _asset(plan_id := "library-1", TRACK_PATH)
    repository.upsert_file_asset(asset)
    stored = _approved_field(asset.id, "title", "Old title")
    repository.upsert_approved_field(stored)
    outcome = merge_library(
        MergeContext(
            library=library,
            file_ids={TRACK_PATH: asset.id},
            stored_values={TRACK_PATH: {"title": "Old title"}},
        )
    )
    plan = ImportPlan(library=library, library_id=plan_id)
    plan.apply_merge(outcome)
    plan.assets.append(asset)
    concurrent = _approved_field(asset.id, "title", "Newer title")
    repository.upsert_approved_field(concurrent)

    plan.commit(repository)

    approved = repository.get_approved_field(asset.id, "title")
    assert approved is not None
    assert approved.approved_value == "Newer title"
    proposals = repository.list_field_proposals(asset.id, "title")
    assert [item.proposed_value for item in proposals] == ["Song"]


def test_commit_leaves_the_database_empty_when_the_plan_is_cancelled(
    repository: Repository,
) -> None:
    """A cancelled plan writes nothing, so the database holds no trace of it."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "Song"),)),)
    )
    outcome = merge_library(MergeContext(library=library, file_ids={TRACK_PATH: "file-1"}))
    plan = ImportPlan(library=library)
    plan.apply_merge(outcome)
    plan.assets.append(_asset(plan.library_id, TRACK_PATH))

    assert repository.list_files() == []
    assert repository.approved_fields_by_file() == {}


def test_commit_rolls_back_when_one_write_fails(repository: Repository) -> None:
    """A failure inside the commit leaves the database without the change."""
    library = make_library(
        (ImportedTrack(source_path=TRACK_PATH, values=(ImportedValue("title", "Song"),)),)
    )
    outcome = merge_library(MergeContext(library=library, file_ids={TRACK_PATH: "file-1"}))
    plan = ImportPlan(library=library)
    plan.apply_merge(outcome)
    plan.assets.append(_asset(plan.library_id, TRACK_PATH))
    plan.approved_fields.append(_approved_field("missing-file", "title", "Song"))

    with pytest.raises(sqlite3.IntegrityError):
        plan.commit(repository)

    assert repository.list_files() == []
    assert repository.approved_fields_by_file() == {}


def _asset(library_id: str, path: str) -> FileAsset:
    """Return a file row for one track."""
    return FileAsset(
        id="file-1",
        current_path=path,
        original_path=path,
        file_size=10,
        content_hash="hash-1",
        format=AudioFormat.WAV,
        duration=1.0,
        original_tags={},
        import_batch_id=library_id,
        source_group_id=None,
        task_status=TaskStatus.IDLE,
    )


def _approved_field(file_id: str, field_name: str, value: str) -> ApprovedField:
    """Return an approved field for one value."""
    return ApprovedField(
        file_id=file_id,
        field_name=field_name,
        approved_value=value,
        approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
        approved_at="",
    )
