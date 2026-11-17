"""Behavioral checks for the accessible names of the combo box popups."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QWidget

from infiniteprep.ui.accessibility import name_combo_popups


def test_a_popup_takes_the_name_of_its_combo(qtbot: Any) -> None:
    """The popup list of a combo box carries the accessible name of the combo."""
    window = QWidget()
    qtbot.addWidget(window)
    combo = QComboBox(window)
    combo.setAccessibleName("Status filter")
    combo.addItems(["All files", "With warnings"])

    name_combo_popups(window)

    assert combo.view().accessibleName() == "Status filter"


def test_a_combo_without_a_name_leaves_its_popup_unnamed(qtbot: Any) -> None:
    """A combo box without a name gives no name to its popup."""
    window = QWidget()
    qtbot.addWidget(window)
    combo = QComboBox(window)
    combo.addItems(["One choice"])

    name_combo_popups(window)

    assert combo.view().accessibleName() == ""


def test_a_combo_below_a_child_widget_is_named(qtbot: Any) -> None:
    """The walk reaches the combo boxes of every child widget."""
    window = QWidget()
    qtbot.addWidget(window)
    panel = QWidget(window)
    combo = QComboBox(panel)
    combo.setAccessibleName("Theme")
    combo.addItems(["Dark", "Light"])

    name_combo_popups(window)

    assert combo.view().accessibleName() == "Theme"
