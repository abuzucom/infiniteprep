"""An in-memory stand-in for SettingsManager, so window tests never touch settings.json."""

from __future__ import annotations

from dataclasses import replace

from infiniteprep.config.settings import AppSettings


class MemorySettingsManager:
    """Keep application settings in memory for window tests."""

    def __init__(self) -> None:
        self.settings = AppSettings()

    def load(self) -> AppSettings:
        """Return a separate settings value for each load."""
        return replace(self.settings)

    def save(self, settings: AppSettings) -> None:
        """Retain a copy of the last saved settings value."""
        self.settings = replace(settings)
