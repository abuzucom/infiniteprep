"""Labels, backup states, and field diffs for the History dialog."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from infiniteprep.domain.enums import OperationStatus
from infiniteprep.domain.models import ChangeRecord

# Plain status words for each journal state.
STATUS_LABELS: Final[dict[OperationStatus, str]] = {
    OperationStatus.PLANNED: "Planned",
    OperationStatus.IN_PROGRESS: "Stopped partway",
    OperationStatus.BACKED_UP: "Stopped partway",
    OperationStatus.TAGS_WRITTEN: "Stopped partway",
    OperationStatus.RENAMED: "Stopped partway",
    OperationStatus.COMPLETED: "Saved",
    OperationStatus.FAILED: "Failed",
    OperationStatus.ROLLED_BACK: "Undone",
    OperationStatus.RESTORED: "Restored",
}
BACKUP_READY_TEXT: Final[str] = (
    "A backup from before this change is ready. Restore puts the file back as it was."
)
BACKUP_USED_TEXT: Final[str] = "This file is back as it was before the change."
BACKUP_PENDING_TEXT: Final[str] = (
    "This change stopped partway. InfinitePrep asks you to restore or keep the file "
    "the next time it starts."
)
BACKUP_FAILED_TEXT: Final[str] = "This change failed. The file kept its earlier tags."
BACKUP_UNKNOWN_TEXT: Final[str] = (
    "InfinitePrep doesn't recognize this change's state, so Restore stays off."
)
NO_SELECTION_TEXT: Final[str] = "Pick a change to see what it did."
RESTORED_STATUSES: Final[frozenset[OperationStatus]] = frozenset(
    {OperationStatus.ROLLED_BACK, OperationStatus.RESTORED}
)


def history_status_label(status: str) -> str:
    """Return the plain word for a stored journal status, or the stored value if unknown."""
    try:
        return STATUS_LABELS[OperationStatus(status)]
    except ValueError:
        return status


def can_restore(record: ChangeRecord) -> bool:
    """Return True when the change finished and its backup can put the file back."""
    return record.operation_status == OperationStatus.COMPLETED


def backup_state_text(record: ChangeRecord) -> str:
    """Return the sentence that says what the backup of one change can do now.

    The repository rejects an unknown status when it loads a record. A record built
    another way still gets a sentence instead of an error.
    """
    try:
        status = OperationStatus(record.operation_status)
    except ValueError:
        return BACKUP_UNKNOWN_TEXT
    if status is OperationStatus.COMPLETED:
        return BACKUP_READY_TEXT
    if status in RESTORED_STATUSES:
        return BACKUP_USED_TEXT
    if status is OperationStatus.FAILED:
        return BACKUP_FAILED_TEXT
    return BACKUP_PENDING_TEXT


def record_field_diff(
    record: ChangeRecord, label_for: Callable[[str], str]
) -> list[tuple[str, str, str]]:
    """Return (field label, value before, value after) for each field the change wrote."""
    return [
        (label_for(field_name), str(record.previous_tags.get(field_name, "")), str(value))
        for field_name, value in record.written_tags.items()
    ]
