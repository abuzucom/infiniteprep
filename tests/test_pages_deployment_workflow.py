"""Behavioral checks for the GitHub Pages deployment workflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/deploy-pages.yml"
SITE_FOLDER = "website"
SITE_FILES = ("CNAME", "index.html", "robots.txt")
EXPECTED_PERMISSIONS = {
    "contents": "read",
    "pages": "write",
    "id-token": "write",
}
FULL_SHA_REFERENCE = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


class PagesDeploymentWorkflowTest(unittest.TestCase):
    """The workflow publishes the site folder and nothing else."""

    workflow: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once for every check."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def _steps(self) -> list[dict[str, Any]]:
        """Return every step of the deploy job."""
        return self.workflow["jobs"]["deploy"]["steps"]

    def test_runs_on_push_to_main_and_on_demand(self) -> None:
        """The workflow deploys after a push to the default branch and on demand."""
        triggers = self.workflow["on"]
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", triggers)

    def test_token_writes_the_site_and_nothing_else(self) -> None:
        """The workflow token writes the Pages site and proves the deployment identity."""
        self.assertEqual(self.workflow["permissions"], EXPECTED_PERMISSIONS)

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

    def test_uploads_the_site_folder_only(self) -> None:
        """The artifact holds the site folder, so the project documents stay unpublished."""
        uploads = [
            step
            for step in self._steps()
            if step.get("uses", "").startswith("actions/upload-pages-artifact@")
        ]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["with"]["path"], f"./{SITE_FOLDER}")

    def test_the_site_folder_holds_the_three_site_files(self) -> None:
        """The site folder holds the landing page, the crawler preferences, and the domain."""
        published = sorted(path.name for path in (REPOSITORY_ROOT / SITE_FOLDER).iterdir())
        self.assertEqual(published, list(SITE_FILES))

    def test_the_project_documents_stay_in_docs(self) -> None:
        """The documents stay under docs/, and no site file remains there."""
        self.assertTrue((REPOSITORY_ROOT / "docs/agent-policy").is_dir())
        for name in SITE_FILES:
            with self.subTest(name=name):
                self.assertFalse((REPOSITORY_ROOT / "docs" / name).exists())

    def test_the_deployment_reports_the_site_url(self) -> None:
        """The job binds the site URL to the deployment step output."""
        job = self.workflow["jobs"]["deploy"]
        self.assertEqual(job["environment"]["name"], "github-pages")
        self.assertEqual(job["environment"]["url"], "${{ steps.deployment.outputs.page_url }}")
        deployment = next(step for step in self._steps() if step.get("id") == "deployment")
        self.assertTrue(deployment["uses"].startswith("actions/deploy-pages@"))

    def test_one_deployment_runs_at_a_time(self) -> None:
        """The concurrency group keeps a running deployment and queues the rest."""
        self.assertEqual(
            self.workflow["concurrency"],
            {"group": "pages", "cancel-in-progress": False},
        )


if __name__ == "__main__":
    unittest.main()
