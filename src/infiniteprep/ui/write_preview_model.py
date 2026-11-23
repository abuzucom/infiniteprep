"""Counts, labels, and field diffs for the write preview."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from infiniteprep.domain.ports import FileWritePlan

READY_LABEL: Final[str] = "Ready"
READY_WITH_LOSS_LABEL: Final[str] = "Ready (some tag data will be lost)"
SKIPPED_LABEL: Final[str] = "Skipped"
REASON_SEPARATOR: Final[str] = "; "
BACKUP_NOTE: Final[str] = (
    "Each file is backed up before it changes. You can restore it from History."
)
NO_FILES_BUTTON_LABEL: Final[str] = "Save changes"
# A short preview opens every file row. A longer one stays one line per file to scan.
EXPAND_ROWS_MAX_FILES: Final[int] = 10


def should_expand_rows(file_count: int) -> bool:
    """Return True when a preview of file_count files opens its rows."""
    return 0 < file_count <= EXPAND_ROWS_MAX_FILES


@dataclass(frozen=True)
class PreviewCounts:
    """The summary bar counts of one preview."""

    will_change: int
    removes_tag_data: int
    skipped: int


def count_plans(plans: Sequence[FileWritePlan]) -> PreviewCounts:
    """Count the files that change, the ones that lose tag data, and the skipped ones."""
    safe = [plan for plan in plans if plan.is_safe_to_apply]
    return PreviewCounts(
        will_change=len(safe),
        removes_tag_data=sum(1 for plan in safe if plan.tag_data_warnings),
        skipped=len(plans) - len(safe),
    )


def format_count(count: int, singular: str, plural: str) -> str:
    """Return a count with the noun that fits it, such as "1 file" or "3 files"."""
    return f"{count} {singular if count == 1 else plural}"


def format_summary_badges(counts: PreviewCounts) -> tuple[str, str, str]:
    """Return the texts of the three summary badges."""
    loss_verb = "loses" if counts.removes_tag_data == 1 else "lose"
    return (
        f"{format_count(counts.will_change, 'file', 'files')} will change",
        f"{format_count(counts.removes_tag_data, 'file', 'files')} {loss_verb} some tag data",
        f"{format_count(counts.skipped, 'file', 'files')} skipped",
    )


def format_apply_label(counts: PreviewCounts) -> str:
    """Return the primary button text, which names the number of files it saves."""
    if counts.will_change == 0:
        return NO_FILES_BUTTON_LABEL
    files = format_count(counts.will_change, "file", "files")
    return f"Save changes to {files}"


def plan_status_label(plan: FileWritePlan) -> str:
    """Return the plain status of one file: ready, ready with data loss, or skipped."""
    if not plan.is_safe_to_apply:
        return SKIPPED_LABEL
    if plan.tag_data_warnings:
        return READY_WITH_LOSS_LABEL
    return READY_LABEL


def plan_status_reasons(plan: FileWritePlan) -> str:
    """Return the sentences behind a status: why a file is skipped or loses tag data."""
    if not plan.is_safe_to_apply:
        return REASON_SEPARATOR.join(plan.validation_errors + plan.collision_warnings)
    return REASON_SEPARATOR.join(plan.tag_data_warnings)


def plan_field_diff(
    plan: FileWritePlan, label_for: Callable[[str], str]
) -> list[tuple[str, str, str]]:
    """Return (field label, old value, new value) for each field the write changes.

    A field whose new value matches the stored one is left out. label_for turns a stored
    field key into the plain name the preview shows.
    """
    rows: list[tuple[str, str, str]] = []
    for field_name, new_value in plan.proposed_tags_to_write.items():
        old_value = str(plan.existing_tags.get(field_name, ""))
        if old_value != new_value:
            rows.append((label_for(field_name), old_value, new_value))
    return rows
