#!/usr/bin/env python3
"""Check push operand parsing and ref store failures in the tag gate."""
import os
import tempfile
import unittest
from pathlib import Path

from tests.test_tag_push_resolution import _gate_core, build_repository, decide

OVERSIZED_PACKED_REFS_BYTES = 4 * 1024 * 1024 + 1


class PushOperandParsingTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        build_repository(self.root)

    def test_option_values_not_refspecs(self):
        for command in ("git push -o production origin feat/x",
                        "git push --push-option production origin feat/x",
                        "git push --push-option=production origin feat/x"):
            with self.subTest(command=command):
                self.assertEqual(decide(command, self.root), ("", ""))

    def test_repo_option_operands(self):
        self.assertEqual(decide("git push --repo origin production", self.root)[0], "deny")
        self.assertEqual(decide("git push --repo=origin feat/x", self.root), ("", ""))


class RefStoreFailureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git_dir = build_repository(self.root)

    def test_oversized_packed_refs_asks(self):
        (self.git_dir / "packed-refs").write_bytes(b"#" * OVERSIZED_PACKED_REFS_BYTES)
        self.assertEqual(decide("git push origin feat/x", self.root)[0], "ask")

    def test_non_utf8_packed_refs_asks(self):
        (self.git_dir / "packed-refs").write_bytes(b"\xff\xfe refs/tags/x\n")
        self.assertEqual(decide("git push origin feat/x", self.root)[0], "ask")

    def test_unreadable_packed_refs_asks(self):
        (self.git_dir / "packed-refs").unlink()
        (self.git_dir / "packed-refs").mkdir()
        self.assertEqual(decide("git push origin feat/x", self.root)[0], "ask")

    def test_missing_packed_refs(self):
        (self.git_dir / "packed-refs").unlink()
        self.assertEqual(decide("git push origin production", self.root)[0], "deny")
        self.assertEqual(decide("git push origin feat/x", self.root), ("", ""))

    def test_unresolved_repository_location_asks(self):
        verdict = _gate_core.git_verdict(
            ["push", "origin", "feat/x"], str(self.root), [("GIT_DIR", "")])
        self.assertEqual(verdict[0], "ask")

    @unittest.skipIf(os.name == "nt", "chmod cannot remove directory read access on Windows")
    def test_unreadable_repository_entry_asks(self):
        outside = self.root / "outside"
        nested = outside / "work"
        nested.mkdir(parents=True)
        os.chmod(outside, 0)
        self.addCleanup(os.chmod, outside, 0o700)
        if os.access(nested, os.R_OK):
            self.skipTest("the process can read any directory, as root can")
        verdict = _gate_core.git_verdict(["push", "origin", "feat/x"], str(nested))
        self.assertEqual(verdict[0], "ask")


if __name__ == "__main__":
    unittest.main()
