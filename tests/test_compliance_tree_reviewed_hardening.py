#!/usr/bin/env python3
"""Test indirect event data and job permission limits on reviewed workflows."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

NOTICE_PATH = ".github/workflows/stop-requested-notice.yml"
SCRIPT_ACTION = "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3"
NOTICE = f"""\
name: stop-requested-notice
"on":
  pull_request_target:
    types: [labeled]
permissions: {{}}
jobs:
  notice:
    if: github.event.label.name == 'stop-requested'
    concurrency:
      group: notice-${{{{ github.event.pull_request.number }}}}
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - name: Post the stop notice
        uses: {SCRIPT_ACTION}
        with:
          script: |
            await github.rest.issues.createComment({{body: "paused"}});
"""
# A well-formed sha256 that the structural workflow checks never compare.
PLACEHOLDER_DIGEST = "0" * 64
INDIRECT_MESSAGE = (
    f"{NOTICE_PATH}: reviewed pull_request_target interpolates event data "
    "outside if and concurrency")


def build_exemptions(entry: dict) -> object:
    """Parse a manifest that reviews only NOTICE_PATH with one entry."""
    entry = {"sha256": PLACEHOLDER_DIGEST, **entry}
    document = {"version": 1, "pull_request_target": {NOTICE_PATH: entry}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / check_compliance_tree.EXEMPTIONS_FILE).write_text(
            json.dumps(document), encoding="ascii")
        return check_compliance_tree.load_exemptions(root)


class ReviewedScan:
    """Scan NOTICE_PATH under a manifest that allows pull-requests: write."""

    persist = check_compliance_tree._load_trusted("check_persist_credentials")

    def setUp(self) -> None:
        self.exemptions = build_exemptions(
            {"secrets": [], "permissions": {"pull-requests": "write"}})

    def scan(self, text: str, exemptions=None) -> list[str]:
        """Return workflow violations for the notice path."""
        return check_compliance_tree._workflow_violations(
            text, NOTICE_PATH, self.persist, exemptions or self.exemptions)


class IndirectEventDataTest(ReviewedScan, unittest.TestCase):
    """Event data stays inside if conditions and concurrency groups."""

    def test_if_and_concurrency_pass(self) -> None:
        self.assertEqual(self.scan(NOTICE), [])

    def test_workflow_env_event_fails(self) -> None:
        text = NOTICE.replace(
            "jobs:\n", "env:\n  PAYLOAD: ${{ github.event.pull_request.title }}\njobs:\n")
        self.assertIn(INDIRECT_MESSAGE, self.scan(text))

    def test_job_env_event_fails(self) -> None:
        text = NOTICE.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    env:\n      HEAD: ${{ github.head_ref }}\n")
        self.assertIn(INDIRECT_MESSAGE, self.scan(text))

    def test_matrix_event_fails(self) -> None:
        text = NOTICE.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
            "        title: ['${{ github.event.pull_request.title }}']\n")
        self.assertIn(INDIRECT_MESSAGE, self.scan(text))

    def test_whole_context_dump_fails(self) -> None:
        text = NOTICE.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    env:\n      CONTEXT: ${{ toJSON(github) }}\n")
        self.assertIn(INDIRECT_MESSAGE, self.scan(text))


class JobPermissionTest(ReviewedScan, unittest.TestCase):
    """Job permissions stay within the write scopes the manifest lists."""

    def test_unlisted_write_scope_fails(self) -> None:
        text = NOTICE.replace("      pull-requests: write\n",
                              "      pull-requests: write\n      contents: write\n")
        self.assertIn(
            f"{NOTICE_PATH}: reviewed pull_request_target job notice grants unlisted "
            "write scope contents", self.scan(text))

    def test_write_all_fails(self) -> None:
        text = NOTICE.replace("    permissions:\n      pull-requests: write\n",
                              "    permissions: write-all\n")
        self.assertIn(
            f"{NOTICE_PATH}: reviewed pull_request_target job notice permissions are "
            "not a scope mapping", self.scan(text))

    def test_unlisted_manifest_scope_fails(self) -> None:
        exemptions = build_exemptions({"secrets": []})
        self.assertIn(
            f"{NOTICE_PATH}: reviewed pull_request_target job notice grants unlisted "
            "write scope pull-requests", self.scan(NOTICE, exemptions))

    def test_read_scope_passes(self) -> None:
        text = NOTICE.replace("      pull-requests: write\n",
                              "      pull-requests: write\n      contents: read\n")
        self.assertEqual(self.scan(text), [])

    def test_invalid_level_fails(self) -> None:
        text = NOTICE.replace("      pull-requests: write\n", "      pull-requests: admin\n")
        self.assertIn(
            f"{NOTICE_PATH}: reviewed pull_request_target job notice sets an invalid "
            "level for pull-requests", self.scan(text))


class PermissionManifestValidationTest(unittest.TestCase):
    """Manifest permission maps name known scopes with write access only."""

    def assert_rejected(self, permissions: object) -> None:
        """Require load_exemptions to refuse one permissions value."""
        with self.assertRaises(RuntimeError):
            build_exemptions({"secrets": [], "permissions": permissions})

    def test_unknown_scope_rejected(self) -> None:
        self.assert_rejected({"everything": "write"})

    def test_read_level_rejected(self) -> None:
        self.assert_rejected({"contents": "read"})

    def test_string_permissions_rejected(self) -> None:
        self.assert_rejected("write-all")


if __name__ == "__main__":
    unittest.main()
