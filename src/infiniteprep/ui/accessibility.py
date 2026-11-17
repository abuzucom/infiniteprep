"""Accessible names for composite widgets whose inner parts take keyboard focus."""

from __future__ import annotations

from typing import Final

from PySide6.QtWidgets import QComboBox

# The popup list of a picker reads as "<picker name> options".
POPUP_NAME_SUFFIX: Final[str] = " options"


def set_combo_box_name(combo: QComboBox, name: str) -> None:
    """Name a combo box and the popup list that opens from it.

    The popup list is a separate widget that Tab can reach. A screen reader
    announced it with no name while only the combo box carried one.
    """
    combo.setAccessibleName(name)
    combo.view().setAccessibleName(f"{name}{POPUP_NAME_SUFFIX}")
