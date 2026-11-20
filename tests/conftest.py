"""Pytest options and fixtures shared by every suite under tests/.

The --exclude-upstream flag lets the ci test matrix skip the upstream test
copies that other jobs already run on every operating system. Local pytest
runs collect everything unless the flag is passed.

Every test also gets a fresh storage registry under its own tmp_path, so no
test reads or writes the real per-user profile.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

EXCLUDE_UPSTREAM_OPTION = "--exclude-upstream"
LOADER_PATH = Path(__file__).resolve().parent / "upstream_tests.py"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_PATHS_KEY = pytest.StashKey[frozenset[Path]]()
APP_PACKAGE = "infiniteprep"
STORAGE_MODULE = f"{APP_PACKAGE}.storage_paths"


def load_storage_paths() -> ModuleType | None:
    """Return the storage module, or None when a third-party dependency is absent.

    The lint-and-style job runs one invariant test with only ruff and pytest
    installed. The source tree is importable there, but platformdirs is not, and
    no test there can reach the registry. A missing module inside the app package
    is a real defect, so it still raises.
    """
    try:
        return importlib.import_module(STORAGE_MODULE)
    except ModuleNotFoundError as error:
        missing = error.name or ""
        # A submodule of the app package that fails to import is a defect in the tree.
        if missing.startswith(f"{APP_PACKAGE}."):
            raise
        # Any other missing module is an absent dependency, such as platformdirs.
        return None


def load_upstream_module() -> ModuleType:
    """Load tests/upstream_tests.py by path. The tests folder is no package."""
    spec = importlib.util.spec_from_file_location("upstream_tests", LOADER_PATH)
    if spec is None or spec.loader is None:
        raise pytest.UsageError(f"cannot load {LOADER_PATH}. Restore the loader module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the opt-in upstream exclusion flag."""
    parser.addoption(
        EXCLUDE_UPSTREAM_OPTION,
        action="store_true",
        default=False,
        help="Skip the upstream test copies listed in pyproject.toml.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Read the upstream list once per session when the flag is set."""
    if not config.getoption(EXCLUDE_UPSTREAM_OPTION):
        return
    loader = load_upstream_module()
    try:
        config.stash[UPSTREAM_PATHS_KEY] = loader.load_upstream_test_paths(REPOSITORY_ROOT)
    except loader.UpstreamTestListError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip a listed upstream module. Defer every other path to pytest."""
    upstream_paths = config.stash.get(UPSTREAM_PATHS_KEY, frozenset())
    if collection_path in upstream_paths:
        return True
    return None


@pytest.fixture(autouse=True)
def isolate_storage_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Swap the process-wide storage registry for one configured below tmp_path.

    A test that installs its own registry replaces this one. Autouse fixtures run
    first, so that later monkeypatch wins.
    """
    storage_paths = load_storage_paths()
    if storage_paths is None:
        yield
        return
    # The layout mirrors per_user_locations(): backups and tools live in the data folder.
    data_dir = tmp_path / "storage-data"
    registry = storage_paths.StorageRegistry()
    registry.configure(
        storage_paths.StorageLocations(
            data_dir=data_dir,
            config_dir=tmp_path / "storage-config",
            cache_dir=tmp_path / "storage-cache",
            backup_dir=data_dir / storage_paths.BACKUP_DIR_NAME,
            bin_dir=data_dir / storage_paths.BIN_DIR_NAME,
            mode=storage_paths.StorageMode.PER_USER,
        )
    )
    monkeypatch.setattr(storage_paths, "STORAGE_REGISTRY", registry)
    yield
