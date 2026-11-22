"""Tests for the Settings section list: order, icons, timing notes, and page switching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import SettingsManager
from infiniteprep.ui.settings_dialog import SettingsDialog

SHOWN_SECTIONS = ("General", "Analysis", "Transcode", "Identification", "Updates", "Storage")
SECTIONS_WITHOUT_NOTE = ("Updates",)


@pytest.fixture
def dialog(qtbot: Any, tmp_path: Path) -> SettingsDialog:
    """Open a Settings dialog over a fresh settings file."""
    settings_dialog = SettingsDialog(
        SettingsManager(tmp_path / "settings.json"), CredentialStore(use_memory_backend=True)
    )
    qtbot.addWidget(settings_dialog)
    return settings_dialog


def test_sections_list_in_order(dialog: SettingsDialog) -> None:
    """The list shows each section with settings, in order, each with its icon."""
    section_list = dialog.tabs.section_list
    titles = [section_list.item(row).text() for row in range(section_list.count())]

    assert tuple(titles) == SHOWN_SECTIONS
    assert all(not section_list.item(row).icon().isNull() for row in range(section_list.count()))


def test_sections_state_when_changes_apply(dialog: SettingsDialog) -> None:
    """Every section with settings states when its changes take effect."""
    notes = dict(zip(SHOWN_SECTIONS, dialog.tabs.notes, strict=True))

    for title, note in notes.items():
        assert bool(note) is (title not in SECTIONS_WITHOUT_NOTE), title


def test_list_click_shows_the_section(qtbot: Any, dialog: SettingsDialog) -> None:
    """Choosing a section in the list shows its page and its settings."""
    storage_row = SHOWN_SECTIONS.index("Storage")

    dialog.tabs.section_list.setCurrentRow(storage_row)

    assert dialog.tabs.currentIndex() == storage_row
    assert dialog.tabs.currentWidget() is dialog.storage_tab
    assert dialog.tabs.pages.currentIndex() == storage_row


def test_first_section_opens_first(dialog: SettingsDialog) -> None:
    """The dialog opens on General."""
    assert dialog.tabs.currentWidget() is dialog.general_tab
