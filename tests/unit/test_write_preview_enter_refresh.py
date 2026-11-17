"""Behavioral test for the Enter key in the write preview rename template field."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.write_plan_dialog import WritePlanDialog

NEW_TEMPLATE: str = "{artist}_{title}"


class CountingWriteService(WriteService):
    """A real WriteService that records the template of every preview build."""

    def __init__(self, repository: Repository) -> None:
        """Start with an empty record of preview templates."""
        super().__init__(repository)
        self.preview_templates: list[str] = []

    def build_batch_preview(
        self,
        file_ids: list[str],
        rename_template: str | None = None,
        enable_rename: bool = False,
    ) -> list[FileWritePlan]:
        """Record the rename template, then build the preview as usual."""
        self.preview_templates.append(rename_template or "")
        return super().build_batch_preview(file_ids, rename_template, enable_rename)


def test_enter_in_template_field_refreshes_the_preview_once(qtbot: Any) -> None:
    """Enter rebuilds the preview once with the typed template and keeps the dialog open."""
    service = CountingWriteService(Repository(DatabaseManager(":memory:")))
    dialog = WritePlanDialog(service, [])
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    dialog.template_input.setFocus()
    qtbot.waitUntil(dialog.template_input.hasFocus)
    dialog.template_input.setText(NEW_TEMPLATE)
    builds_before_enter = len(service.preview_templates)

    qtbot.keyClick(dialog.template_input, Qt.Key.Key_Return)

    assert service.preview_templates[builds_before_enter:] == [NEW_TEMPLATE]
    assert dialog.isVisible()
    assert dialog.result() != WritePlanDialog.DialogCode.Accepted
