"""Checks that the classifier reads version files apart from the version decision."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_classify_changed_paths import (
    BASE_FILES,
    PACKAGE_INIT_TEXT,
    PYPROJECT_TEXT,
    classifier,
    commit_files_in,
    run_git_in,
)

PYPROJECT_FILE = "pyproject.toml"
PACKAGE_INIT_FILE = "src/infiniteprep/__init__.py"
BUMPED_PYPROJECT_TEXT = PYPROJECT_TEXT.replace('version = "1.0.0"', 'version = "1.0.1"')
CHANGED_PACKAGE_INIT_TEXT = PACKAGE_INIT_TEXT + "VALUE = 2\n"


class SelectVersionOnlyFilesTest(unittest.TestCase):
    """The version decision works on file texts without Git."""

    def test_only_version_edits_are_selected(self) -> None:
        """A version bump passes and a code change beside the version line fails."""
        texts = {
            PYPROJECT_FILE: (PYPROJECT_TEXT, BUMPED_PYPROJECT_TEXT),
            PACKAGE_INIT_FILE: (PACKAGE_INIT_TEXT, CHANGED_PACKAGE_INIT_TEXT),
        }
        self.assertEqual(classifier.select_version_only_files(texts), frozenset({PYPROJECT_FILE}))

    def test_no_texts_select_no_files(self) -> None:
        """An empty mapping selects nothing."""
        self.assertEqual(classifier.select_version_only_files({}), frozenset())


class ReadVersionFileTextsTest(unittest.TestCase):
    """The Git reader returns the old and new text of each changed version file."""

    def setUp(self) -> None:
        """Build a repository with a base commit and one bump commit."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "repository"
        self.root.mkdir()
        run_git_in(self.root, "init", "--quiet")
        self.base = commit_files_in(self.root, BASE_FILES)
        self.head = commit_files_in(self.root, {PYPROJECT_FILE: BUMPED_PYPROJECT_TEXT})

    def test_reads_only_the_changed_version_files(self) -> None:
        """Paths outside the version files never reach Git."""
        texts = classifier.read_version_file_texts(
            str(self.root), self.base, self.head, [PYPROJECT_FILE, "docs/guide.md"]
        )
        self.assertEqual(texts, {PYPROJECT_FILE: (PYPROJECT_TEXT, BUMPED_PYPROJECT_TEXT)})

    def test_composition_keeps_the_find_result(self) -> None:
        """find_version_only_files still returns the verified version files."""
        found = classifier.find_version_only_files(
            str(self.root), self.base, self.head, [PYPROJECT_FILE]
        )
        self.assertEqual(found, frozenset({PYPROJECT_FILE}))


if __name__ == "__main__":
    unittest.main()
