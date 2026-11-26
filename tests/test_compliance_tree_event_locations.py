#!/usr/bin/env python3
"""Test that only structural if and concurrency fields may carry event data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

NOTICE_PATH = ".github/workflows/stop-requested-notice.yml"
# A well-formed sha256 that the structural workflow checks never compare.
PLACEHOLDER_DIGEST = "0" * 64
EVENT = "${{ github.event.pull_request.title }}"
NOTICE = f"""\
name: stop-requested-notice
"on":
  pull_request_target:
    types: [labeled]
permissions: {{}}
concurrency:
  group: notice-${{{{ github.event.pull_request.number }}}}
jobs:
  notice:
    if: github.event.label.name == 'stop-requested'
    concurrency:
      group: job-${{{{ github.event.pull_request.number }}}}
    runs-on: ubuntu-latest
    steps:
      - if: github.event.action == 'labeled'
        run: echo paused
"""
OUTSIDE_MESSAGE = (
    f"{NOTICE_PATH}: reviewed pull_request_target interpolates event data "
    "outside if and concurrency")


class EventLocationTest(unittest.TestCase):
    """Nested keys named if or concurrency gain no exemption."""

    persist = check_compliance_tree._load_trusted("check_persist_credentials")

    def setUp(self) -> None:
        document = {"version": 1, "pull_request_target": {
            NOTICE_PATH: {"secrets": [], "sha256": PLACEHOLDER_DIGEST}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / check_compliance_tree.EXEMPTIONS_FILE).write_text(
                json.dumps(document), encoding="ascii")
            self.exemptions = check_compliance_tree.load_exemptions(root)

    def scan(self, text: str) -> list[str]:
        """Return workflow violations for the notice path."""
        return check_compliance_tree._workflow_violations(
            text, NOTICE_PATH, self.persist, self.exemptions)

    def test_structural_locations_pass(self) -> None:
        self.assertEqual(self.scan(NOTICE), [])

    def test_workflow_env_if_fails(self) -> None:
        text = NOTICE.replace("jobs:\n", f"env:\n  if: '{EVENT}'\njobs:\n")
        self.assertIn(OUTSIDE_MESSAGE, self.scan(text))

    def test_job_env_concurrency_fails(self) -> None:
        text = NOTICE.replace(
            "    runs-on: ubuntu-latest\n",
            f"    runs-on: ubuntu-latest\n    env:\n      concurrency: '{EVENT}'\n")
        self.assertIn(OUTSIDE_MESSAGE, self.scan(text))

    def test_step_with_if_fails(self) -> None:
        text = NOTICE.replace(
            "        run: echo paused\n",
            f"        uses: ./.github/actions/local\n        with:\n          if: '{EVENT}'\n")
        self.assertIn(OUTSIDE_MESSAGE, self.scan(text))

    def test_step_concurrency_key_fails(self) -> None:
        text = NOTICE.replace(
            "        run: echo paused\n",
            f"        run: echo paused\n        concurrency: '{EVENT}'\n")
        self.assertIn(OUTSIDE_MESSAGE, self.scan(text))


if __name__ == "__main__":
    unittest.main()
