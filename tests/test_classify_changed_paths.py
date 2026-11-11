"""Behavioral checks for the changed-path classifier that selects CI jobs."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "classify_changed_paths.py"
GIT = shutil.which("git") or "git"
GIT_IDENTITY = ("-c", "user.name=Test Author", "-c", "user.email=author@example.invalid")
NULL_REVISION = "0" * 40
PYPROJECT_TEXT = '[project]\nname = "sample"\nversion = "1.0.0"\ndependencies = ["alpha==1.0"]\n'
PACKAGE_INIT_TEXT = '"""Sample package."""\n\n__version__ = "1.0.0"\n'
BASE_FILES = {
    "README.md": "Sample\n",
    "CHANGELOG.md": "## [1.0.0]\n",
    "docs/guide.md": "Guide\n",
    "docs/project-orientation.md": "Orientation\n",
    "website/index.html": "<p>Site</p>\n",
    "pyproject.toml": PYPROJECT_TEXT,
    "src/infiniteprep/__init__.py": PACKAGE_INIT_TEXT,
    "src/infiniteprep/app.py": "VALUE = 1\n",
}


def load_classifier() -> ModuleType:
    """Load the classifier script as a module without changing sys.path."""
    spec = importlib.util.spec_from_file_location("classify_changed_paths", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classifier = load_classifier()


class ClassifyPathsTest(unittest.TestCase):
    """The path sets decide the class, and any other path selects full CI."""

    def test_each_path_set_maps_to_its_class(self) -> None:
        """Docs, website, mixed, and code path sets map to four classes."""
        cases = (
            (["README.md"], "docs"),
            (["docs/guide.md", "CHANGELOG.md"], "docs"),
            (["CHANGELOG.md"], "docs"),
            (["website/index.html", "CHANGELOG.md"], "website"),
            (["website/assets/logo.svg"], "website"),
            (["docs/guide.md", "website/robots.txt"], "light"),
            (["docs/guide.md", "src/infiniteprep/app.py"], "full"),
            ([], "full"),
        )
        for paths, expected in cases:
            with self.subTest(paths=paths):
                self.assertEqual(classifier.classify_paths(paths), expected)

    def test_policy_inputs_and_other_prose_select_full(self) -> None:
        """Policy inputs and prose outside the docs set keep full CI."""
        full_paths = (
            "AGENTS.md",
            "CLAUDE.md",
            "docs/project-orientation.md",
            "docs/Project-Orientation.md",
            "docs/agent-policy/security.md",
            "docs/diagram.png",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "plan/HANDOFF.md.example",
            "docs/../src/app.md",
            "website/../pyproject.toml",
        )
        for path in full_paths:
            with self.subTest(path=path):
                self.assertEqual(classifier.classify_paths([path]), "full")

    def test_version_files_count_only_when_verified(self) -> None:
        """A version file is light only after its diff passed the version check."""
        paths = ["docs/guide.md", "pyproject.toml", "src/infiniteprep/__init__.py"]
        self.assertEqual(classifier.classify_paths(paths), "full")
        verified = frozenset({"pyproject.toml", "src/infiniteprep/__init__.py"})
        self.assertEqual(classifier.classify_paths(paths, verified), "docs")
        partly_verified = frozenset({"pyproject.toml"})
        self.assertEqual(classifier.classify_paths(paths, partly_verified), "full")


class VersionOnlyEditTest(unittest.TestCase):
    """Only the version value may differ between the two file states."""

    def test_pyproject_version_change_is_version_only(self) -> None:
        """A new project version with no other change passes."""
        new_text = PYPROJECT_TEXT.replace('version = "1.0.0"', 'version = "1.0.1"')
        self.assertTrue(classifier.is_version_only_edit("pyproject.toml", PYPROJECT_TEXT, new_text))

    def test_pyproject_dependency_change_is_rejected(self) -> None:
        """A dependency change beside the version change fails."""
        new_text = PYPROJECT_TEXT.replace('version = "1.0.0"', 'version = "1.0.1"')
        new_text = new_text.replace("alpha==1.0", "alpha==2.0")
        self.assertFalse(
            classifier.is_version_only_edit("pyproject.toml", PYPROJECT_TEXT, new_text)
        )

    def test_pyproject_version_in_another_table_is_rejected(self) -> None:
        """A version key outside the project table is not the project version."""
        new_text = PYPROJECT_TEXT + '\n[tool.sample]\nversion = "9.9.9"\n'
        self.assertFalse(
            classifier.is_version_only_edit("pyproject.toml", PYPROJECT_TEXT, new_text)
        )

    def test_unparsable_pyproject_is_rejected(self) -> None:
        """A file that is not TOML fails."""
        self.assertFalse(
            classifier.is_version_only_edit("pyproject.toml", PYPROJECT_TEXT, "[project\n")
        )

    def test_package_version_change_is_version_only(self) -> None:
        """A new package version line with no other change passes."""
        new_text = PACKAGE_INIT_TEXT.replace("1.0.0", "1.0.1")
        path = "src/infiniteprep/__init__.py"
        self.assertTrue(classifier.is_version_only_edit(path, PACKAGE_INIT_TEXT, new_text))

    def test_package_code_change_is_rejected(self) -> None:
        """Code beside the version line fails, on the same line or on a new one."""
        path = "src/infiniteprep/__init__.py"
        added_line = PACKAGE_INIT_TEXT + "import os\n"
        same_line = PACKAGE_INIT_TEXT.replace('"1.0.0"', '"1.0.1"; import os')
        for new_text in (added_line, same_line):
            with self.subTest(new_text=new_text):
                self.assertFalse(classifier.is_version_only_edit(path, PACKAGE_INIT_TEXT, new_text))

    def test_other_files_are_never_version_only(self) -> None:
        """The check covers the two version files and nothing else."""
        self.assertFalse(classifier.is_version_only_edit("setup.py", "a\n", "a\n"))


class ClassifyRangeTest(unittest.TestCase):
    """The classifier reads a real commit range from a Git repository."""

    def setUp(self) -> None:
        """Create a repository with one base commit."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run_git("init", "--quiet")
        self.base = self.commit_files(BASE_FILES)

    def run_git(self, *arguments: str) -> str:
        """Run one Git command in the test repository and return its output."""
        result = subprocess.run(
            [GIT, "-C", str(self.root), *GIT_IDENTITY, "-c", "commit.gpgsign=false", *arguments],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def commit_files(self, files: dict[str, str]) -> str:
        """Write the files, commit them, and return the commit revision."""
        for name, text in files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.run_git("add", "--all")
        self.run_git("commit", "--quiet", "--message", "change")
        return self.run_git("rev-parse", "HEAD")

    def classify(self, head: str) -> str:
        """Classify the range from the base commit to the given head."""
        return classifier.classify_range(str(self.root), self.base, head)

    def test_docs_change_with_version_bump_is_docs(self) -> None:
        """A prose change plus the three version edits stays in the docs class."""
        head = self.commit_files(
            {
                "docs/guide.md": "Guide, revised\n",
                "CHANGELOG.md": "## [1.0.1]\n",
                "pyproject.toml": PYPROJECT_TEXT.replace('"1.0.0"', '"1.0.1"'),
                "src/infiniteprep/__init__.py": PACKAGE_INIT_TEXT.replace("1.0.0", "1.0.1"),
            }
        )
        self.assertEqual(self.classify(head), "docs")

    def test_website_change_is_website(self) -> None:
        """A landing site change stays in the website class."""
        head = self.commit_files({"website/index.html": "<p>New site</p>\n"})
        self.assertEqual(self.classify(head), "website")

    def test_dependency_change_beside_docs_is_full(self) -> None:
        """A dependency edit in pyproject.toml selects full CI."""
        head = self.commit_files(
            {
                "docs/guide.md": "Guide, revised\n",
                "pyproject.toml": PYPROJECT_TEXT.replace("alpha==1.0", "alpha==2.0"),
            }
        )
        self.assertEqual(self.classify(head), "full")

    def test_rename_out_of_the_docs_set_is_full(self) -> None:
        """A rename reports both paths, and the code path selects full CI."""
        self.run_git("mv", "docs/guide.md", "src/infiniteprep/guide.md")
        self.run_git("commit", "--quiet", "--message", "move")
        self.assertEqual(self.classify(self.run_git("rev-parse", "HEAD")), "full")

    def test_invalid_revisions_raise(self) -> None:
        """A revision that is not a full commit hash never reaches Git."""
        for revision in ("", "main", "--output=x", NULL_REVISION):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                classifier.classify_range(str(self.root), revision, self.base)

    def test_main_writes_both_outputs(self) -> None:
        """The command writes the class and the light flag for the workflow."""
        head = self.commit_files({"README.md": "Sample, revised\n"})
        output = self.root / "outputs.txt"
        arguments = ["--repository", str(self.root), "--base", self.base, "--head", head]
        self.assertEqual(classifier.main(arguments, str(output)), 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "change_class=docs\nlight=true\n")

    def test_main_selects_full_when_the_range_is_unreadable(self) -> None:
        """An unknown commit selects full CI and still exits with success."""
        output = self.root / "outputs.txt"
        missing = "1" * 40
        arguments = ["--repository", str(self.root), "--base", self.base, "--head", missing]
        self.assertEqual(classifier.main(arguments, str(output)), 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "change_class=full\nlight=false\n")


if __name__ == "__main__":
    unittest.main()
