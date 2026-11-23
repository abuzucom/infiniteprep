"""The badge row under the detail panel's track name: status, format, BPM, and key."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from infiniteprep.ui.detail_badges import TrackBadges
from infiniteprep.ui.icon_loader import cached_themed_icon
from infiniteprep.ui.library_columns import status_icon
from infiniteprep.ui.tokens import DARK_PALETTE, Palette

BADGE_ICON_SIZE: Final[int] = 16
BPM_SUFFIX: Final[str] = " BPM"
# A badge keeps a small inner margin, so its text never touches the fill edge.
BADGE_STYLE: Final[str] = "padding: 1px 6px; border-radius: 4px;"


class TrackBadgesRow(QWidget):
    """Show the status, format, BPM, and key of the selected track.

    The status badge shows its icon and its word. The key badge takes the key color.
    A badge without a value hides.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the four badges, all hidden until a track loads."""
        super().__init__(parent)
        self._palette: Palette = DARK_PALETTE
        self.status_badge = QToolButton(self)
        self.status_badge.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.status_badge.setIconSize(QSize(BADGE_ICON_SIZE, BADGE_ICON_SIZE))
        self.status_badge.setAutoRaise(True)
        # The badge shows a state. It takes no click and no Tab stop.
        self.status_badge.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.status_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.format_badge = QLabel(self)
        self.bpm_badge = QLabel(self)
        self.key_badge = QLabel(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for badge in (self.status_badge, self.format_badge, self.bpm_badge, self.key_badge):
            layout.addWidget(badge)
        for label in (self.format_badge, self.bpm_badge, self.key_badge):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet(BADGE_STYLE)
        layout.addStretch()
        self.clear()

    def set_palette(self, palette: Palette) -> None:
        """Use the theme's colors for the status icon on the next show."""
        self._palette = palette

    def clear(self) -> None:
        """Hide every badge."""
        for badge in (self.status_badge, self.format_badge, self.bpm_badge, self.key_badge):
            badge.hide()

    def show_badges(self, badges: TrackBadges) -> None:
        """Show the badges of one track."""
        self._show_status(badges)
        self._show_text(self.format_badge, badges.format_text)
        bpm_text = f"{badges.bpm_text}{BPM_SUFFIX}" if badges.bpm_text else ""
        self._show_text(self.bpm_badge, bpm_text)
        self._show_text(self.key_badge, badges.key_text)
        key_style = BADGE_STYLE
        if badges.key_colors is not None:
            fill, text = badges.key_colors.fill, badges.key_colors.text
            key_style = f"{BADGE_STYLE} background: {fill}; color: {text};"
        self.key_badge.setStyleSheet(key_style)

    def _show_status(self, badges: TrackBadges) -> None:
        """Show the status word with its state icon in its status color."""
        self.status_badge.setText(badges.status_text)
        self.status_badge.setAccessibleName(badges.status_text)
        state = status_icon(badges.status, self._palette)
        if state is None:
            self.status_badge.setIcon(QIcon())
        else:
            icon_name, color = state
            self.status_badge.setIcon(cached_themed_icon(icon_name, color))
        self.status_badge.setVisible(bool(badges.status_text))

    @staticmethod
    def _show_text(badge: QLabel, text: str) -> None:
        """Show a text badge, or hide it when the text is empty."""
        badge.setText(text)
        badge.setVisible(bool(text))
