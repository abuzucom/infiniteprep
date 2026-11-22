"""A titled section with an arrow button that shows or hides its content."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """Show a title row with a collapse arrow above one content widget.

    The arrow points down while the content shows and right while it hides. The
    title button takes Tab focus, and Space or Enter toggles it.
    """

    expanded_changed = Signal(bool)

    def __init__(
        self,
        title: str,
        content: QWidget,
        parent: QWidget | None = None,
        *,
        expanded: bool = True,
    ) -> None:
        """Build the title button and place the content below it."""
        super().__init__(parent)
        self.content = content
        self.toggle_button = QToolButton(self)
        self.toggle_button.setText(title)
        self.toggle_button.setAccessibleName(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle_button.toggled.connect(self._apply_expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(content)
        self.toggle_button.setChecked(expanded)
        self._apply_expanded(expanded)

    @property
    def title(self) -> str:
        """Return the section title."""
        return self.toggle_button.text()

    def is_expanded(self) -> bool:
        """Return True while the content shows."""
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the content."""
        self.toggle_button.setChecked(expanded)

    def _apply_expanded(self, expanded: bool) -> None:
        """Point the arrow and show or hide the content."""
        arrow = Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        self.toggle_button.setArrowType(arrow)
        self.content.setVisible(expanded)
        self.expanded_changed.emit(expanded)
