#!/usr/bin/env python3
"""Test workflow action pin validation."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_action_pins

COMMIT_SHA_HEX_LENGTH = 40
SHA256_DIGEST_HEX_LENGTH = 64


class ActionPinTest(unittest.TestCase):
    """External actions require immutable revisions."""

    def test_full_sha_passes(self):
        text = "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@" + "a" * COMMIT_SHA_HEX_LENGTH
        self.assertEqual(check_action_pins.find_violations(text, "x.yml"), [])

    def test_tag_fails(self):
        found = check_action_pins.find_violations(
            "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n", "x.yml")
        self.assertEqual(len(found), 1)

    def test_local_reference_passes(self):
        text = "jobs:\n  test:\n    uses: ./.github/workflows/reuse.yml\n"
        self.assertEqual(check_action_pins.find_violations(text, "x.yml"), [])

    def test_container_digest_passes(self):
        text = "jobs:\n  test:\n    steps:\n      - uses: docker://alpine@sha256:" + "a" * SHA256_DIGEST_HEX_LENGTH
        self.assertEqual(check_action_pins.find_violations(text, "x.yml"), [])

    def test_container_tag_fails(self):
        text = "jobs:\n  test:\n    steps:\n      - uses: docker://alpine:latest\n"
        found = check_action_pins.find_violations(text, "x.yml")
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
