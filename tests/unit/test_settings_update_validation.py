"""SettingsManager.update rejects invalid values instead of storing defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.config.settings import AppSettings, SettingsManager

SAVED_ZOOM_SECONDS = 16
UNSUPPORTED_ZOOM_SECONDS = 3
FLOAT_ZOOM_SECONDS = 16.0


@pytest.mark.parametrize("invalid_value", [UNSUPPORTED_ZOOM_SECONDS, FLOAT_ZOOM_SECONDS, "16"])
def test_update_rejects_invalid_value_and_keeps_saved_setting(
    tmp_path: Path, invalid_value: object
) -> None:
    """An invalid update raises and leaves the stored value unchanged."""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save(AppSettings(waveform_zoom_seconds=SAVED_ZOOM_SECONDS))

    with pytest.raises(ValueError, match="waveform_zoom_seconds"):
        manager.update(waveform_zoom_seconds=invalid_value)

    assert manager.load().waveform_zoom_seconds == SAVED_ZOOM_SECONDS


def test_update_rejects_negative_column_count(tmp_path: Path) -> None:
    """A negative count raises and writes nothing."""
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(settings_path)

    with pytest.raises(ValueError, match="file_table_column_count"):
        manager.update(file_table_column_count=-1)

    assert not settings_path.exists()
