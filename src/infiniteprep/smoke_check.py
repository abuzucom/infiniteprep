"""Bundle smoke check: load the runtime libraries and Qt, then record the version.

A packaged build runs this through the hidden --smoke-check flag. PyInstaller
bundles most often fail on a missing native library, Qt plugin, or hidden
import. Each step here loads one of those, so a broken bundle exits with an
error before it writes the version file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import keyring
import numpy
import scipy.signal
import soundfile
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from infiniteprep import __version__

QT_PLATFORM_VAR: Final[str] = "QT_QPA_PLATFORM"
# The offscreen platform needs no display, so CI runners and the windowed
# Windows build can run the check without opening a window.
OFFSCREEN_PLATFORM: Final[str] = "offscreen"
SMOKE_WINDOW_NAME: Final[str] = "hann"
SMOKE_WINDOW_LENGTH: Final[int] = 8


def _load_native_libraries() -> None:
    """Call into each native library the app needs, so a missing one raises here."""
    numpy.zeros(SMOKE_WINDOW_LENGTH)
    scipy.signal.get_window(SMOKE_WINDOW_NAME, SMOKE_WINDOW_LENGTH)
    if not soundfile.__libsndfile_version__:
        raise RuntimeError("libsndfile did not load. Rebuild the bundle with soundfile's library.")
    keyring.get_keyring()


def _load_qt_multimedia() -> None:
    """Start Qt offscreen and create a media player, which loads the multimedia backend."""
    os.environ.setdefault(QT_PLATFORM_VAR, OFFSCREEN_PLATFORM)
    application = QApplication.instance() or QApplication([])
    player = QMediaPlayer()
    player.deleteLater()
    application.processEvents()


def run_smoke_check(output_path: Path) -> int:
    """Load the runtime libraries and Qt, write the version to output_path, and return 0."""
    _load_native_libraries()
    _load_qt_multimedia()
    output_path.write_text(__version__, encoding="utf-8")
    return 0
