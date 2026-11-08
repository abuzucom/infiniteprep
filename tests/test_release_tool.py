"""Behavioral checks for packaging/release.py: the tag check, checksums, and notes."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "packaging" / "release.py"
CHANGELOG = """\
# Changelog

Intro text.

## [1.2.0] (2026-11-09)

### Added

- The new feature.

## [1.1.0] (2026-11-08)

### Fixed

- The old fix.
"""


def _load_release() -> ModuleType:
    """Import packaging/release.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("release_target", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load_release()


def _make_repo(root: Path, init: str, pyproject: str, changelog: str) -> None:
    """Write the three version files the tag check reads."""
    package = root / "src" / "infiniteprep"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'"""App."""\n\n__version__ = "{init}"\n', "utf-8")
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{pyproject}"\n', "utf-8")
    (root / "CHANGELOG.md").write_text(changelog, "utf-8")


class TagCheckTest(unittest.TestCase):
    """A tag releases only when every version file names its version (D317)."""

    def test_matching_tag_passes(self) -> None:
        """v1.2.0 matches __init__.py, pyproject.toml, and the top CHANGELOG entry."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_repo(root, "1.2.0", "1.2.0", CHANGELOG)

            self.assertEqual(RELEASE.tag_problems("v1.2.0", root), [])

    def test_each_mismatched_file_is_named(self) -> None:
        """A stale pyproject.toml and an older tag each produce their own line."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_repo(root, "1.2.0", "1.1.0", CHANGELOG)

            problems = RELEASE.tag_problems("v1.2.0", root)
            older = RELEASE.tag_problems("v1.1.0", root)

        self.assertEqual(len(problems), 1)
        self.assertIn("pyproject.toml", problems[0])
        self.assertEqual(
            sorted(problem.split()[0] for problem in older),
            ["CHANGELOG.md", "src/infiniteprep/__init__.py"],
        )

    def test_tag_without_the_prefix_fails(self) -> None:
        """A tag must start with 'v'."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_repo(root, "1.2.0", "1.2.0", CHANGELOG)

            problems = RELEASE.tag_problems("1.2.0", root)

        self.assertEqual(len(problems), 1)
        self.assertIn("does not start with", problems[0])

    def test_repository_versions_agree(self) -> None:
        """The real version files name one version, so a tag for it would pass."""
        versions = RELEASE.read_versions(ROOT)
        self.assertEqual(len(set(versions.values())), 1, versions)


class ChecksumTest(unittest.TestCase):
    """SHA256SUMS.txt lists every other file in sha256sum format, sorted by name."""

    def test_checksums_cover_every_asset_once(self) -> None:
        """Each asset gets one line, and a rerun does not list SHA256SUMS.txt itself."""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "b.dmg").write_bytes(b"mac")
            (folder / "a.zip").write_bytes(b"win")
            RELEASE.write_checksums(folder)

            output = RELEASE.write_checksums(folder)
            text = output.read_text(encoding="utf-8")

        expected = (
            f"{hashlib.sha256(b'win').hexdigest()}  a.zip\n"
            f"{hashlib.sha256(b'mac').hexdigest()}  b.dmg\n"
        )
        self.assertEqual(text, expected)


class NotesTest(unittest.TestCase):
    """The notes hold one version's CHANGELOG section and the download block (D318)."""

    def test_section_stops_at_the_next_release(self) -> None:
        """The 1.2.0 section holds its own entries and none of 1.1.0."""
        section = RELEASE.changelog_section("1.2.0", CHANGELOG)
        self.assertIn("The new feature.", section)
        self.assertNotIn("The old fix.", section)
        self.assertNotIn("## [1.2.0]", section)

    def test_last_section_runs_to_the_end(self) -> None:
        """The oldest section ends at the end of the file."""
        self.assertIn("The old fix.", RELEASE.changelog_section("1.1.0", CHANGELOG))

    def test_missing_version_raises(self) -> None:
        """A version without a section raises ValueError with the version named."""
        with self.assertRaises(ValueError) as caught:
            RELEASE.changelog_section("9.9.9", CHANGELOG)
        self.assertIn("9.9.9", str(caught.exception))

    def test_notes_end_with_the_download_block(self) -> None:
        """The notes name SHA256SUMS.txt, the macOS Open Anyway step, and NOTICE."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_repo(root, "1.2.0", "1.2.0", CHANGELOG)

            notes = RELEASE.release_notes("1.2.0", root)

        self.assertTrue(notes.startswith("### Added"))
        for fragment in ("SHA256SUMS.txt", "Open Anyway", "NOTICE"):
            self.assertIn(fragment, notes)


class CommandTest(unittest.TestCase):
    """The command line runs the tag check against the real repository."""

    def test_check_tag_fails_for_a_wrong_tag(self) -> None:
        """A tag that names no version in the repository returns 1 and says why."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = RELEASE.main(["check-tag", "v0.0.0-not-a-release"])

        self.assertEqual(exit_code, RELEASE.CHECK_FAILED)
        self.assertIn("align the tag and the version files", output.getvalue())


if __name__ == "__main__":
    unittest.main()
