"""Behavioral tests for the explicit bundle requirement of check-assets (D319, D328)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "packaging" / "release.py"
VERSION = "1.2.0"
NOT_AN_ASSET = "is not a release asset"


def _load_release() -> ModuleType:
    """Import packaging/release.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("release_bundle_flag_target", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load_release()
PACKAGES = tuple(sorted(RELEASE.expected_packages(VERSION)))
BUNDLES = tuple(sorted(RELEASE.expected_bundles(VERSION)))


def _write_files(folder: Path, names: tuple[str, ...]) -> None:
    """Write one small file per name."""
    for name in names:
        (folder / name).write_bytes(name.encode("utf-8"))


def _run_check_assets(arguments: list[str]) -> tuple[int, str]:
    """Run check-assets through release.main and return its exit code and output."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = RELEASE.main(["check-assets", *arguments])
    return exit_code, output.getvalue()


class ExplicitBundleRequirementTest(unittest.TestCase):
    """Only --require-bundles makes check-assets expect Sigstore bundles."""

    def test_stray_bundle_is_an_extra_without_the_flag(self) -> None:
        """One bundle beside the six packages yields one extra-file problem."""
        with tempfile.TemporaryDirectory() as folder:
            _write_files(Path(folder), (*PACKAGES, BUNDLES[0]))
            problems = RELEASE.asset_problems(Path(folder), VERSION)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(BUNDLES[0], problems[0])
        self.assertIn(NOT_AN_ASSET, problems[0])

    def test_complete_bundle_set_fails_without_the_flag(self) -> None:
        """Every bundle counts as an extra file when the flag is absent."""
        with tempfile.TemporaryDirectory() as folder:
            _write_files(Path(folder), (*PACKAGES, *BUNDLES))
            problems = RELEASE.asset_problems(Path(folder), VERSION)
            exit_code, _ = _run_check_assets([folder, VERSION])
        self.assertEqual(len(problems), len(BUNDLES), problems)
        self.assertTrue(all(NOT_AN_ASSET in problem for problem in problems), problems)
        self.assertEqual(exit_code, RELEASE.CHECK_FAILED)

    def test_complete_bundle_set_passes_with_the_flag(self) -> None:
        """The six packages and six bundles pass under --require-bundles."""
        with tempfile.TemporaryDirectory() as folder:
            _write_files(Path(folder), (*PACKAGES, *BUNDLES))
            problems = RELEASE.asset_problems(Path(folder), VERSION, require_bundles=True)
            exit_code, _ = _run_check_assets([folder, VERSION, "--require-bundles"])
        self.assertEqual(problems, [])
        self.assertEqual(exit_code, 0)


class SuccessMessageCountTest(unittest.TestCase):
    """The success line states the number of expected assets."""

    def test_message_counts_packages_without_the_flag(self) -> None:
        """Six packages give a count of six."""
        with tempfile.TemporaryDirectory() as folder:
            _write_files(Path(folder), PACKAGES)
            exit_code, output = _run_check_assets([folder, VERSION])
        self.assertEqual(exit_code, 0)
        self.assertIn(f"holds the {len(PACKAGES)} release assets", output)

    def test_message_counts_every_expected_name(self) -> None:
        """A folder entry that is not a regular file still counts as its expected name."""
        with tempfile.TemporaryDirectory() as folder:
            _write_files(Path(folder), PACKAGES[1:])
            (Path(folder) / PACKAGES[0]).mkdir()
            exit_code, output = _run_check_assets([folder, VERSION])
        self.assertEqual(exit_code, 0)
        self.assertIn(f"holds the {len(PACKAGES)} release assets", output)

    def test_message_counts_packages_and_bundles_with_the_flag(self) -> None:
        """Six packages and six bundles give a count of twelve."""
        with tempfile.TemporaryDirectory() as folder:
            _write_files(Path(folder), (*PACKAGES, *BUNDLES))
            exit_code, output = _run_check_assets([folder, VERSION, "--require-bundles"])
        self.assertEqual(exit_code, 0)
        self.assertIn(f"holds the {len(PACKAGES) + len(BUNDLES)} release assets", output)


if __name__ == "__main__":
    unittest.main()
