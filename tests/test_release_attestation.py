"""Behavioral tests for the build provenance attestation and Sigstore bundles (D325, D328)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build-packages.yml"
RELEASE_PATH = ROOT / "packaging" / "release.py"
README_PATH = ROOT / "README.md"
ATTEST_ACTION = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
REQUIRED_PERMISSIONS = {
    "contents": "write",
    "actions": "read",
    "id-token": "write",
    "attestations": "write",
    "artifact-metadata": "write",
}
TEST_VERSION = "0.48.4"
PACKAGE_COUNT = 6
BUNDLE_COUNT = 6
# Six package attestations and one for SHA256SUMS.txt.
ATTEST_STEP_COUNT = 7
RELEASE_ASSET_COUNT = 12


def _load_release() -> ModuleType:
    """Import packaging/release.py by file path."""
    spec = importlib.util.spec_from_file_location("release_attest_target", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load_release()


class AttestationWorkflowTest(unittest.TestCase):
    """build-packages release job wires actions/attest and attestation permissions."""

    release_job: dict[str, Any]
    steps: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the release job once."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.release_job = workflow["jobs"]["release"]
        cls.steps = cls.release_job["steps"]

    def _step(self, name: str) -> dict[str, Any]:
        """Return the one step with this name."""
        [step] = [step for step in self.steps if step.get("name") == name]
        return step

    def test_release_job_holds_required_attestation_permissions(self) -> None:
        """The release job holds id-token, attestations, and artifact-metadata (D325)."""
        self.assertEqual(self.release_job["permissions"], REQUIRED_PERMISSIONS)

    def test_actions_attest_uses_pinned_commit_sha(self) -> None:
        """Every actions/attest invocation matches the pinned v4.2.2 SHA (D325)."""
        attest_steps = [
            step for step in self.steps if step.get("uses", "").startswith("actions/attest@")
        ]
        self.assertEqual(len(attest_steps), ATTEST_STEP_COUNT)
        for step in attest_steps:
            self.assertEqual(step["uses"], ATTEST_ACTION)

    def test_all_six_packages_and_checksums_are_attested(self) -> None:
        """Each of the six packages and SHA256SUMS.txt has a dedicated attestation step."""
        attest_steps = [
            step for step in self.steps if step.get("uses", "").startswith("actions/attest@")
        ]
        subjects = [step["with"]["subject-path"] for step in attest_steps]
        expected_suffixes = (
            "windows-x64-portable.zip",
            "windows-x64-setup.exe",
            "linux-x86_64.AppImage",
            "linux-x86_64.tar.gz",
            "linux-x86_64-portable.tar.gz",
            "macos-arm64.dmg",
        )
        for suffix in expected_suffixes:
            self.assertTrue(
                any(suffix in subject for subject in subjects),
                f"Missing attestation step for {suffix}",
            )
        self.assertTrue(
            any("SHA256SUMS.txt" in subject for subject in subjects),
            "Missing attestation step for SHA256SUMS.txt",
        )

    def test_save_bundles_step_checks_assets_with_require_bundles(self) -> None:
        """The bundle-saving step copies six bundles and runs check-assets --require-bundles."""
        step = self._step("Save package attestation bundles")
        run_script = step["run"]
        for suffix in (
            "windows-x64-portable.zip.sigstore.json",
            "windows-x64-setup.exe.sigstore.json",
            "linux-x86_64.AppImage.sigstore.json",
            "linux-x86_64.tar.gz.sigstore.json",
            "linux-x86_64-portable.tar.gz.sigstore.json",
            "macos-arm64.dmg.sigstore.json",
        ):
            self.assertIn(suffix, run_script)
        self.assertIn("check-assets", run_script)
        self.assertIn("--require-bundles", run_script)


class AttestationReleaseToolingTest(unittest.TestCase):
    """packaging/release.py identifies packages and Sigstore bundles (D328)."""

    def test_expected_bundles_names_six_sigstore_files(self) -> None:
        """expected_bundles returns the six .sigstore.json file names."""
        bundles = RELEASE.expected_bundles(TEST_VERSION)
        packages = RELEASE.expected_packages(TEST_VERSION)
        self.assertEqual(len(bundles), BUNDLE_COUNT)
        self.assertEqual(len(packages), PACKAGE_COUNT)
        for package in packages:
            self.assertIn(f"{package}.sigstore.json", bundles)

    def test_expected_assets_returns_twelve_names(self) -> None:
        """expected_assets returns the union of six packages and six bundles."""
        assets = RELEASE.expected_assets(TEST_VERSION)
        expected = RELEASE.expected_packages(TEST_VERSION) | RELEASE.expected_bundles(TEST_VERSION)
        self.assertEqual(assets, expected)

    def test_asset_problems_with_bundles_and_packages_passes(self) -> None:
        """A folder holding the six packages and six bundles has zero asset problems."""
        with tempfile.TemporaryDirectory() as folder:
            dir_path = Path(folder)
            for name in RELEASE.expected_assets(TEST_VERSION):
                (dir_path / name).write_text("test", encoding="utf-8")
            problems = RELEASE.asset_problems(dir_path, TEST_VERSION, require_bundles=True)
            self.assertEqual(problems, [])

    def test_require_bundles_flag_reports_missing_bundles(self) -> None:
        """check-assets --require-bundles reports missing bundles even when packages are present."""
        with tempfile.TemporaryDirectory() as folder:
            dir_path = Path(folder)
            for name in RELEASE.expected_packages(TEST_VERSION):
                (dir_path / name).write_text("test", encoding="utf-8")
            problems = RELEASE.asset_problems(dir_path, TEST_VERSION, require_bundles=True)
            self.assertEqual(len(problems), BUNDLE_COUNT)
            for problem in problems:
                self.assertIn("Bundle", problem)
                self.assertIn(".sigstore.json", problem)

    def test_checksums_writes_all_twelve_assets_sorted(self) -> None:
        """write_checksums hashes all 12 assets and excludes SHA256SUMS.txt."""
        with tempfile.TemporaryDirectory() as folder:
            dir_path = Path(folder)
            for name in RELEASE.expected_assets(TEST_VERSION):
                (dir_path / name).write_text(f"content-{name}", encoding="utf-8")
            checksum_path = RELEASE.write_checksums(dir_path)
            lines = checksum_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), RELEASE_ASSET_COUNT)
            recorded_names = [line.split("  ")[1] for line in lines]
            self.assertEqual(recorded_names, sorted(recorded_names))
            self.assertEqual(set(recorded_names), RELEASE.expected_assets(TEST_VERSION))


class AttestationReadmeTest(unittest.TestCase):
    """README.md documents gh attestation verify instructions."""

    def test_readme_contains_attestation_verification_command(self) -> None:
        """README.md contains the gh attestation verify command."""
        content = README_PATH.read_text(encoding="utf-8")
        self.assertIn("gh attestation verify <package-file> --repo abuzucom/infiniteprep", content)
        self.assertIn("--bundle <package-file>.sigstore.json", content)


if __name__ == "__main__":
    unittest.main()
