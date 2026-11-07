"""Behavioral checks for the manual package build workflow (D83a0)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github/workflows/build-packages.yml"
FULL_SHA_REFERENCE = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
PINNED_IMAGES = ["windows-2025", "macos-14", "ubuntu-22.04"]
RETENTION_DAYS = 3


class BuildPackagesWorkflowTest(unittest.TestCase):
    """The workflow builds on pinned images, smoke-checks, and keeps uploads briefly."""

    workflow: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once for every check."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def _steps(self) -> list[dict[str, Any]]:
        """Return every step of the bundle job."""
        return self.workflow["jobs"]["bundle"]["steps"]

    def test_runs_only_when_started_by_hand(self) -> None:
        """The only trigger is workflow_dispatch until the release stage adds tags (D248)."""
        self.assertEqual(list(self.workflow["on"]), ["workflow_dispatch"])

    def test_token_is_read_only(self) -> None:
        """The workflow token can read the repository and nothing else."""
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("permissions", self.workflow["jobs"]["bundle"])

    def test_every_checkout_drops_credentials(self) -> None:
        """Each checkout sets persist-credentials to false (Rule 11)."""
        checkouts = [
            step for step in self._steps() if step.get("uses", "").startswith("actions/checkout@")
        ]
        self.assertTrue(checkouts)
        for step in checkouts:
            self.assertIs(step["with"]["persist-credentials"], False)

    def test_every_action_is_pinned_to_a_full_sha(self) -> None:
        """Every external action names a 40-character commit SHA."""
        references = [step["uses"] for step in self._steps() if "uses" in step]
        self.assertTrue(references)
        for reference in references:
            self.assertRegex(reference, FULL_SHA_REFERENCE)

    def test_matrix_uses_the_pinned_images(self) -> None:
        """The bundle job runs on the three pinned runner images (D251)."""
        job = self.workflow["jobs"]["bundle"]
        self.assertEqual(job["strategy"]["matrix"]["os"], PINNED_IMAGES)
        self.assertIs(job["strategy"]["fail-fast"], False)
        self.assertEqual(job["runs-on"], "${{ matrix.os }}")

    def test_build_runs_the_spec_then_the_smoke_check(self) -> None:
        """The spec build comes before the built app's smoke check."""
        runs = [step.get("run", "") for step in self._steps()]
        build_index = next(i for i, run in enumerate(runs) if "pyinstaller" in run)
        smoke_index = next(i for i, run in enumerate(runs) if "--smoke-check" in run)
        self.assertIn("infiniteprep.spec", runs[build_index])
        self.assertLess(build_index, smoke_index)

    def test_upload_keeps_bundles_for_three_days(self) -> None:
        """Every upload expires after 3 days and fails when its files are missing (D255).

        The bundle upload of dist/InfinitePrep still runs on every OS (D271).
        """
        uploads = [
            step
            for step in self._steps()
            if step.get("uses", "").startswith("actions/upload-artifact@")
        ]
        self.assertTrue(uploads)
        for upload in uploads:
            self.assertEqual(upload["with"]["retention-days"], RETENTION_DAYS)
            self.assertEqual(upload["with"]["if-no-files-found"], "error")
        bundle_uploads = [u for u in uploads if u["with"]["path"] == "dist/InfinitePrep"]
        self.assertEqual(len(bundle_uploads), 1)
        self.assertNotIn("if", bundle_uploads[0])

    def test_installs_no_unpinned_pip_upgrade(self) -> None:
        """The install step uses the locked requirements and never upgrades pip unpinned."""
        runs = "\n".join(step.get("run", "") for step in self._steps())
        self.assertIn("requirements-lock.txt", runs)
        self.assertNotIn("--upgrade pip", runs)

    def test_pip_check_runs_before_the_build(self) -> None:
        """pip check confirms the installed requirements before PyInstaller runs."""
        runs = [step.get("run", "") for step in self._steps()]
        check_index = runs.index("python -m pip check")
        build_index = next(i for i, run in enumerate(runs) if "pyinstaller" in run)
        self.assertLess(check_index, build_index)


if __name__ == "__main__":
    unittest.main()
