"""Application icon loading for every InfinitePrep window (D393)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

# The frozen build copies the PNG beside this module, so one lookup serves both builds.
APP_ICON_PATH: Final[Path] = Path(__file__).with_name("app-icon.png")


def load_application_icon() -> QIcon:
    """Return the InfinitePrep logo as a Qt icon."""
    return QIcon(str(APP_ICON_PATH))


def apply_application_icon(qt_app: QApplication) -> None:
    """Set the logo as the default icon of every window and the taskbar entry.

    A missing or unreadable PNG leaves the platform default icon. The warning names
    the file, so a packaging layout that drops it shows up in the log.
    """
    icon = load_application_icon()
    if icon.isNull():
        logger.warning(
            "App icon %s is missing or unreadable. Reinstall InfinitePrep to restore it.",
            APP_ICON_PATH,
        )
        return
    qt_app.setWindowIcon(icon)
