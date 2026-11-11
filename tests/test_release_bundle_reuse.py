"""Behavioral tests for the reuse of draft-release Sigstore bundles (D316, D328)."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build-packages.yml"
SAVE_STEP_NAME = "Save package attestation bundles"
STRICT_MODE = "set -euo pipefail"
VERIFY_COMMAND = "gh attestation verify"
DOWNLOAD_COMMAND = "gh release download"
REUSE_COPY = 'cp "$dl_dir/$bname" "$bundle"'


def _load_save_step_script() -> str:
    """Return the run script of the bundle-saving step in the release job."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    [step] = [step for step in steps if step.get("name") == SAVE_STEP_NAME]
    return step["run"]


class BundleReuseHardeningTest(unittest.TestCase):
    """The bundle-saving step fails closed and verifies every reused bundle."""

    script: str

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the step script once."""
        cls.script = _load_save_step_script()

    def test_script_starts_in_strict_shell_mode(self) -> None:
        """The first script line enables errexit, nounset, and pipefail."""
        self.assertEqual(self.script.splitlines()[0].strip(), STRICT_MODE)

    def test_script_tolerates_no_command_failure(self) -> None:
        """No command hides its failure behind an unconditional success."""
        self.assertNotIn("|| true", self.script)

    def test_reused_bundle_is_verified_before_the_copy(self) -> None:
        """A downloaded bundle passes gh attestation verify before it replaces the fresh one."""
        download = self.script.index(DOWNLOAD_COMMAND)
        verify = self.script.index(VERIFY_COMMAND)
        copy = self.script.index(REUSE_COPY)
        self.assertLess(download, verify)
        self.assertLess(verify, copy)

    def test_verification_binds_bundle_package_and_repository(self) -> None:
        """The verify command names the package, the downloaded bundle, and the repository."""
        [line] = [line for line in self.script.splitlines() if VERIFY_COMMAND in line]
        self.assertIn('"${bundle%.sigstore.json}"', line)
        self.assertIn('--bundle "$dl_dir/$bname"', line)
        self.assertIn('--repo "$repo"', line)

    def test_empty_download_stops_the_job(self) -> None:
        """An absent or empty downloaded bundle exits before verification."""
        guard = self.script.index('[ ! -s "$dl_dir/$bname" ]')
        self.assertLess(guard, self.script.index(VERIFY_COMMAND))

    def test_published_release_stops_the_job(self) -> None:
        """The step refuses to reuse assets of a published release."""
        self.assertIn('[ "$is_draft" != "true" ]', self.script)


if __name__ == "__main__":
    unittest.main()
