"""Settings writes replace the file atomically and survive a failed write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.config.settings import AppSettings, SettingsManager

ORIGINAL_TEMPLATE = "{artist} - {title}"
REPLACEMENT_TEMPLATE = "{title}"
UPDATED_ZOOM_SECONDS = 16


def _fail_replace(_source: Any, _target: Any) -> None:
    raise OSError("simulated replace failure")


def test_failed_save_keeps_previous_settings_file(tmp_path: Path, monkeypatch: Any) -> None:
    """A write failure leaves the earlier settings readable and no partial file."""
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(settings_path)
    manager.save(AppSettings(rename_template=ORIGINAL_TEMPLATE))
    monkeypatch.setattr("infiniteprep.config.settings.os.replace", _fail_replace)

    with pytest.raises(OSError):
        manager.save(AppSettings(rename_template=REPLACEMENT_TEMPLATE))

    assert manager.load().rename_template == ORIGINAL_TEMPLATE
    assert list(tmp_path.iterdir()) == [settings_path]


def test_failed_update_keeps_previous_settings_file(tmp_path: Path, monkeypatch: Any) -> None:
    """A failed partial update leaves the earlier settings readable."""
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(settings_path)
    manager.save(AppSettings(rename_template=ORIGINAL_TEMPLATE))
    monkeypatch.setattr("infiniteprep.config.settings.os.replace", _fail_replace)

    with pytest.raises(OSError):
        manager.update(waveform_zoom_seconds=UPDATED_ZOOM_SECONDS)

    loaded = manager.load()
    assert loaded.rename_template == ORIGINAL_TEMPLATE
    assert loaded.waveform_zoom_seconds == AppSettings().waveform_zoom_seconds
    assert list(tmp_path.iterdir()) == [settings_path]


def test_unexpected_write_failure_removes_the_temporary_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A failure outside OSError still leaves no temporary file behind."""
    settings_path = tmp_path / "settings.json"
    manager = SettingsManager(settings_path)
    manager.save(AppSettings(rename_template=ORIGINAL_TEMPLATE))

    def fail_unexpectedly(_source: Any, _target: Any) -> None:
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr("infiniteprep.config.settings.os.replace", fail_unexpectedly)

    with pytest.raises(RuntimeError):
        manager.save(AppSettings(rename_template=REPLACEMENT_TEMPLATE))

    assert manager.load().rename_template == ORIGINAL_TEMPLATE
    assert list(tmp_path.iterdir()) == [settings_path]


def test_save_and_update_leave_only_the_settings_file(tmp_path: Path) -> None:
    """Successful writes remove their temporary file."""
    settings_path = tmp_path / "config" / "settings.json"
    manager = SettingsManager(settings_path)

    manager.save(AppSettings(rename_template=ORIGINAL_TEMPLATE))
    manager.update(waveform_zoom_seconds=UPDATED_ZOOM_SECONDS)

    loaded = manager.load()
    assert loaded.rename_template == ORIGINAL_TEMPLATE
    assert loaded.waveform_zoom_seconds == UPDATED_ZOOM_SECONDS
    assert list(settings_path.parent.iterdir()) == [settings_path]
