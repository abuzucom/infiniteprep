"""Behavioral tests: default folders follow the configured storage locations (D263)."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep import storage_paths
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.write_service import WriteService
from infiniteprep.storage_paths import StorageLocations, StorageMode, StorageRegistry


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StorageLocations:
    """Install a fresh registry configured with portable-style temp folders."""
    locations = StorageLocations(
        data_dir=tmp_path / "Data",
        config_dir=tmp_path / "Data",
        cache_dir=tmp_path / "cache",
        backup_dir=tmp_path / "backups",
        bin_dir=tmp_path / "bin",
        mode=StorageMode.PORTABLE,
    )
    registry = StorageRegistry()
    registry.configure(locations)
    monkeypatch.setattr(storage_paths, "STORAGE_REGISTRY", registry)
    return locations


def test_settings_save_into_the_configured_folder(configured: StorageLocations) -> None:
    """SettingsManager() with no path reads and writes settings.json in config_dir."""
    manager = SettingsManager()
    settings = AppSettings()
    settings.theme = "light_blue.xml"

    manager.save(settings)

    assert manager.settings_path == configured.config_dir / "settings.json"
    assert manager.settings_path.is_file()
    assert SettingsManager().load().theme == "light_blue.xml"


def test_services_default_to_the_configured_folders(configured: StorageLocations) -> None:
    """Backups and the fpcalc tool default to the configured folders."""
    repository = Repository(DatabaseManager(":memory:"))

    assert WriteService(repository).backup_dir == configured.backup_dir
    assert FpcalcBootstrapService().install_dir == configured.bin_dir
