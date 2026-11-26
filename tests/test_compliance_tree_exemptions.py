#!/usr/bin/env python3
"""Test the base-branch exemption manifest in check_compliance_tree.py.

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

REPO_ROOT = Path(__file__).resolve().parent.parent
CALLER_PATH = ".github/workflows/persona-review-pr.yml"
NOTICE_PATH = ".github/workflows/stop-requested-notice.yml"
FIXTURE_PATH = "reviewers/foucault/eval/cases/file/weak-hash/input.py"
WEAK_HASH_SOURCE = "import hashlib\n\ndigest = hashlib.md5(b'x').hexdigest()\n"
MALFORMED_SOURCE = "def partial(:\n"
# A well-formed sha256 that the structural workflow checks never compare.
PLACEHOLDER_DIGEST = "0" * 64
CALLER = """\
name: persona-review-pr
"on":
  pull_request_target:
    types: [labeled]
permissions:
  contents: read
jobs:
  persona:
    if: github.event.label.name == 'persona-scan-requested'
    permissions:
      contents: read
      pull-requests: write
    uses: ./.github/workflows/persona-review.yml
    with:
      personas_ref: ${{ github.sha }}
    secrets:
      MODEL_API_KEY: ${{ secrets.OLLAMA_API_KEY }}
"""
NOTICE = """\
name: stop-requested-notice
"on":
  pull_request_target:
    types: [labeled]
permissions: {}
jobs:
  notice:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - name: Post the stop notice
        uses: actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3
        with:
          script: |
            await github.rest.issues.createComment({body: "paused"});
"""
CHECKOUT_STEPS = """\
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false
"""


def build_exemptions(document: dict) -> object:
    """Parse one manifest document through a temporary trusted root."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest = root / check_compliance_tree.EXEMPTIONS_FILE
        manifest.write_text(json.dumps(document), encoding="ascii")
        return check_compliance_tree.load_exemptions(root)


def reviewed_manifest(secrets: list[str]) -> dict:
    """Return a manifest that reviews the caller and the notice workflow."""
    return {
        "version": 1,
        "pull_request_target": {
            CALLER_PATH: {
                "secrets": secrets,
                "sha256": PLACEHOLDER_DIGEST,
                "reusable_workflows": {"./.github/workflows/persona-review.yml": PLACEHOLDER_DIGEST},
                "permissions": {"pull-requests": "write"},
            },
            NOTICE_PATH: {"secrets": [], "sha256": PLACEHOLDER_DIGEST,
                          "permissions": {"pull-requests": "write"}},
        },
        "rule7_excluded_trees": ["reviewers/*/eval/cases/"],
    }


class WorkflowScan:
    """Run the workflow checks with the trusted persist-credentials module."""

    persist = check_compliance_tree._load_trusted("check_persist_credentials")

    def scan(self, text: str, path: str, exemptions=None) -> list[str]:
        """Return workflow violations for one workflow text."""
        return check_compliance_tree._workflow_violations(
            text, path, self.persist, exemptions)


class UnlistedWorkflowTest(WorkflowScan, unittest.TestCase):
    """Workflows absent from the manifest keep exact template equality."""

    def test_unlisted_caller_still_fails(self) -> None:
        findings = self.scan(CALLER, CALLER_PATH)
        self.assertIn(f"{CALLER_PATH}: pull_request_target references secrets", findings)
        self.assertIn(f"{CALLER_PATH}: pull_request_target job schema is not trusted", findings)

    def test_other_path_not_exempted(self) -> None:
        exemptions = build_exemptions(reviewed_manifest(["OLLAMA_API_KEY"]))
        findings = self.scan(CALLER, ".github/workflows/other-review-pr.yml", exemptions)
        self.assertTrue(any("references secrets" in item for item in findings))

    def test_missing_manifest_exempts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exemptions = check_compliance_tree.load_exemptions(Path(directory))
        self.assertTrue(self.scan(CALLER, CALLER_PATH, exemptions))


class ReviewedWorkflowTest(WorkflowScan, unittest.TestCase):
    """Listed workflows pass only the documented reviewed rule set."""

    def setUp(self) -> None:
        self.exemptions = build_exemptions(reviewed_manifest(["OLLAMA_API_KEY"]))

    def test_listed_caller_passes(self) -> None:
        self.assertEqual(self.scan(CALLER, CALLER_PATH, self.exemptions), [])

    def test_listed_notice_passes(self) -> None:
        self.assertEqual(self.scan(NOTICE, NOTICE_PATH, self.exemptions), [])

    def test_unlisted_secret_fails(self) -> None:
        text = CALLER.replace("secrets.OLLAMA_API_KEY", "secrets.DEPLOY_KEY")
        findings = self.scan(text, CALLER_PATH, self.exemptions)
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target references unlisted secret DEPLOY_KEY",
            findings)

    def test_inherited_secrets_fail(self) -> None:
        text = CALLER.replace(
            "    secrets:\n      MODEL_API_KEY: ${{ secrets.OLLAMA_API_KEY }}\n",
            "    secrets: inherit\n")
        findings = self.scan(text, CALLER_PATH, self.exemptions)
        self.assertIn(f"{CALLER_PATH}: reviewed pull_request_target inherits secrets", findings)

    def test_indexed_secret_fails(self) -> None:
        text = CALLER.replace("secrets.OLLAMA_API_KEY", "secrets['OLLAMA_API_KEY']")
        findings = self.scan(text, CALLER_PATH, self.exemptions)
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target indexes secrets", findings)


