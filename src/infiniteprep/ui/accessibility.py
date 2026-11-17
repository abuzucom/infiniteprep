"""Helpers that give the controls of the app an accessible name."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget


def name_combo_popups(root: QWidget) -> None:
    """Give every combo box popup below root the accessible name of its combo box.

    A combo box popup holds a list view that takes the focus while the popup
    is open. The view carries no name of its own, so a screen reader announces
    an unnamed list. The name of the combo box names the popup as well.
    """
    for combo in root.findChildren(QComboBox):
        view = combo.view()
        if view is not None:
            view.setAccessibleName(combo.accessibleName())
