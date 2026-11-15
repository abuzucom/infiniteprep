"""USB reconnect recovery dialog and journal expiry notice helpers (D166, D191-D198)."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.services.usb.models import DriveIdentity, PartitionInfo
from infiniteprep.services.usb.recovery import (
    ReconnectChoice,
    UsbJournalEntry,
    UsbReconnectMatch,
)


def _format_entry_bullet(entry: UsbJournalEntry) -> str:
    """Format one unresolved journal entry as a plain-text bullet line."""
    return f"* {entry.export_id} ({entry.mount_point}, started {entry.started_at_utc})"


def format_expiry_warning_text(entries: Sequence[UsbJournalEntry]) -> str:
    """Build the 1-day advance expiry warning message for unresolved USB entries (D194)."""
    header = "Automatic cleanup removes these unresolved USB recovery entries after 1 day:"
    lines = [header, *(_format_entry_bullet(entry) for entry in entries)]
    return "\n".join(lines)


def format_overdue_cleanup_text(entries: Sequence[UsbJournalEntry]) -> str:
    """Build the launch-time notice listing expired entries removed automatically (D196)."""
    header = "Automatic cleanup removed these expired USB recovery entries:"
    lines = [header, *(_format_entry_bullet(entry) for entry in entries)]
    return "\n".join(lines)


def _describe_drive_identity(drive: DriveIdentity) -> str:
    """Render untrusted drive vendor, model, and serial attributes as plain text."""
    vendor = drive.vendor or "Unknown vendor"
    model = drive.model or drive.name
    serial = drive.serial or "Unavailable"
    return f"Drive: {vendor} {model} (Serial: {serial})"


def _select_display_partition(
    partitions: Sequence[PartitionInfo], entry: UsbJournalEntry
) -> PartitionInfo | None:
    """Return the most relevant partition record to summarize for the matched drive."""
    for part in partitions:
        if entry.volume_uuid and part.uuid == entry.volume_uuid:
            return part
    for part in partitions:
        if entry.mount_point in part.mount_points:
            return part
    return partitions[0] if partitions else None


def _describe_volume(match: UsbReconnectMatch) -> str:
    """Render matched volume label, UUID, and mount path as plain text."""
    inspection = match.candidate.inspection
    partitions = inspection.partitions if inspection is not None else ()
    partition = _select_display_partition(partitions, match.entry)
    label = partition.label if partition is not None and partition.label else "Unlabeled"
    uuid = (
        partition.uuid
        if partition is not None and partition.uuid
        else match.entry.volume_uuid or "Unavailable"
    )
    mounts = (
        ", ".join(partition.mount_points)
        if partition is not None and partition.mount_points
        else match.entry.mount_point
    )
    return f"Volume: {label} (UUID: {uuid}, Mount: {mounts})"


def _describe_scope(entry: UsbJournalEntry) -> str:
    """Summarize the recorded export scope and playlist context."""
    playlists = ", ".join(entry.playlist_names) if entry.playlist_names else "All tracks"
    return (
        f"Export {entry.export_id} started {entry.started_at_utc} | "
        f"Scope: {entry.scope.value} ({playlists})"
    )


class UsbRecoveryDialog(QDialog):
    """Modal prompt shown when an interrupted USB export drive reconnects (D166)."""

    def __init__(self, match: UsbReconnectMatch, parent: QWidget | None = None) -> None:
        """Initialize reconnect controls, confirmation gating, and plain-text labels."""
        super().__init__(parent)
        self.setWindowTitle("Interrupted USB Export Detected")
        self.match = match
        self.selected_choice: ReconnectChoice | None = None

        layout = QVBoxLayout(self)
        self._populate_summary_labels(layout, match)
        self.confirm_checkbox = self._build_confirmation_checkbox(layout, match)
        self._build_action_buttons(layout)
        self._apply_confirmation_state()

    def _add_plain_label(self, layout: QVBoxLayout, text: str) -> QLabel:
        """Create a plain-text wrapped QLabel and append it to layout."""
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def _populate_summary_labels(
        self, layout: QVBoxLayout, match: UsbReconnectMatch
    ) -> None:
        """Add plain-text drive, volume, export, and match-reason labels."""
        inspection = match.candidate.inspection
        drive = (
            inspection.drive
            if inspection is not None
            else match.entry.drive_identity
        )
        self._add_plain_label(layout, _describe_scope(match.entry))
        self._add_plain_label(layout, _describe_drive_identity(drive))
        self._add_plain_label(layout, _describe_volume(match))
        self._add_plain_label(layout, f"Match status: {match.reason}")

    def _build_confirmation_checkbox(
        self, layout: QVBoxLayout, match: UsbReconnectMatch
    ) -> QCheckBox | None:
        """Create an explicit confirmation checkbox when match identity changed."""
        if not match.requires_confirmation:
            return None
        checkbox = QCheckBox(
            "Confirm this is the intended USB drive before modifying it",
            self,
        )
        checkbox.toggled.connect(self._apply_confirmation_state)
        layout.addWidget(checkbox)
        return checkbox

    def _build_action_buttons(self, layout: QVBoxLayout) -> None:
        """Create Resume, Restore, and Leave Unchanged dialog action buttons."""
        row = QHBoxLayout()
        self.resume_btn = QPushButton("Resume Export", self)
        self.resume_btn.clicked.connect(lambda: self._select(ReconnectChoice.RESUME))
        row.addWidget(self.resume_btn)

        self.restore_btn = QPushButton("Restore Previous State", self)
        self.restore_btn.clicked.connect(lambda: self._select(ReconnectChoice.RESTORE))
        row.addWidget(self.restore_btn)

        self.leave_unchanged_btn = QPushButton("Leave Unchanged", self)
        self.leave_unchanged_btn.clicked.connect(
            lambda: self._select(ReconnectChoice.LEAVE_UNCHANGED)
        )
        row.addWidget(self.leave_unchanged_btn)
        layout.addLayout(row)

    def _apply_confirmation_state(self) -> None:
        """Enable Resume and Restore once confirmation passes."""
        allowed = self.confirm_checkbox is None or self.confirm_checkbox.isChecked()
        self.resume_btn.setEnabled(allowed)
        self.restore_btn.setEnabled(allowed)
        self.leave_unchanged_btn.setEnabled(True)

    def _select(self, choice: ReconnectChoice) -> None:
        """Record the selected recovery choice and accept the dialog."""
        self.selected_choice = choice
        self.accept()
