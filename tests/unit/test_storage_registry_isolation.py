"""Each test runs against its own storage registry under its own temporary folder."""

from __future__ import annotations

from pathlib import Path

from infiniteprep import storage_paths
from infiniteprep.storage_paths import StorageMode

# The process-wide registry as the import left it, before any test fixture ran.
PROCESS_REGISTRY = storage_paths.STORAGE_REGISTRY


def test_each_test_gets_fresh_registry() -> None:
    """The process-wide registry never reaches a test."""
    assert storage_paths.STORAGE_REGISTRY is not PROCESS_REGISTRY


def test_default_locations_inside_test_folder(tmp_path: Path) -> None:
    """Code that reads the default folders writes below tmp_path, never in the real profile."""
    locations = storage_paths.current()

    for folder in (
        locations.data_dir,
        locations.config_dir,
        locations.cache_dir,
        locations.backup_dir,
        locations.bin_dir,
    ):
        assert folder.is_relative_to(tmp_path), folder
    assert locations.mode is StorageMode.PER_USER


def test_two_tests_never_share_registry(tmp_path: Path) -> None:
    """A registry configured in one test holds that test's folder only."""
    assert storage_paths.current().data_dir.parent == tmp_path
