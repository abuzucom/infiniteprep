"""Write preview and batch execution dialog (Section 9)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.write_service import PendingRecoveryError, WriteService
from infiniteprep.ui.accessible_names import announce
from infiniteprep.ui.detail_panel import field_display_name
from infiniteprep.ui.write_preview_model import (
    BACKUP_NOTE,
    count_plans,
    format_apply_label,
    format_summary_badges,
    plan_field_diff,
    plan_status_label,
    plan_status_reasons,
    should_expand_rows,
)

WRITE_PLAN_DIALOG_WIDTH: Final[int] = 980
WRITE_PLAN_DIALOG_HEIGHT: Final[int] = 560
PLAN_TREE_HEADERS: Final[tuple[str, ...]] = ("File", "Current", "New", "Status")
COLUMN_PLAN_FILE: Final[int] = 0
COLUMN_PLAN_OLD: Final[int] = 1
COLUMN_PLAN_NEW: Final[int] = 2
COLUMN_PLAN_STATUS: Final[int] = 3
RENAME_ARROW: Final[str] = " -> "
NO_SAFE_PLANS_TEXT: Final[str] = (
    "No file is ready for a safe write. Resolve the blocked rows, or close the preview."
)


def format_plan_status(plan: FileWritePlan) -> str:
    """Return the Safety Status cell text, with any tag data loss after READY."""
    if not plan.is_safe_to_apply:
        issues = plan.validation_errors + plan.collision_warnings
        return f"BLOCKED: {'; '.join(issues)}"
    if plan.tag_data_warnings:
        return f"READY WITH DATA LOSS: {'; '.join(plan.tag_data_warnings)}"
    return "READY"


class WritePlanDialog(QDialog):
    """Modal dialog displaying exact tag changes, rename preview, and backup paths."""

    def __init__(
        self,
        write_service: WriteService,
        file_ids: list[str],
        default_template: str = "{artist} - {title} ({mix_name})",
        parent: QWidget | None = None,
        submit_plans: Callable[[list[FileWritePlan]], None] | None = None,
    ) -> None:
        """Initialize WritePlanDialog and generate initial preview plans.

        submit_plans receives the safe plans when the person applies them, for a caller
        that queues the write. Without it, the dialog applies the plans itself.
        """
        super().__init__(parent)
        self.write_service = write_service
        self.file_ids = file_ids
        self.submit_plans = submit_plans
        self.current_plans: list[FileWritePlan] = []

        self.setWindowTitle("Preview & Apply Metadata Writes")
        self.resize(WRITE_PLAN_DIALOG_WIDTH, WRITE_PLAN_DIALOG_HEIGHT)

        self.rename_checkbox = QCheckBox("Rename files using template:", self)
        self.template_input = QLineEdit(default_template, self)
        self.change_badge = QLabel("", self)
        self.loss_badge = QLabel("", self)
        self.skipped_badge = QLabel("", self)
        self.backup_note = QLabel(BACKUP_NOTE, self)
        self.plan_tree = QTreeWidget(self)
        self.status_label = QLabel("", self)
        self.refresh_btn = QPushButton("Refresh Preview", self)
        self.apply_btn = QPushButton("Apply Safe Writes", self)
        self.close_btn = QPushButton("Cancel", self)

        self._build_layout()
        self.refresh_preview()

    def _build_layout(self) -> None:
        """Assemble the dialog layout."""
        root = QVBoxLayout(self)
        top_row = QHBoxLayout()
        top_row.addWidget(self.rename_checkbox)
        top_row.addWidget(self.template_input, stretch=1)
        self.refresh_btn.clicked.connect(self.refresh_preview)
        # Enter activates the default button of a dialog. Refresh holds that role, so
        # Enter in the template field updates the preview and never writes a file.
        self.refresh_btn.setDefault(True)
        self.apply_btn.setAutoDefault(False)
        self.close_btn.setAutoDefault(False)
        top_row.addWidget(self.refresh_btn)
        root.addLayout(top_row)

        summary = QHBoxLayout()
        for badge in (self.change_badge, self.loss_badge, self.skipped_badge):
            summary.addWidget(badge)
        summary.addStretch()
        summary.addWidget(self.backup_note)
        root.addLayout(summary)

        self.plan_tree.setColumnCount(len(PLAN_TREE_HEADERS))
        self.plan_tree.setHeaderLabels(list(PLAN_TREE_HEADERS))
        header = self.plan_tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        # The rows preview computed plans. An edit in a cell changed nothing and was lost.
        self.plan_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plan_tree.setTabKeyNavigation(False)
        self.plan_tree.setAccessibleName("Write plan")
        self.template_input.setAccessibleName("Rename template")
        root.addWidget(self.plan_tree, stretch=1)

        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label, stretch=1)
        bottom.addWidget(self.apply_btn)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)

        self.rename_checkbox.toggled.connect(self.refresh_preview)
        self.apply_btn.clicked.connect(self.execute_safe_writes)
        self.close_btn.clicked.connect(self.reject)

    def refresh_preview(self) -> None:
        """Recompute FileWritePlans and populate the preview table."""
        self.current_plans = self.write_service.build_batch_preview(
            self.file_ids,
            rename_template=self.template_input.text().strip(),
            enable_rename=self.rename_checkbox.isChecked(),
        )
        self.plan_tree.clear()
        for plan in self.current_plans:
            self.plan_tree.addTopLevelItem(self._build_plan_item(plan))
        if should_expand_rows(len(self.current_plans)):
            self.plan_tree.expandAll()
        counts = count_plans(self.current_plans)
        change_text, loss_text, skipped_text = format_summary_badges(counts)
        self.change_badge.setText(change_text)
        self.loss_badge.setText(loss_text)
        self.loss_badge.setVisible(counts.removes_tag_data > 0)
        self.skipped_badge.setText(skipped_text)
        self.skipped_badge.setVisible(counts.skipped > 0)
        self.apply_btn.setText(format_apply_label(counts))
        safe_count = counts.will_change
        self._show_status(
            f"{safe_count} of {len(self.current_plans)} files ready for safe backed-up write."
        )

    @staticmethod
    def _build_plan_item(plan: FileWritePlan) -> QTreeWidgetItem:
        """Return one file row with a child row per changed field."""
        name = plan.source_path.name
        if plan.rename_enabled:
            name = f"{name}{RENAME_ARROW}{plan.destination_path.name}"
        item = QTreeWidgetItem([name, "", "", plan_status_label(plan)])
        reasons = plan_status_reasons(plan)
        if reasons:
            item.setToolTip(COLUMN_PLAN_STATUS, reasons)
        item.setData(COLUMN_PLAN_FILE, Qt.ItemDataRole.UserRole, plan.file_id)
        for label, old_value, new_value in plan_field_diff(plan, field_display_name):
            item.addChild(QTreeWidgetItem([label, old_value, new_value, ""]))
        return item

    def _apply_or_submit(self, safe_plans: list[FileWritePlan]) -> None:
        """Hand the plans to submit_plans, or apply them here without one.

        Either path raises PendingRecoveryError before any file changes.
        """
        if self.submit_plans is None:
            self.write_service.apply_batch(safe_plans)
            return
        self.write_service.ensure_no_pending_recovery()
        self.submit_plans(safe_plans)

    def _show_status(self, text: str) -> None:
        """Show and announce the preview status, so a screen reader hears each change."""
        self.status_label.setText(text)
        announce(self.status_label, text)

    def execute_safe_writes(self) -> None:
        """Apply or submit all safe plans in current_plans and accept the dialog.

        Without a safe plan, the dialog stays open and says why nothing was applied.
        """
        safe_plans = [
            write_plan for write_plan in self.current_plans if write_plan.is_safe_to_apply
        ]
        if not safe_plans:
            self._show_status(NO_SAFE_PLANS_TEXT)
            return
        try:
            self._apply_or_submit(safe_plans)
        except PendingRecoveryError as exc:
            # The dialog stays open, because no file changed.
            QMessageBox.warning(self, "Writes Blocked", str(exc))
            return
        self.accept()
