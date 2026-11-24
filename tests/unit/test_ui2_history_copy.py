"""Tests for the plain copy of the History dialog (#342 item 12)."""

from __future__ import annotations

from infiniteprep.ui import journal_dialog as journal_module
from infiniteprep.ui.journal_dialog import format_restore_failure


def test_history_list_name_is_plain() -> None:
    """Screen readers name the list of writes History."""
    assert journal_module.HISTORY_LIST_NAME == "History"


def test_restore_failure_keeps_its_reason() -> None:
    """A failed restore says the file is unchanged and keeps the reason."""
    assert format_restore_failure("disk full") == (
        "Couldn't restore this file (disk full). It's unchanged. "
        "Check the backup drive and try again."
    )
