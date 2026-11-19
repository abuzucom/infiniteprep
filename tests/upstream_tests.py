"""Read the upstream test copies that the test matrix skips.

The tests/ entries of the pyproject.toml [tool.ruff] extend-exclude list name
the byte-identical copies of the abuzucom/agents gate tests and the review
wiring test. The ci checks job, os-tests, and the sync-check hook coverage run
those tests already. This module imports the standard library only, because
the run_tests.py environments install no pytest.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT_NAME = "pyproject.toml"
TESTS_DIR_NAME = "tests"
TESTS_PREFIX = f"{TESTS_DIR_NAME}/"
TEST_NAME_PREFIX = "test_"
TEST_SUFFIX = ".py"


class UpstreamTestListError(ValueError):
    """The upstream test list is missing, malformed, unsafe, or stale."""


def read_exclude_entries(root: Path) -> list[str]:
    """Return the [tool.ruff] extend-exclude entries of root/pyproject.toml."""
    pyproject_path = root / PYPROJECT_NAME
    try:
        document = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise UpstreamTestListError(
            f"cannot read {pyproject_path}: {error}. Restore a valid pyproject.toml."
        ) from error
    entries = document.get("tool", {}).get("ruff", {}).get("extend-exclude")
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        raise UpstreamTestListError(
            f"{pyproject_path} [tool.ruff] extend-exclude must be a list of strings. Fix the list."
        )
    return entries


def resolve_upstream_paths(root: Path, entries: list[str]) -> list[Path]:
    """Return the root test module paths named by entries, rejecting escapes."""
    tests_dir = (root / TESTS_DIR_NAME).resolve()
    paths: list[Path] = []
    for entry in entries:
        # Helper modules and directory entries stay in the Ruff list only.
        is_test_module = Path(entry).name.startswith(TEST_NAME_PREFIX)
        if not (entry.startswith(TESTS_PREFIX) and entry.endswith(TEST_SUFFIX) and is_test_module):
            continue
        if (root / entry).resolve().parent != tests_dir:
            raise UpstreamTestListError(
                f"entry {entry!r} resolves outside tests/. List only root test modules."
            )
        paths.append(root / entry)
    return paths


def load_upstream_test_paths(root: Path) -> frozenset[Path]:
    """Return the upstream root test modules listed in root/pyproject.toml.

    Each call reads the file again. The caller owns the returned set.
    """
    paths = resolve_upstream_paths(root, read_exclude_entries(root))
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise UpstreamTestListError(
            f"listed upstream tests are missing: {', '.join(missing)}. "
            "Remove the stale entries from [tool.ruff] extend-exclude."
        )
    if not paths:
        raise UpstreamTestListError(
            "no upstream test module is listed in [tool.ruff] extend-exclude. "
            "Restore the tests/test_*.py entries."
        )
    return frozenset(paths)
