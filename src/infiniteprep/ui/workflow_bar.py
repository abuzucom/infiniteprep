"""The workflow bar: five numbered stages, the current stage's action, and tools."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QToolButton, QWidget

from infiniteprep.ui.icon_loader import themed_icon
from infiniteprep.ui.stylesheet import (
    BUTTON_VARIANT_PROPERTY,
    CURRENT_STAGE_PROPERTY,
    ICON_BUTTON_VARIANT,
    PRIMARY_BUTTON_VARIANT,
)
from infiniteprep.ui.tokens import DARK_PALETTE, Palette
from infiniteprep.ui.workflow import STAGE_ORDER, WorkflowStage

STAGE_LABELS: Final[dict[WorkflowStage, str]] = {
    WorkflowStage.IMPORT: "Import",
    WorkflowStage.ANALYZE: "Analyze",
    WorkflowStage.REVIEW: "Review",
    WorkflowStage.APPLY: "Apply",
    WorkflowStage.EXPORT: "Export",
}
# Each stage shows the icon of its main action: Import folder, Analyze, Approve all,
# Write tags, and Export playlist.
STAGE_ICONS: Final[dict[WorkflowStage, str]] = {
    WorkflowStage.IMPORT: "folder-input",
    WorkflowStage.ANALYZE: "zoom-question",
    WorkflowStage.REVIEW: "check-check",
    WorkflowStage.APPLY: "tag-plus",
    WorkflowStage.EXPORT: "square-arrow-out-up-right",
}
# The primary button names the action of the current stage.
STAGE_ACTION_LABELS: Final[dict[WorkflowStage, str]] = {
    WorkflowStage.IMPORT: "Import folder",
    WorkflowStage.ANALYZE: "Analyze",
    WorkflowStage.REVIEW: "Approve all matches",
    WorkflowStage.APPLY: "Review changes",
    WorkflowStage.EXPORT: "Export playlist",
}
TRANSCODE_TOOL_LABEL: Final[str] = "Transcode"
HISTORY_TOOL_LABEL: Final[str] = "History"
SETTINGS_TOOL_LABEL: Final[str] = "Settings"
TRANSCODE_ICON: Final[str] = "replace"
HISTORY_ICON: Final[str] = "history"
SETTINGS_ICON: Final[str] = "settings"
STAGE_ICON_SIZE: Final[int] = 20
TOOL_ICON_SIZE: Final[int] = 20
STAGE_TEXT_GAP: Final[str] = "  "


def format_stage_text(stage: WorkflowStage, count: int, show_label: bool) -> str:
    """Return the text of a stage button: number, label when shown, and count."""
    number = STAGE_ORDER.index(stage) + 1
    parts = [str(number)]
    if show_label:
        parts.append(STAGE_LABELS[stage])
    parts.append(str(count))
    return STAGE_TEXT_GAP.join(parts)


def stage_shortcut(stage: WorkflowStage) -> QKeySequence:
    """Return the window shortcut that runs a stage: Alt and the stage number."""
    return QKeySequence(f"Alt+{STAGE_ORDER.index(stage) + 1}")


def format_stage_tooltip(stage: WorkflowStage, count: int) -> str:
    """Return the tooltip of a stage button: its spoken name and its shortcut."""
    shown_key = stage_shortcut(stage).toString(QKeySequence.SequenceFormat.NativeText)
    return f"{format_stage_name(stage, count)} ({shown_key})"


def format_stage_name(stage: WorkflowStage, count: int) -> str:
    """Return the spoken name of a stage button, which always holds the label."""
    noun = "track" if count == 1 else "tracks"
    return f"{STAGE_LABELS[stage]}, {count} {noun}"


class WorkflowBar(QWidget):
    """Top bar with the five stages, the current stage's action, and three tools.

    A stage button runs its stage's action. The primary button runs the action of
    the current stage. Transcode, History, and Settings sit at the right end.
    """

    stage_triggered = Signal(str)
    transcode_requested = Signal()
    history_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the stage buttons, the primary action button, and the tool buttons."""
        super().__init__(parent)
        self._palette: Palette = DARK_PALETTE
        self._counts: dict[WorkflowStage, int] = dict.fromkeys(STAGE_ORDER, 0)
        self._current: WorkflowStage = WorkflowStage.IMPORT
        self._compact = False
        self._locked: set[WorkflowStage] = set()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stage_buttons: dict[WorkflowStage, QToolButton] = {}
        for stage in STAGE_ORDER:
            button = self._build_stage_button(stage)
            self.stage_buttons[stage] = button
            layout.addWidget(button)
        self.primary_button = QPushButton(self)
        self.primary_button.setProperty(BUTTON_VARIANT_PROPERTY, PRIMARY_BUTTON_VARIANT)
        self.primary_button.clicked.connect(self._on_primary_clicked)
        layout.addWidget(self.primary_button)
        layout.addStretch()
        self.transcode_btn = self._build_tool_button(TRANSCODE_TOOL_LABEL)
        self.history_btn = self._build_tool_button(HISTORY_TOOL_LABEL)
        self.settings_btn = self._build_tool_button(SETTINGS_TOOL_LABEL)
        self.transcode_btn.clicked.connect(self.transcode_requested)
        self.history_btn.clicked.connect(self.history_requested)
        self.settings_btn.clicked.connect(self.settings_requested)
        for button in (self.transcode_btn, self.history_btn, self.settings_btn):
            layout.addWidget(button)
        self._render()

    def _build_stage_button(self, stage: WorkflowStage) -> QToolButton:
        """Return a stage button that emits its stage when clicked."""
        button = QToolButton(self)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(STAGE_ICON_SIZE, STAGE_ICON_SIZE))
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.clicked.connect(lambda: self.stage_triggered.emit(stage.value))
        return button

    def _build_tool_button(self, label: str) -> QToolButton:
        """Return an icon-only tool button whose label serves as tooltip and spoken name."""
        button = QToolButton(self)
        button.setProperty(BUTTON_VARIANT_PROPERTY, ICON_BUTTON_VARIANT)
        button.setIconSize(QSize(TOOL_ICON_SIZE, TOOL_ICON_SIZE))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button

    @property
    def current_stage(self) -> WorkflowStage:
        """Return the stage whose action the primary button carries."""
        return self._current

    def set_counts(self, counts: dict[WorkflowStage, int], current: WorkflowStage) -> None:
        """Show new stage counts and move the primary action to the current stage.

        Each table refresh calls this. Unchanged counts skip the icon renders.
        """
        new_counts = {stage: counts.get(stage, 0) for stage in STAGE_ORDER}
        if new_counts == self._counts and current is self._current:
            return
        self._counts = new_counts
        self._current = current
        self._render()

    def set_compact(self, compact: bool) -> None:
        """Hide the stage labels except the current one, for windows below 1600 px."""
        self._compact = compact
        self._render()

    def set_palette(self, palette: Palette) -> None:
        """Draw the icons in the colors of the theme in effect."""
        self._palette = palette
        self._render()

    def is_stage_locked(self, stage: WorkflowStage) -> bool:
        """Return True while a stage's action must wait, such as Review during a batch."""
        return stage in self._locked

    def set_stages_locked(self, stages: set[WorkflowStage]) -> None:
        """Disable the stages whose action must wait, such as Review while a batch runs."""
        self._locked = set(stages)
        self._render()

    def _render(self) -> None:
        """Apply the counts, labels, icons, current mark, and locks to every button."""
        for stage, button in self.stage_buttons.items():
            is_current = stage is self._current
            show_label = is_current or not self._compact
            count = self._counts[stage]
            button.setText(format_stage_text(stage, count, show_label))
            button.setAccessibleName(format_stage_name(stage, count))
            button.setToolTip(format_stage_tooltip(stage, count))
            color = self._palette.highlight if is_current else self._palette.text_muted
            button.setIcon(themed_icon(STAGE_ICONS[stage], color))
            button.setProperty(CURRENT_STAGE_PROPERTY, is_current)
            button.setEnabled(stage not in self._locked)
            button.style().unpolish(button)
            button.style().polish(button)
        self.primary_button.setText(STAGE_ACTION_LABELS[self._current])
        self.primary_button.setEnabled(self._current not in self._locked)
        tool_icons = (
            (self.transcode_btn, TRANSCODE_ICON),
            (self.history_btn, HISTORY_ICON),
            (self.settings_btn, SETTINGS_ICON),
        )
        for button, icon_name in tool_icons:
            button.setIcon(themed_icon(icon_name, self._palette.text))

    def _on_primary_clicked(self) -> None:
        """Run the action of the current stage."""
        self.stage_triggered.emit(self._current.value)
