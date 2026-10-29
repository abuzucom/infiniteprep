"""Write preview and batch execution dialog (Section 9)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.write_service import WriteService


class WritePlanDialog(QDialog):
    """Modal dialog displaying exact tag changes, rename preview, and backup paths."""

    def __init__(
        self,
        write_service: WriteService,
        file_ids: list[str],
        default_template: str = "{artist} - {title} ({mix_name})",
        parent: QWidget | None = None,
    ) -> None:
        """Initialize WritePlanDialog and generate initial preview plans."""
        super().__init__(parent)
        self.write_service = write_service
        self.file_ids = file_ids
        self.current_plans: list[FileWritePlan] = []

        self.setWindowTitle("Preview & Apply Metadata Writes")
        self.resize(980, 560)

        self.rename_checkbox = QCheckBox("Rename files using template:", self)
        self.template_input = QLineEdit(default_template, self)
        self.table = QTableWidget(0, 6, self)
        self.status_label = QLabel("", self)
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
        refresh_btn = QPushButton("Refresh Preview", self)
        refresh_btn.clicked.connect(self.refresh_preview)
        top_row.addWidget(refresh_btn)
        root.addLayout(top_row)

        self.table.setHorizontalHeaderLabels(
            [
                "Current File",
                "Destination File",
                "Proposed Tag Changes",
                "Preserved Custom Frames",
                "Backup Path",
                "Safety Status",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=1)

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
        self.table.setRowCount(len(self.current_plans))
        for idx, plan in enumerate(self.current_plans):
            self._populate_plan_row(idx, plan)
        safe_count = sum(1 for p in self.current_plans if p.is_safe_to_apply)
        self.status_label.setText(
            f"{safe_count} of {len(self.current_plans)} files ready for safe backed-up write."
        )

    def _populate_plan_row(self, row_idx: int, plan: FileWritePlan) -> None:
        """Render a single FileWritePlan row in the preview table."""
        tags_str = ", ".join(f"{k}={v}" for k, v in plan.proposed_tags_to_write.items()) or "None"
        preserved_str = ", ".join(plan.preserved_custom_tags.keys()) or "None"
        issues = plan.validation_errors + plan.collision_warnings
        status_str = "READY" if plan.is_safe_to_apply else f"BLOCKED: {'; '.join(issues)}"
        cells = [
            plan.source_path.name,
            plan.destination_path.name,
            tags_str,
            preserved_str,
            str(plan.backup_destination_path.name),
            status_str,
        ]
        for col_idx, val in enumerate(cells):
            self.table.setItem(row_idx, col_idx, QTableWidgetItem(val))

    def execute_safe_writes(self) -> None:
        """Apply all safe plans in current_plans and accept the dialog."""
        safe_plans = [p for p in self.current_plans if p.is_safe_to_apply]
        if safe_plans:
            self.write_service.apply_batch(safe_plans)
        self.accept()
