"""Persisted application settings reject invalid types and supported ranges."""

from __future__ import annotations

import json
from pathlib import Path

from infiniteprep.config.settings import AppSettings, SettingsManager


def test_load_replaces_invalid_setting_types_and_ranges(tmp_path: Path) -> None:
    """Malformed persisted values fall back independently to their defaults."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "enable_acoustid": "false",
                "rename_template": None,
                "file_table_column_count": -1,
                "waveform_zoom_seconds": 8.0,
            }
        ),
        encoding="utf-8",
    )

    loaded = SettingsManager(settings_path).load()
    defaults = AppSettings()

    assert loaded.enable_acoustid is defaults.enable_acoustid
    assert loaded.rename_template == defaults.rename_template
    assert loaded.file_table_column_count == defaults.file_table_column_count
    assert type(loaded.waveform_zoom_seconds) is int
    assert loaded.waveform_zoom_seconds == defaults.waveform_zoom_seconds


def test_load_replaces_non_object_settings_document(tmp_path: Path) -> None:
    """A JSON array cannot be interpreted as a settings mapping."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("[]", encoding="utf-8")

    assert SettingsManager(settings_path).load() == AppSettings()
