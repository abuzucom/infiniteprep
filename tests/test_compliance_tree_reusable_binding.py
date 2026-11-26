#!/usr/bin/env python3
"""Test reviewed workflow binding of job-level reusable workflows and inputs."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

CALLER_PATH = ".github/workflows/persona-review-pr.yml"
LOCAL_REUSABLE = "./.github/workflows/persona-review.yml"
# A well-formed sha256 that the structural workflow checks never compare.
PLACEHOLDER_DIGEST = "0" * 64
REMOTE_REUSABLE = (
    "evil/repo/.github/workflows/steal.yml@0123456789abcdef0123456789abcdef01234567")
CALLER = f"""\
name: persona-review-pr
"on":
  pull_request_target:
    types: [labeled]
permissions:
  contents: read
jobs:
  persona:
    permissions:
      contents: read
    uses: {LOCAL_REUSABLE}
    with:
      personas_ref: ${{{{ github.sha }}}}
    secrets:
      MODEL_API_KEY: ${{{{ secrets.OLLAMA_API_KEY }}}}
"""


def build_exemptions(entry: dict) -> object:
    """Parse a manifest that reviews only CALLER_PATH with one entry."""
    entry = {"sha256": PLACEHOLDER_DIGEST, **entry}
    document = {"version": 1, "pull_request_target": {CALLER_PATH: entry}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / check_compliance_tree.EXEMPTIONS_FILE).write_text(
            json.dumps(document), encoding="ascii")
        return check_compliance_tree.load_exemptions(root)


class ReusableBindingTest(unittest.TestCase):
    """A reviewed caller may call only the reusable workflows the manifest binds."""

    persist = check_compliance_tree._load_trusted("check_persist_credentials")

    def setUp(self) -> None:
        self.exemptions = build_exemptions(
            {"secrets": ["OLLAMA_API_KEY"], "reusable_workflows": {LOCAL_REUSABLE: PLACEHOLDER_DIGEST}})

    def scan(self, text: str, exemptions=None) -> list[str]:
        """Return workflow violations for the caller path."""
        return check_compliance_tree._workflow_violations(
            text, CALLER_PATH, self.persist, exemptions or self.exemptions)

    def test_bound_local_reusable_passes(self) -> None:
        self.assertEqual(self.scan(CALLER), [])

    def test_unbound_reusable_fails(self) -> None:
        exemptions = build_exemptions({"secrets": ["OLLAMA_API_KEY"]})
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target calls unbound reusable "
            f"workflow {LOCAL_REUSABLE}", self.scan(CALLER, exemptions))

    def test_substituted_remote_reusable_fails(self) -> None:
        text = CALLER.replace(LOCAL_REUSABLE, REMOTE_REUSABLE)
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target calls unbound reusable "
            f"workflow {REMOTE_REUSABLE}", self.scan(text))

    def test_job_input_interpolation_fails(self) -> None:
        text = CALLER.replace(
            "${{ github.sha }}", "${{ github.event.pull_request.title }}")
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target interpolates event data "
            "in job inputs", self.scan(text))

    def test_non_text_job_uses_fails(self) -> None:
        text = CALLER.replace(f"uses: {LOCAL_REUSABLE}", "uses: [a, b]")
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target has a non-text job uses value",
            self.scan(text))


class ReusableManifestValidationTest(unittest.TestCase):
    """Bound reusable workflow identities must be exact and immutable."""

    def assert_rejected(self, reusable: object) -> None:
        """Require load_exemptions to refuse one reusable_workflows value."""
        with self.assertRaises(RuntimeError):
            build_exemptions({"secrets": [], "reusable_workflows": reusable})

    def test_tag_reference_rejected(self) -> None:
        self.assert_rejected(["evil/repo/.github/workflows/steal.yml@v1"])

    def test_non_list_rejected(self) -> None:
        self.assert_rejected(LOCAL_REUSABLE)

    def test_traversal_local_rejected(self) -> None:
        self.assert_rejected(["./.github/workflows/../../x.yml"])

    def test_remote_sha_accepted(self) -> None:
        exemptions = build_exemptions({"secrets": [], "reusable_workflows": [REMOTE_REUSABLE]})
        self.assertIn(REMOTE_REUSABLE, exemptions.pull_target[CALLER_PATH].reusable_workflows)

    def test_missing_secrets_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            build_exemptions({"reusable_workflows": [LOCAL_REUSABLE]})


if __name__ == "__main__":
    unittest.main()
