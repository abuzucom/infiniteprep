"""Behavioral checks for the tag release jobs in build-packages (D83a4b, D314 to D318)."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github/workflows/build-packages.yml"
# A manual run started on a tag also has ref_type tag. The event must also be a push.
TAG_CONDITION = "github.event_name == 'push' && github.ref_type == 'tag'"
PACKAGE_ARTIFACTS = (
    "InfinitePrep-windows-packages",
    "InfinitePrep-linux-packages",
    "InfinitePrep-macos-dmg",
)
FIRST_PARTY_ACTIONS = ("actions/checkout@", "actions/setup-python@")


class ReleaseWorkflowTest(unittest.TestCase):
    """Tags check first, build next, and only the release job writes."""

    jobs: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.jobs = workflow["jobs"]

    def _step(self, job: str, name: str) -> dict[str, Any]:
        """Return the one step with this name in the job."""
        [step] = [step for step in self.jobs[job]["steps"] if step.get("name") == name]
        return step

    def test_check_tag_runs_on_tags_with_full_history(self) -> None:
        """check-tag runs only for tags, reads all history, and keeps no credentials."""
        job = self.jobs["check-tag"]
        self.assertEqual(job["if"], TAG_CONDITION)
        checkout = job["steps"][0]["with"]
        self.assertEqual(checkout["fetch-depth"], 0)
        self.assertIs(checkout["persist-credentials"], False)
        self.assertIn(
            'release.py check-tag "$GITHUB_REF_NAME"',
            self._step("check-tag", "Check tag against version files")["run"],
        )
        self.assertIn(
            'git merge-base --is-ancestor "$GITHUB_SHA" origin/main',
            self._step("check-tag", "Check tagged commit is on main")["run"],
        )
        self.assertNotIn("permissions", job)

    def test_bundle_waits_for_the_tag_check_but_runs_by_hand(self) -> None:
        """bundle needs check-tag, runs when it is skipped, and stops when it fails."""
        bundle = self.jobs["bundle"]
        self.assertEqual(bundle["needs"], "check-tag")
        self.assertIn("needs.check-tag.result == 'success'", bundle["if"])
        self.assertIn("needs.check-tag.result == 'skipped'", bundle["if"])
        self.assertIn("!cancelled()", bundle["if"])
        self.assertNotIn("permissions", bundle)

    def test_only_the_release_job_can_write(self) -> None:
        """The release job alone holds contents: write, after every bundle job."""
        release = self.jobs["release"]
        self.assertEqual(release["needs"], "bundle")
        self.assertEqual(release["if"], TAG_CONDITION)
        self.assertEqual(release["permissions"], {"contents": "write", "actions": "read"})
        for name, job in self.jobs.items():
            if name != "release":
                self.assertNotIn("permissions", job, name)

    def test_release_job_uses_no_third_party_action(self) -> None:
        """Every action in the release job is a first-party checkout or setup."""
        uses = [step["uses"] for step in self.jobs["release"]["steps"] if "uses" in step]
        self.assertTrue(uses)
        for reference in uses:
            self.assertTrue(reference.startswith(FIRST_PARTY_ACTIONS), reference)
        for step in self.jobs["release"]["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                self.assertIs(step["with"]["persist-credentials"], False)

    def test_release_gathers_all_six_packages_and_writes_checksums(self) -> None:
        """All three package artifacts download, then the checksums and notes are written."""
        download = self._step("release", "Download package artifacts")["run"]
        for artifact in PACKAGE_ARTIFACTS:
            self.assertIn(artifact, download)
        self.assertIn("-ne 6 ]", download)
        written = self._step("release", "Write checksums and release notes")["run"]
        self.assertIn("release.py checksums", written)
        self.assertIn('release.py notes "${GITHUB_REF_NAME#v}"', written)

    def test_upload_fills_drafts_and_checks_existing_hashes(self) -> None:
        """The upload creates a draft, refuses a published release, and compares hashes."""
        run = self._step("release", "Upload to draft release")["run"]
        for fragment in (
            "--draft",
            "--verify-tag",
            "--generate-notes",
            "--notes-file",
            "is already published",
            "sha256sum",
            "gh release upload",
        ):
            self.assertIn(fragment, run)
        self.assertIn("which this build does not make", run)
        self.assertLess(run.index("which this build does not make"), run.index("gh release upload"))
        self.assertNotIn("--clobber", run)
        self.assertNotIn("gh release edit", run)
        self.assertLess(run.index("sha256sum"), run.index("gh release upload"))


if __name__ == "__main__":
    unittest.main()
