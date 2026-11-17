"""Helpers that give Qt controls the names a screen reader announces."""

from __future__ import annotations

from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QComboBox, QWidget


def name_combo_box(combo: QComboBox, name: str) -> None:
    """Name a combo box and the list it opens.

    The open list is a separate popup view. Without its own name, a screen reader
    announces an unnamed list once the popup opens.
    """
    combo.setAccessibleName(name)
    combo.view().setAccessibleName(name)


def announce(widget: QWidget, text: str) -> None:
    """Ask a screen reader to speak a status change that the widget shows.

    A label update alone is silent to a screen reader. Empty text announces nothing.
    """
    if not text.strip():
        return
    QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(widget, text))