class ReviewedWorkflowLimitsTest(WorkflowScan, unittest.TestCase):
    """The reviewed rule set rejects head checkout and write defaults."""

    def setUp(self) -> None:
        self.exemptions = build_exemptions(reviewed_manifest([]))

    def test_checkout_step_fails(self) -> None:
        text = NOTICE.replace("    steps:\n", CHECKOUT_STEPS, 1)
        findings = self.scan(text, NOTICE_PATH, self.exemptions)
        self.assertIn(f"{NOTICE_PATH}: reviewed pull_request_target checks out code", findings)

    def test_write_top_permissions_fail(self) -> None:
        text = NOTICE.replace("permissions: {}\n", "permissions: write-all\n")
        findings = self.scan(text, NOTICE_PATH, self.exemptions)
        self.assertIn(
            f"{NOTICE_PATH}: reviewed pull_request_target top-level permissions are not "
            "empty or contents: read", findings)

    def test_event_interpolation_fails(self) -> None:
        text = NOTICE.replace(
            'body: "paused"', 'body: "${{ github.event.pull_request.title }}"')
        findings = self.scan(text, NOTICE_PATH, self.exemptions)
        self.assertIn(
            f"{NOTICE_PATH}: reviewed pull_request_target interpolates event data in a step",
            findings)

    def test_unpinned_action_still_fails(self) -> None:
        text = NOTICE.replace(
            "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3",
            "actions/github-script@v9")
        self.assertTrue(self.scan(text, NOTICE_PATH, self.exemptions))


class RuleSevenTreeTest(unittest.TestCase):
    """Excluded fixture trees skip only the Rule 7 weak-hash scan."""

    checkers = check_compliance_tree._load_checkers()

    def setUp(self) -> None:
        self.exemptions = build_exemptions(reviewed_manifest([]))

    def scan(self, path: str, text: str, exemptions=None) -> list[str]:
        """Return blob violations for one regular file."""
        return check_compliance_tree._scan_blob(
            path, text, "100644", self.checkers, exemptions)

    def test_fixture_weak_hash_excluded(self) -> None:
        self.assertTrue(self.scan(FIXTURE_PATH, WEAK_HASH_SOURCE))
        self.assertEqual(self.scan(FIXTURE_PATH, WEAK_HASH_SOURCE, self.exemptions), [])

    def test_fixture_malformed_python_excluded(self) -> None:
        self.assertEqual(self.scan(FIXTURE_PATH, MALFORMED_SOURCE, self.exemptions), [])

    def test_sibling_tree_not_excluded(self) -> None:
        path = "reviewers/foucault/eval/input.py"
        self.assertTrue(self.scan(path, WEAK_HASH_SOURCE, self.exemptions))

    def test_tree_root_file_not_excluded(self) -> None:
        path = "reviewers/foucault/eval/cases"
        self.assertTrue(self.scan(path + ".py", WEAK_HASH_SOURCE, self.exemptions))


class ManifestValidationTest(unittest.TestCase):
    """A malformed manifest fails closed before any scan."""

    def assert_rejected(self, document: dict) -> None:
        """Require load_exemptions to raise RuntimeError."""
        with self.assertRaises(RuntimeError):
            build_exemptions(document)

    def test_unknown_key_rejected(self) -> None:
        self.assert_rejected({"version": 1, "extra": []})

    def test_wrong_version_rejected(self) -> None:
        self.assert_rejected({"version": 2})

    def test_wildcard_first_segment_rejected(self) -> None:
        self.assert_rejected({"version": 1, "rule7_excluded_trees": ["*/"]})

    def test_protected_tree_rejected(self) -> None:
        self.assert_rejected({"version": 1, "rule7_excluded_trees": ["scripts/"]})

    def test_traversal_tree_rejected(self) -> None:
        self.assert_rejected({"version": 1, "rule7_excluded_trees": ["reviewers/../scripts/"]})

    def test_non_workflow_path_rejected(self) -> None:
        self.assert_rejected(
            {"version": 1, "pull_request_target": {"scripts/x.yml": {"secrets": []}}})

    def test_bad_secret_name_rejected(self) -> None:
        self.assert_rejected(
            {"version": 1, "pull_request_target": {CALLER_PATH: {"secrets": ["a b"]}}})


class ManifestSourceTest(unittest.TestCase):
    """The manifest comes from the trusted checker root only."""

    def test_trusted_root_is_checker_parent(self) -> None:
        self.assertEqual(check_compliance_tree._trusted_root(), REPO_ROOT)

    def test_symlink_manifest_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.json"
            real.write_text(json.dumps({"version": 1}), encoding="ascii")
            os.symlink(real, root / check_compliance_tree.EXEMPTIONS_FILE)
            with self.assertRaises(RuntimeError):
                check_compliance_tree.load_exemptions(root)

    def test_non_ascii_manifest_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / check_compliance_tree.EXEMPTIONS_FILE
            manifest.write_bytes('{"version": 1, "x": "é"}'.encode("utf-8"))
            with self.assertRaises(RuntimeError):
                check_compliance_tree.load_exemptions(root)


if __name__ == "__main__":
    unittest.main()
