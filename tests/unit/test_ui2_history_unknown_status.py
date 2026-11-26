"""Tests for a History record whose stored status this version does not know."""

from __future__ import annotations

from typing import cast

from infiniteprep.domain.enums import OperationStatus
from infiniteprep.domain.models import ChangeRecord
from infiniteprep.ui.history_model import (
    BACKUP_UNKNOWN_TEXT,
    backup_state_text,
    can_restore,
    history_status_label,
)

UNKNOWN_STATUS = "from_a_newer_version"


def make_record(status: str) -> ChangeRecord:
    """Return a journal record with the given stored status text."""
    return ChangeRecord(
        operation_id="op-1",
        file_id="file-1",
        previous_path="/music/a.wav",
        new_path="/music/a.wav",
        previous_tags={"artist": "Old"},
        written_tags={"artist": "New"},
        backup_reference="/backups/a.wav",
        operation_status=cast(OperationStatus, status),
        timestamp="2026-11-23T12:00:00+00:00",
    )


def test_unknown_status_plain_blocks_restore() -> None:
    """An unknown status shows as stored, says the state is unknown, and keeps Restore off."""
    record = make_record(UNKNOWN_STATUS)

    assert history_status_label(UNKNOWN_STATUS) == UNKNOWN_STATUS
    assert backup_state_text(record) == BACKUP_UNKNOWN_TEXT
    assert not can_restore(record)
