"""Checks for the PR #64 quality review fixes in release.py and the release job (D319 to D321)."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "packaging" / "release.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build-packages.yml"
VERSION = "1.2.0"
INIT_TEXT = f'__version__ = "{VERSION}"\n'
PYPROJECT_TEXT = f'[project]\nname = "infiniteprep"\nversion = "{VERSION}"\n'
CHANGELOG_TEXT = f"# Changelog\n\n## [{VERSION}] (2026-11-09)\n\n- The feature.\n"
# Three MiB spans several reads for any chunked digest.
LARGE_FILE_BYTES = 3 * 1024 * 1024 + 17
SIX_ASSETS = (
    f"InfinitePrep-{VERSION}-windows-x64-portable.zip",
    f"InfinitePrep-{VERSION}-windows-x64-setup.exe",
    f"InfinitePrep-{VERSION}-linux-x86_64.AppImage",
    f"InfinitePrep-{VERSION}-linux-x86_64.tar.gz",
    f"InfinitePrep-{VERSION}-linux-x86_64-portable.tar.gz",
    f"InfinitePrep-{VERSION}-macos-arm64.dmg",
)


def _load_release() -> ModuleType:
    """Import packaging/release.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("release_review_target", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load_release()


def _make_repo(root: Path, init: str | None, pyproject: str | None, changelog: str | None) -> None:
    """Write each version file whose text is not None."""
    package = root / "src" / "infiniteprep"
    package.mkdir(parents=True)
    for path, text in (
        (package / "__init__.py", init),
        (root / "pyproject.toml", pyproject),
        (root / "CHANGELOG.md", changelog),
    ):
        if text is not None:
            path.write_text(text, encoding="utf-8")


def _make_assets(folder: Path, names: tuple[str, ...]) -> None:
    """Write one small file per asset name."""
    for name in names:
        (folder / name).write_bytes(name.encode("utf-8"))


def _run_main(argv: list[str]) -> tuple[int, str]:
    """Run release.main and return its exit code and printed text."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = RELEASE.main(argv)
    return exit_code, output.getvalue()


class MissingVersionFileTest(unittest.TestCase):
    """A missing version file gives a problem line, not a traceback."""

    def test_each_missing_file_is_named(self) -> None:
        """Each absent file yields one line that names it."""
        cases = (
            ("src/infiniteprep/__init__.py", (None, PYPROJECT_TEXT, CHANGELOG_TEXT)),
            ("pyproject.toml", (INIT_TEXT, None, CHANGELOG_TEXT)),
            ("CHANGELOG.md", (INIT_TEXT, PYPROJECT_TEXT, None)),
        )
        for missing, texts in cases:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                _make_repo(root, *texts)
                problems = RELEASE.tag_problems(f"v{VERSION}", root)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(missing, problems[0])
                self.assertIn("missing", problems[0])

    def test_malformed_pyproject_gives_a_problem_line(self) -> None:
        """A pyproject.toml that does not parse states no version."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _make_repo(root, INIT_TEXT, "[project\n", CHANGELOG_TEXT)
            problems = RELEASE.tag_problems(f"v{VERSION}", root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("pyproject.toml", problems[0])


class EmptyVersionTest(unittest.TestCase):
    """An empty version never matches a tag."""

    def test_bare_prefix_tag_fails_when_no_file_states_a_version(self) -> None:
        """The tag v fails even when all three files state no version."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _make_repo(root, "", "[project]\n", "# Changelog\n")
            problems = RELEASE.tag_problems("v", root)
        self.assertTrue(problems)
        self.assertTrue(any("names no version" in problem for problem in problems), problems)
        self.assertEqual(sum("states no version" in problem for problem in problems), 3, problems)

    def test_bare_prefix_tag_fails_from_the_command_line(self) -> None:
        """check-tag v returns the failure code."""
        exit_code, output = _run_main(["check-tag", "v"])
        self.assertEqual(exit_code, RELEASE.CHECK_FAILED)
        self.assertIn("names no version", output)


class ChangelogEndTest(unittest.TestCase):
    """A heading on the last line without a newline does not crash."""

    def test_last_heading_without_newline_gives_an_empty_body(self) -> None:
        """The section body is empty instead of raising ValueError."""
        changelog = f"# Changelog\n\n## [{VERSION}] (2026-11-09)"
        self.assertEqual(RELEASE.changelog_section(VERSION, changelog).strip(), "")


class FileDigestTest(unittest.TestCase):
    """file_sha256 matches hashlib on a file larger than one read."""

    def test_digest_matches_hashlib(self) -> None:
        """The digest equals hashlib.sha256 over the whole content."""
        content = bytes(index % 251 for index in range(LARGE_FILE_BYTES))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.bin"
            path.write_bytes(content)
            self.assertEqual(RELEASE.file_sha256(path), hashlib.sha256(content).hexdigest())


class AssetNameTest(unittest.TestCase):
    """check-assets requires the six package names and nothing else."""

    def test_six_expected_names_pass(self) -> None:
        """The six names for the version pass."""
        with tempfile.TemporaryDirectory() as folder:
            _make_assets(Path(folder), SIX_ASSETS)
            self.assertEqual(RELEASE.asset_problems(Path(folder), VERSION), [])
            exit_code, _ = _run_main(["check-assets", folder, VERSION])
        self.assertEqual(exit_code, 0)

    def test_missing_name_fails(self) -> None:
        """A missing package is named and fails the check."""
        with tempfile.TemporaryDirectory() as folder:
            _make_assets(Path(folder), SIX_ASSETS[1:])
            problems = RELEASE.asset_problems(Path(folder), VERSION)
            exit_code, output = _run_main(["check-assets", folder, VERSION])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(SIX_ASSETS[0], problems[0])
        self.assertEqual(exit_code, RELEASE.CHECK_FAILED)
        self.assertIn(SIX_ASSETS[0], output)

    def test_wrong_version_fails(self) -> None:
        """Six packages of another version all fail, in both directions."""
        with tempfile.TemporaryDirectory() as folder:
            _make_assets(Path(folder), tuple(name.replace(VERSION, "1.1.9") for name in SIX_ASSETS))
            problems = RELEASE.asset_problems(Path(folder), VERSION)
        self.assertEqual(len(problems), 2 * len(SIX_ASSETS), problems)

    def test_extra_name_fails(self) -> None:
        """An extra file is named and fails the check."""
        with tempfile.TemporaryDirectory() as folder:
            _make_assets(Path(folder), (*SIX_ASSETS, "stray.txt"))
            problems = RELEASE.asset_problems(Path(folder), VERSION)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("stray.txt", problems[0])


class ReleaseJobReviewFixTest(unittest.TestCase):
    """The release job checks the names and deletes nothing."""

    steps: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the release job steps once."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.steps = workflow["jobs"]["release"]["steps"]

    def _run(self, name: str) -> str:
        """Return the run script of the one step with this name."""
        [step] = [step for step in self.steps if step.get("name") == name]
        return step["run"]

    def test_download_checks_the_asset_names(self) -> None:
        """check-assets runs after the downloads, with the tag version."""
        download = self._run("Download package artifacts")
        call = 'release.py check-assets "$assets" "${GITHUB_REF_NAME#v}"'
        self.assertIn(call, download)
        self.assertLess(download.rindex("gh run download"), download.index(call))

    def test_upload_uses_a_fresh_folder_per_asset(self) -> None:
        """Each existing asset downloads into a new mktemp folder."""
        upload = self._run("Upload to draft release")
        self.assertNotIn("rm -rf", upload)
        self.assertIn('mktemp -d "$RUNNER_TEMP/existing-asset.XXXXXX"', upload)
        self.assertLess(upload.index("mktemp -d"), upload.index("gh release download"))


if __name__ == "__main__":
    unittest.main()
