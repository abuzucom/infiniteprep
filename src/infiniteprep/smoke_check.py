"""Bundle smoke check: load the runtime libraries and Qt, then record the version.

A packaged build runs this through the hidden --smoke-check flag. PyInstaller
bundles most often fail on a missing native library, Qt plugin, or hidden
import. Each step here loads one of those, so a broken bundle exits with an
error before it writes the version file.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol

import keyring
import numpy
import scipy.signal
import soundfile
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from infiniteprep import __version__

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)
QT_PLATFORM_VAR: Final[str] = "QT_QPA_PLATFORM"
# The offscreen platform needs no display, so CI runners and the windowed
# Windows build can run the check without opening a window.
OFFSCREEN_PLATFORM: Final[str] = "offscreen"
SMOKE_WINDOW_NAME: Final[str] = "hann"
SMOKE_WINDOW_LENGTH: Final[int] = 8
# A missing backend returns an exit code instead of raising. The windowed Windows
# build would show an error dialog for an exception, and CI would wait on it.
SMOKE_CHECK_BACKEND_ERROR: Final[int] = 1
BACKEND_ERROR_MESSAGE: Final[str] = (
    "Qt Multimedia found no playback backend. Rebuild the bundle with the FFmpeg media"
    " plugin and every Qt library it loads."
)


class MediaPlayer(Protocol):
    """The part of QMediaPlayer the smoke check uses."""

    def isAvailable(self) -> bool:  # Qt's own method name.
        """Return True when a multimedia backend loaded."""
        ...

    def deleteLater(self) -> None:  # Qt's own method name.
        """Schedule the player for deletion."""
        ...


def _load_native_libraries() -> None:
    """Call into each native library the app needs, so a missing one raises here."""
    numpy.zeros(SMOKE_WINDOW_LENGTH)
    scipy.signal.get_window(SMOKE_WINDOW_NAME, SMOKE_WINDOW_LENGTH)
    if not soundfile.__libsndfile_version__:
        raise RuntimeError("libsndfile did not load. Rebuild the bundle with soundfile's library.")
    keyring.get_keyring()


def _load_qt_multimedia(player_factory: Callable[[], MediaPlayer]) -> bool:
    """Start Qt offscreen, create a media player, and return whether a backend loaded.

    Creating the player loads the multimedia backend. On every OS that backend is the
    FFmpeg plugin, so a plugin that cannot load its Qt libraries shows up here.
    """
    os.environ.setdefault(QT_PLATFORM_VAR, OFFSCREEN_PLATFORM)
    application = QApplication.instance() or QApplication([])
    player = player_factory()
    backend_available = player.isAvailable()
    player.deleteLater()
    application.processEvents()
    return backend_available


def run_smoke_check(
    output_path: Path, player_factory: Callable[[], MediaPlayer] = QMediaPlayer
) -> int:
    """Load the runtime libraries and Qt, write the version to output_path, and return 0.

    The file opens in exclusive-create mode, so the check never overwrites an existing
    file. An existing path raises FileExistsError. Without a multimedia backend the
    check writes no file and returns SMOKE_CHECK_BACKEND_ERROR.
    """
    _load_native_libraries()
    backend_available = _load_qt_multimedia(player_factory)
    if not backend_available:
        # The app configures no log handler, so logging's last-resort handler writes
        # this to stderr.
        LOGGER.error(BACKEND_ERROR_MESSAGE)
        return SMOKE_CHECK_BACKEND_ERROR
    with output_path.open("x", encoding="utf-8") as result_file:
        result_file.write(__version__)
    return 0
