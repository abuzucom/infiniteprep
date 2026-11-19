"""Pytest options shared by every suite under tests/.

The --exclude-upstream flag lets the ci test matrix skip the upstream test
copies that other jobs already run on every operating system. Local pytest
runs collect everything unless the flag is passed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

EXCLUDE_UPSTREAM_OPTION = "--exclude-upstream"
LOADER_PATH = Path(__file__).resolve().parent / "upstream_tests.py"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_PATHS_KEY = pytest.StashKey[frozenset[Path]]()


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
