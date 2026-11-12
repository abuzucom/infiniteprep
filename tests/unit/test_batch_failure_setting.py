"""Behavioral tests for the stop-at-first-failure setting and the cleanup lock in Settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.ui.settings_dialog import SettingsDialog


def test_stop_setting_defaults_off_and_older_settings_files_load(tmp_path: Path) -> None:
    """A batch continues by default, and a file without the key loads that default."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"transcode_mode": "sibling_export"}), encoding="utf-8")

    assert AppSettings().stop_batch_on_first_failure is False
    assert SettingsManager(settings_path).load().stop_batch_on_first_failure is False


def test_settings_dialog_saves_the_stop_checkbox(qtbot: Any, tmp_path: Path) -> None:
    """Checking the box and saving stores the stop rule. Reopening shows it checked."""
    manager = SettingsManager(tmp_path / "settings.json")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)
    assert not dialog.stop_on_failure_checkbox.isChecked()

    dialog.stop_on_failure_checkbox.setChecked(True)
    dialog.save_settings()

    assert manager.load().stop_batch_on_first_failure is True
    reopened = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(reopened)
    assert reopened.stop_on_failure_checkbox.isChecked()


def test_cleanup_button_follows_the_cleanup_enabled_flag(qtbot: Any, tmp_path: Path) -> None:
    """The data deletion button is on by default and off while the caller reports a batch."""
    manager = SettingsManager(tmp_path / "settings.json")
    default_dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    locked_dialog = SettingsDialog(
        manager, CredentialStore(use_memory_backend=True), cleanup_enabled=False
    )
    qtbot.addWidget(default_dialog)
    qtbot.addWidget(locked_dialog)

    assert default_dialog.cleanup_btn.isEnabled()
    assert not locked_dialog.cleanup_btn.isEnabled()
