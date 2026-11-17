"""Helpers that give Qt controls the names a screen reader announces."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox


def name_combo_box(combo: QComboBox, name: str) -> None:
    """Name a combo box and the list it opens.

    The open list is a separate popup view. Without its own name, a screen reader
    announces an unnamed list once the popup opens.
    """
    combo.setAccessibleName(name)
    combo.view().setAccessibleName(name)
