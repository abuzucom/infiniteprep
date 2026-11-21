"""Behavioral tests for toolbar mnemonics and main window keyboard shortcuts."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QAccessible, QKeySequence
from PySide6.QtWidgets import QLabel

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.memory_settings import MemorySettingsManager

# Positional argument index of QFileDialog.getExistingDirectory(parent, caption).
FOLDER_CAPTION_INDEX = 1

# The spoken name of each workflow bar button in an empty library.
EXPECTED_BAR_NAMES: tuple[str, ...] = (
    "Import, 0 tracks",
    "Analyze, 0 tracks",
    "Review, 0 tracks",
    "Apply, 0 tracks",
    "Export, 0 tracks",
    "Transcode",
    "History",
    "Settings",
)
LITERAL_AMPERSAND_PLACEHOLDER: str = "\x00"


@pytest.fixture
def window(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    """Open an empty main window with settings held in memory."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    repository = Repository(DatabaseManager(":memory:"))
    built = MainWindow(
        repository=repository,
        import_service=ImportService(repository),
        review_service=ReviewService(repository),
    )
    qtbot.addWidget(built)
    return built


def shown_text(label: str) -> str:
    """Return the text Qt draws for a label: a doubled ampersand shows one, a single one hides."""
    kept = label.replace("&&", LITERAL_AMPERSAND_PLACEHOLDER).replace("&", "")
    return kept.replace(LITERAL_AMPERSAND_PLACEHOLDER, "&")


def mnemonic_key(label: str) -> str:
    """Return the upper-case letter after the single mnemonic ampersand, or an empty string."""
    kept = label.replace("&&", "")
    index = kept.find("&")
    if index < 0 or index + 1 >= len(kept):
        return ""
    return kept[index + 1].upper()


def test_screen_readers_name_every_workflow_bar_button(window: MainWindow) -> None:
    """Each stage button speaks its stage and count, and each tool button speaks its tool."""
    bar = window.workflow_bar
    buttons = [*bar.stage_buttons.values(), bar.transcode_btn, bar.history_btn, bar.settings_btn]

    names = [
        QAccessible.queryAccessibleInterface(button).text(QAccessible.Text.Name)
        for button in buttons
    ]

    assert names == list(EXPECTED_BAR_NAMES)


def test_toolbar_picker_labels_move_focus_to_their_pickers(window: MainWindow) -> None:
    """The Key Notation and Theme labels carry an Alt key that focuses their picker."""
    buddies = {
        shown_text(label.text()).strip(): (mnemonic_key(label.text()), label.buddy())
        for label in window.findChildren(QLabel)
        if label.buddy() is not None
    }

    assert buddies["Key Notation:"] == ("K", window.key_notation_combo)


def test_import_shortcut_opens_the_folder_picker(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Open key sequence asks for a folder to import."""
    # The picker recorder keeps each caption and answers with no folder.
    asked = CallRecorder(FOLDER_CAPTION_INDEX, reply="")
    monkeypatch.setattr(main_window_module.QFileDialog, "getExistingDirectory", asked)

    window.import_shortcut.activated.emit()

    assert window.import_shortcut.key() == QKeySequence(QKeySequence.StandardKey.Open)
    assert len(asked) == 1


def test_settings_shortcut_opens_settings(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl+Comma opens the settings dialog callback."""
    monkeypatch.setattr(main_window_module, "SettingsManager", MemorySettingsManager)
    repository = Repository(DatabaseManager(":memory:"))
    opened: list[bool] = []
    built = MainWindow(
        repository=repository,
        import_service=ImportService(repository),
        review_service=ReviewService(repository),
        on_open_settings_dialog=lambda: opened.append(True),
    )
    qtbot.addWidget(built)

    built.settings_shortcut.activated.emit()

    assert built.settings_shortcut.key() == QKeySequence("Ctrl+,")
    assert opened == [True]


def test_play_pause_shortcuts_press_the_player_button(window: MainWindow) -> None:
    """Ctrl+P and the media Play/Pause key both toggle the preview player."""
    play_button = window.detail_panel.audio_player.play_btn

    window.play_pause_shortcut.activated.emit()
    after_first = play_button.text()
    window.media_play_pause_shortcut.activated.emit()

    assert window.play_pause_shortcut.key() == QKeySequence("Ctrl+P")
    assert window.media_play_pause_shortcut.key() == QKeySequence("Media Play/Pause")
    assert (after_first, play_button.text()) == ("Pause", "Play")


def test_play_pause_shortcut_is_named_in_the_button_tooltip(window: MainWindow) -> None:
    """The player button tooltip tells keyboard users the shortcut in platform notation."""
    play_button = window.detail_panel.audio_player.play_btn
    native = QKeySequence("Ctrl+P").toString(QKeySequence.SequenceFormat.NativeText)

    assert native in play_button.toolTip()
