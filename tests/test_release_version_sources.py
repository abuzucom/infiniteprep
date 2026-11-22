"""Checks how check-tag reports unreadable and unparsable version files (D317)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "packaging" / "release.py"
VERSION = "1.2.0"
INIT_TEXT = f'__version__ = "{VERSION}"\n'
PYPROJECT_TEXT = f'[project]\nname = "infiniteprep"\nversion = "{VERSION}"\n'
CHANGELOG_TEXT = f"# Changelog\n\n## [{VERSION}] (2026-11-09)\n\n- The feature.\n"
MALFORMED_PYPROJECT_TEXT = "[project\n"
PARSE_FAILURE_TEXT = "does not parse"
MISSING_TEXT = "is missing from the checkout"
NO_VERSION_TEXT = "states no version"


def load_release_module() -> ModuleType:
    """Import packaging/release.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("release_version_sources_target", RELEASE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {RELEASE_PATH}. Check that the file exists.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = load_release_module()


def write_version_files(root: Path, init: str, pyproject: str, changelog: str) -> None:
    """Write the three version files of a release checkout."""
    package = root / "src" / "infiniteprep"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(init, encoding="utf-8")
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


class UnparsablePyprojectTest(unittest.TestCase):
    """A pyproject.toml that does not parse gets its own problem line."""

    def test_parse_failure_not_called_missing(self) -> None:
        """The line names the parse failure instead of an empty version."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_version_files(root, INIT_TEXT, MALFORMED_PYPROJECT_TEXT, CHANGELOG_TEXT)
            problems = RELEASE.find_tag_problems(f"v{VERSION}", root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("pyproject.toml", problems[0])
        self.assertIn(PARSE_FAILURE_TEXT, problems[0])
        self.assertNotIn(NO_VERSION_TEXT, problems[0])

    def test_parse_failure_keeps_empty_version(self) -> None:
        """read_versions still maps an unparsable pyproject.toml to an empty version."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_version_files(root, INIT_TEXT, MALFORMED_PYPROJECT_TEXT, CHANGELOG_TEXT)
            versions = RELEASE.read_versions(root)
        self.assertEqual(versions[RELEASE.PYPROJECT_FILE], "")
        self.assertEqual(versions[RELEASE.INIT_FILE], VERSION)
        self.assertEqual(versions[RELEASE.CHANGELOG_FILE], VERSION)


class NonFileVersionSourceTest(unittest.TestCase):
    """A version source that is not a regular file is reported before any read."""

    def test_version_dir_gives_problem_line(self) -> None:
        """A directory named CHANGELOG.md yields the missing line, not a traceback."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_version_files(root, INIT_TEXT, PYPROJECT_TEXT, CHANGELOG_TEXT)
            changelog = root / "CHANGELOG.md"
            changelog.unlink()
            changelog.mkdir()
            problems = RELEASE.find_tag_problems(f"v{VERSION}", root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("CHANGELOG.md", problems[0])
        self.assertIn(MISSING_TEXT, problems[0])


if __name__ == "__main__":
    unittest.main()
