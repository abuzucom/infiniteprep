#!/usr/bin/env python3
"""Test that shared skills and policy files name no specific organization."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_shared_neutrality

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class NeutralityRuleTest(unittest.TestCase):
    """Token classification over one text."""

    def test_owner_code_span_flagged(self):
        findings = check_shared_neutrality.owner_references("See `exampleorg/widgets`.\n")
        self.assertEqual(findings, [(1, "exampleorg/widgets")])

    def test_plain_text_owner_flagged(self):
        for text in ("Clone acme/widgets first.\n", "See [docs](acme/widgets).\n",
                     "- acme/widgets\n"):
            with self.subTest(text=text):
                findings = check_shared_neutrality.owner_references(text)
                self.assertEqual([reference for _line, reference in findings], ["acme/widgets"])

    def test_nested_paths_not_flagged(self):
        text = "Edit skills/pr-steward/SKILL.md and use MD5/SHA-1 for cache keys.\n"
        self.assertEqual(check_shared_neutrality.owner_references(text), [])

    def test_github_url_flagged(self):
        findings = check_shared_neutrality.owner_references("https://github.com/exampleorg/x\n")
        self.assertEqual(findings, [(1, "github.com/exampleorg/")])

    def test_repository_paths_allowed(self):
        text = "Run `scripts/sync.py` and `hooks/_gate_core.py` on `fix/branch-name`.\n"
        self.assertEqual(check_shared_neutrality.owner_references(text), [])

    def test_placeholders_and_actions_allowed(self):
        text = "Use `<owner>/<repo>` and `actions/checkout` and `OWNER/REPOSITORY`.\n"
        self.assertEqual(check_shared_neutrality.owner_references(text), [])

    def test_unmatched_markers_reported(self):
        start = "<!-- source orientation:start -->\n"
        end = "<!-- source orientation:end -->\n"
        for text in (start + "Clone `exampleorg/widgets`.\n", end + "x\n",
                     start + start + "x\n" + end):
            with self.subTest(text=text):
                problems = check_shared_neutrality.marker_problems(text)
                self.assertTrue(problems)

    def test_unmatched_start_still_scanned(self):
        text = "<!-- source orientation:start -->\nClone `exampleorg/widgets`.\n"
        self.assertTrue(check_shared_neutrality.owner_references(text))

    def test_source_orientation_block_skipped(self):
        text = ("<!-- source orientation:start -->\n`exampleorg/widgets`\n"
                "<!-- source orientation:end -->\n")
        self.assertEqual(check_shared_neutrality.owner_references(text), [])


class NeutralityTreeTest(unittest.TestCase):
    """The checker scans the shared files of one root."""

    def test_live_repository_passes(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = check_shared_neutrality.main(["--root", str(REPOSITORY_ROOT)])
        self.assertEqual(code, 0, stderr.getvalue())

    def test_named_owner_in_skill_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skills" / "pr-steward" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("Clone `exampleorg/widgets` first.\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = check_shared_neutrality.main(["--root", str(root)])
        self.assertEqual(code, 1)
        self.assertIn("skills/pr-steward/SKILL.md:1", stderr.getvalue())

    def test_oversized_shared_file_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skills" / "pr-steward" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            padding = "x" * check_shared_neutrality.MAX_FILE_BYTES
            skill.write_text(f"{padding}\nClone `exampleorg/widgets`.\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = check_shared_neutrality.main(["--root", str(root)])
        self.assertEqual(code, 1)
        self.assertIn("size", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
